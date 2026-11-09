# -*- coding: utf-8 -*-
"""
app/services/consejos_service.py
Lógica de persistencia, emisión, respuesta, vencimiento y medición de consejos.
"""
from __future__ import annotations

import logging
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.consejo import ConsejoEmitido, ConsejoSilenciado
from app.models.meta import Meta, EstadoMeta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import Presupuesto, EstadoPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.saldo_arrastrado import SaldoArrastradoTarjeta, EstadoSaldoArrastrado
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.consejos_datos import recolectar
from app.services.consejos_extra import evaluar_candidatos
from app.services.presupuesto_service import uso_periodo
from app.services.consejos_reglas import (
    FilaGasto,
    FilaHistorial,
    comparar_medicion,
    elegir,
    fuera_de_consejos,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.services.inflacion_esperada_service import InflacionEsperada
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)


def _quitar_tildes(texto: str) -> str:
    """Convierte a minúsculas y remueve acentos/diacríticos."""
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    ).lower()


def consejo_activo(
    db: Session,
    usuario_id: UUID,
    hoy: date | None = None,
) -> ConsejoEmitido | None:
    """Retorna el consejo activo del usuario (respuesta None y fecha_vence > hoy, el más nuevo)."""
    fecha_ref = hoy if hoy is not None else hoy_argentina()
    return (
        db.query(ConsejoEmitido)
        .filter(
            ConsejoEmitido.usuario_id == usuario_id,
            ConsejoEmitido.respuesta.is_(None),
            ConsejoEmitido.fecha_vence > fecha_ref,
        )
        .order_by(ConsejoEmitido.fecha_emision.desc(), ConsejoEmitido.created_at.desc())
        .first()
    )


def consejo_cumplido(
    db: Session,
    usuario: Usuario,
    consejo: ConsejoEmitido,
    hoy: date,
) -> bool:
    """
    Determina de forma liviana y en solo lectura si la persona ya cumplió
    la acción sugerida por el consejo.
    """
    tipo = consejo.tipo

    if tipo == "sin_presupuesto":
        cat_id_raw = consejo.valores.get("categoria_id")
        if not cat_id_raw:
            return False
        cat_id_target = UUID(str(cat_id_raw))
        presus = (
            db.query(Presupuesto)
            .options(
                selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria)
            )
            .filter(
                Presupuesto.usuario_id == usuario.id,
                Presupuesto.eliminado_en.is_(None),
                Presupuesto.estado.in_([EstadoPresupuesto.ACTIVO, EstadoPresupuesto.PAUSADO]),
            )
            .all()
        )
        for p in presus:
            for pc in p.categorias:
                if pc.categoria_id == cat_id_target:
                    return True
                if pc.subcategoria and pc.subcategoria.categoria_id == cat_id_target:
                    return True
        return False

    elif tipo == "colchon":
        metas = db.query(Meta).filter(Meta.usuario_id == usuario.id).all()
        for m in metas:
            fc_ar = (
                m.fecha_creacion.astimezone(TZ_ARGENTINA).date()
                if m.fecha_creacion.tzinfo
                else m.fecha_creacion.date()
            )
            if fc_ar >= consejo.fecha_emision:
                return True
            if m.estado == EstadoMeta.ACTIVA:
                nom = _quitar_tildes(m.nombre)
                if any(k in nom for k in ("imprevist", "emergenc", "colchon")):
                    return True
        return False

    elif tipo == "extra":
        hay_aporte = (
            db.query(MovimientoMeta.id)
            .join(Meta, MovimientoMeta.meta_id == Meta.id)
            .filter(
                Meta.usuario_id == usuario.id,
                MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
                MovimientoMeta.fecha >= consejo.fecha_emision,
            )
            .first()
            is not None
        )
        if hay_aporte:
            return True
        metas = db.query(Meta).filter(Meta.usuario_id == usuario.id).all()
        for m in metas:
            fc_ar = (
                m.fecha_creacion.astimezone(TZ_ARGENTINA).date()
                if m.fecha_creacion.tzinfo
                else m.fecha_creacion.date()
            )
            if fc_ar >= consejo.fecha_emision:
                return True
        return False

    elif tipo == "meta":
        partes = consejo.clave.split("/")
        if len(partes) < 2:
            return False
        try:
            meta_id = UUID(partes[1])
        except Exception:
            return False

        meta_obj = db.query(Meta).filter(Meta.id == meta_id, Meta.usuario_id == usuario.id).first()
        if meta_obj is None or meta_obj.estado != EstadoMeta.ACTIVA:
            return True

        if consejo.accion == "aportar_meta":
            hay_aporte = (
                db.query(MovimientoMeta.id)
                .filter(
                    MovimientoMeta.meta_id == meta_id,
                    MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
                    MovimientoMeta.fecha >= consejo.fecha_emision,
                )
                .first()
                is not None
            )
            return hay_aporte
        elif consejo.accion == "poner_fecha_meta":
            return meta_obj.fecha_limite is not None
        return False

    elif tipo == "ritmo":
        presu_id_raw = consejo.valores.get("presupuesto_id")
        inicio_raw = consejo.valores.get("inicio")
        limite_raw = consejo.valores.get("limite")
        if not presu_id_raw or not inicio_raw or limite_raw is None:
            return False
        try:
            presupuesto_id = UUID(str(presu_id_raw))
            fecha_inicio_val = date.fromisoformat(str(inicio_raw))
            limite_original = Decimal(str(limite_raw))
        except Exception:
            return False

        presu = (
            db.query(Presupuesto)
            .filter(Presupuesto.id == presupuesto_id, Presupuesto.usuario_id == usuario.id)
            .first()
        )
        if presu is None or presu.eliminado_en is not None or presu.estado != EstadoPresupuesto.ACTIVO:
            return True

        periodo = (
            db.query(PeriodoPresupuesto)
            .filter(
                PeriodoPresupuesto.presupuesto_id == presupuesto_id,
                PeriodoPresupuesto.fecha_inicio == fecha_inicio_val,
            )
            .first()
        )
        if periodo is None:
            return True

        return periodo.monto_limite > limite_original

    elif tipo == "arrastre":
        tarjeta_id_raw = consejo.valores.get("tarjeta_id")
        moneda_val = consejo.valores.get("moneda")
        if not tarjeta_id_raw or not moneda_val:
            return False
        try:
            tarjeta_id = UUID(str(tarjeta_id_raw))
        except Exception:
            return False

        saldos = (
            db.query(SaldoArrastradoTarjeta)
            .join(TarjetaCredito, SaldoArrastradoTarjeta.tarjeta_id == TarjetaCredito.id)
            .filter(
                TarjetaCredito.usuario_id == usuario.id,
                SaldoArrastradoTarjeta.tarjeta_id == tarjeta_id,
                SaldoArrastradoTarjeta.estado == EstadoSaldoArrastrado.ACTIVO,
            )
            .all()
        )
        saldos_moneda = [
            s for s in saldos
            if (s.moneda.value if hasattr(s.moneda, "value") else str(s.moneda)) == moneda_val
        ]
        suma_restante = sum((s.monto_restante for s in saldos_moneda), Decimal("0"))
        return suma_restante == Decimal("0")

    elif tipo == "sin_ingresos":
        cond_i = condicion_ingreso(usuario.id, desde=consejo.fecha_emision, hasta=hoy, moneda=None, hoy=hoy)
        count_ing = db.query(func.count(Transaccion.id)).filter(cond_i).scalar() or 0
        return count_ing > 0

    elif tipo == "plata_quieta":
        inv_raw = consejo.valores.get("inversiones")
        if inv_raw is None:
            return False
        inv_original = Decimal(str(inv_raw))
        bils_inv = (
            db.query(Billetera)
            .filter(
                Billetera.usuario_id == usuario.id,
                Billetera.estado == EstadoBilletera.ACTIVA,
                Billetera.es_inversion.is_(True),
            )
            .all()
        )
        bils_inv_ars = [
            b for b in bils_inv
            if (b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)) == "ARS"
        ]
        suma_inv = sum((Decimal(str(b.saldo_actual)) for b in bils_inv_ars), Decimal("0"))
        return suma_inv > inv_original

    return False


def resolver_cumplidos(
    db: Session,
    usuario: Usuario,
    hoy: date,
    commit: bool = True,
) -> int:
    """
    A cada consejo del usuario con respuesta None y fecha_vence > hoy
    que esté cumplido le pone respuesta 'resuelto' y fecha_respuesta = ahora UTC.
    """
    activos = (
        db.query(ConsejoEmitido)
        .filter(
            ConsejoEmitido.usuario_id == usuario.id,
            ConsejoEmitido.respuesta.is_(None),
            ConsejoEmitido.fecha_vence > hoy,
        )
        .all()
    )
    resueltos = 0
    now_utc = datetime.now(timezone.utc)
    for c in activos:
        if consejo_cumplido(db, usuario, c, hoy):
            c.respuesta = "resuelto"
            c.fecha_respuesta = now_utc
            resueltos += 1

    if resueltos > 0:
        if commit:
            db.commit()
        else:
            db.flush()

    return resueltos


def emitir_si_corresponde(
    db: Session,
    usuario: Usuario,
    hoy: date,
    inflacion: InflacionEsperada | None,
    commit: bool = True,
) -> ConsejoEmitido | None:
    """Evalúa las reglas y emite un consejo si corresponde."""
    resolver_cumplidos(db, usuario, hoy, commit=commit)

    datos = recolectar(db, usuario, hoy, inflacion)
    candidatos = evaluar_candidatos(datos)

    filas_hist = [
        FilaHistorial(
            tipo=c.tipo,
            clave=c.clave,
            fecha_emision=c.fecha_emision,
            respuesta=c.respuesta,
            fecha_vence=c.fecha_vence,
        )
        for c in db.query(ConsejoEmitido).filter(ConsejoEmitido.usuario_id == usuario.id).all()
    ]
    silenciados = {
        row[0]
        for row in (
            db.query(ConsejoSilenciado.tipo)
            .filter(ConsejoSilenciado.usuario_id == usuario.id)
            .all()
        )
    }

    prioridades = usuario.prioridades_consejos or []
    c = elegir(
        candidatos,
        filas_hist,
        silenciados,
        prioridades,
        hoy,
        datos.cotizacion_compra,
    )

    if c is None:
        return None

    emitido = ConsejoEmitido(
        id=uuid4(),
        usuario_id=usuario.id,
        tipo=c.tipo,
        clave=c.clave,
        moneda=c.moneda,
        texto=c.texto,
        accion=c.accion,
        accion_datos=c.accion_datos,
        valores=c.valores,
        fecha_emision=hoy,
        fecha_vence=hoy + timedelta(days=7),
    )
    db.add(emitido)

    if commit:
        db.commit()
        db.refresh(emitido)
    else:
        db.flush()

    return emitido


def responder(
    db: Session,
    usuario_id: UUID,
    consejo_id: UUID,
    respuesta: str,
    commit: bool = True,
) -> None:
    """Registra la respuesta del usuario a un consejo emitido."""
    if respuesta not in {"sirvio", "no_sirve", "no_avisar"}:
        raise ValueError(f"Respuesta inválida: {respuesta}")

    consejo = (
        db.query(ConsejoEmitido)
        .filter(ConsejoEmitido.id == consejo_id)
        .first()
    )
    if not consejo or consejo.usuario_id != usuario_id:
        raise LookupError("Consejo no encontrado para el usuario")

    # Si ya tenía respuesta, no cambia nada (gana la primera)
    if consejo.respuesta is not None:
        return

    consejo.respuesta = respuesta
    consejo.fecha_respuesta = datetime.now(timezone.utc)

    if respuesta == "no_avisar":
        ya_silenciado = (
            db.query(ConsejoSilenciado)
            .filter(
                ConsejoSilenciado.usuario_id == usuario_id,
                ConsejoSilenciado.tipo == consejo.tipo,
            )
            .first()
        )
        if not ya_silenciado:
            db.add(ConsejoSilenciado(usuario_id=usuario_id, tipo=consejo.tipo))

    if commit:
        db.commit()
    else:
        db.flush()


def marcar_vencidos(db: Session, hoy: date, commit: bool = True) -> int:
    """Marca como vencidos los consejos con respuesta None y fecha_vence <= hoy."""
    pendientes = (
        db.query(ConsejoEmitido)
        .filter(
            ConsejoEmitido.respuesta.is_(None),
            ConsejoEmitido.fecha_vence <= hoy,
        )
        .all()
    )
    for c in pendientes:
        c.respuesta = "vencido"

    if commit:
        db.commit()
    else:
        db.flush()

    return len(pendientes)


def medir_pendientes(
    db: Session,
    usuario: Usuario,
    hoy: date,
    commit: bool = True,
) -> int:
    """
    Mide los consejos del usuario con medicion None cuando pasaron los 2 ciclos completos (hoy > fin c2).
    """
    pendientes = (
        db.query(ConsejoEmitido)
        .filter(
            ConsejoEmitido.usuario_id == usuario.id,
            ConsejoEmitido.medicion.is_(None),
        )
        .all()
    )

    medidos = 0
    for consejo in pendientes:
        _, f0 = get_ciclo_fechas(usuario, consejo.fecha_emision)
        c1_inicio, c1_fin = get_ciclo_fechas(usuario, f0 + timedelta(days=1))
        c2_inicio, c2_fin = get_ciclo_fechas(usuario, c1_fin + timedelta(days=1))

        if hoy <= c2_fin:
            continue

        tipo = consejo.tipo
        antes = Decimal("0")
        despues = Decimal("0")

        if tipo == "hormiga":
            antes = Decimal(str(consejo.valores["total"]))
            subcategorias_raw = consejo.valores.get("subcategorias", [])
            subcat_nombres = {item[1] for item in subcategorias_raw if len(item) > 1}

            cond_c1 = condicion_gasto(usuario.id, desde=c1_inicio, hasta=c1_fin, moneda=consejo.moneda, hoy=hoy)
            cond_c2 = condicion_gasto(usuario.id, desde=c2_inicio, hasta=c2_fin, moneda=consejo.moneda, hoy=hoy)

            g1 = sum(
                (Decimal(str(t.monto)) for t in db.query(Transaccion).join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id).filter(cond_c1, Subcategoria.nombre.in_(subcat_nombres)).all()),
                Decimal("0"),
            )
            g2 = sum(
                (Decimal(str(t.monto)) for t in db.query(Transaccion).join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id).filter(cond_c2, Subcategoria.nombre.in_(subcat_nombres)).all()),
                Decimal("0"),
            )
            despues = (g1 + g2) / Decimal("2")

        elif tipo == "comisiones":
            antes = Decimal(str(consejo.valores["total"])) / Decimal(str(consejo.valores["n"]))
            cond_c1 = condicion_gasto(usuario.id, desde=c1_inicio, hasta=c1_fin, moneda="ARS", hoy=hoy)
            cond_c2 = condicion_gasto(usuario.id, desde=c2_inicio, hasta=c2_fin, moneda="ARS", hoy=hoy)

            g1 = sum(
                (Decimal(str(t.monto)) for t in db.query(Transaccion).join(Categoria, Transaccion.categoria_id == Categoria.id).join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id).filter(cond_c1, Categoria.nombre == "Banco", Subcategoria.nombre == "Comisiones").all()),
                Decimal("0"),
            )
            g2 = sum(
                (Decimal(str(t.monto)) for t in db.query(Transaccion).join(Categoria, Transaccion.categoria_id == Categoria.id).join(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id).filter(cond_c2, Categoria.nombre == "Banco", Subcategoria.nombre == "Comisiones").all()),
                Decimal("0"),
            )
            despues = (g1 + g2) / Decimal("2")

        elif tipo == "pico":
            antes = Decimal(str(consejo.valores["mediana"]))
            cat_nom = consejo.valores["categoria"]

            cond_c1 = condicion_gasto(usuario.id, desde=c1_inicio, hasta=c1_fin, moneda=consejo.moneda, hoy=hoy)
            cond_c2 = condicion_gasto(usuario.id, desde=c2_inicio, hasta=c2_fin, moneda=consejo.moneda, hoy=hoy)

            txs_c1 = (
                db.query(Transaccion, Categoria.nombre, Subcategoria.nombre)
                .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
                .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
                .filter(cond_c1, Categoria.nombre == cat_nom)
                .all()
            )
            txs_c2 = (
                db.query(Transaccion, Categoria.nombre, Subcategoria.nombre)
                .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
                .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
                .filter(cond_c2, Categoria.nombre == cat_nom)
                .all()
            )

            g1 = sum(
                (Decimal(str(tx.monto)) for tx, c_nom, s_nom in txs_c1
                 if not fuera_de_consejos(FilaGasto(fecha=tx.fecha, monto=Decimal(str(tx.monto)), moneda=consejo.moneda or "ARS", categoria=c_nom, subcategoria=s_nom, es_espejo=(tx.reintegro_de_id is not None), es_cuota=bool(tx.es_cuota_hija)))),
                Decimal("0"),
            )
            g2 = sum(
                (Decimal(str(tx.monto)) for tx, c_nom, s_nom in txs_c2
                 if not fuera_de_consejos(FilaGasto(fecha=tx.fecha, monto=Decimal(str(tx.monto)), moneda=consejo.moneda or "ARS", categoria=c_nom, subcategoria=s_nom, es_espejo=(tx.reintegro_de_id is not None), es_cuota=bool(tx.es_cuota_hija)))),
                Decimal("0"),
            )
            despues = (g1 + g2) / Decimal("2")

        elif tipo == "sin_rubro":
            antes = Decimal(str(consejo.valores["pct"]))
            cond_c1 = condicion_gasto(usuario.id, desde=c1_inicio, hasta=c1_fin, moneda=consejo.moneda, hoy=hoy)
            txs_c1 = (
                db.query(Transaccion, Categoria.nombre, Subcategoria.nombre)
                .outerjoin(Categoria, Transaccion.categoria_id == Categoria.id)
                .outerjoin(Subcategoria, Transaccion.subcategoria_id == Subcategoria.id)
                .filter(cond_c1)
                .all()
            )
            g_c1 = sum((Decimal(str(tx.monto)) for tx, _, _ in txs_c1), Decimal("0"))
            if g_c1 <= Decimal("0"):
                despues = Decimal("0")
            else:
                s_c1 = sum(
                    (Decimal(str(tx.monto)) for tx, c_nom, s_nom in txs_c1
                     if c_nom is None or (c_nom == "Otros" and s_nom is None)),
                    Decimal("0"),
                )
                despues = (s_c1 * Decimal("100") / g_c1).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        elif tipo == "plata_quieta":
            antes = Decimal(str(consejo.valores["inversiones"]))
            bils_inv = (
                db.query(Billetera)
                .filter(
                    Billetera.usuario_id == usuario.id,
                    Billetera.estado == EstadoBilletera.ACTIVA,
                    Billetera.moneda == Moneda.ARS,
                    Billetera.es_inversion == True,
                )
                .all()
            )
            despues = sum((Decimal(str(b.saldo_actual)) for b in bils_inv), Decimal("0"))

        elif tipo == "colchon":
            antes = Decimal("0")
            metas = db.query(Meta).filter(Meta.usuario_id == usuario.id).all()
            nuevas = [
                m for m in metas
                if (m.fecha_creacion.astimezone(TZ_ARGENTINA).date() if m.fecha_creacion.tzinfo else m.fecha_creacion.date()) >= consejo.fecha_emision
            ]
            despues = Decimal(len(nuevas))

        elif tipo == "sin_ingresos":
            antes = Decimal("0")
            cond_i = condicion_ingreso(usuario.id, desde=consejo.fecha_emision, hasta=c2_fin, moneda=None, hoy=hoy)
            ing_count = db.query(func.count(Transaccion.id)).filter(cond_i).scalar() or 0
            despues = Decimal(ing_count)

        elif tipo == "ritmo":
            antes = Decimal(str(consejo.valores["limite"]))
            presupuesto_id_val = UUID(str(consejo.valores["presupuesto_id"]))
            fecha_inicio_val = date.fromisoformat(str(consejo.valores["inicio"]))
            periodo = (
                db.query(PeriodoPresupuesto)
                .filter(
                    PeriodoPresupuesto.presupuesto_id == presupuesto_id_val,
                    PeriodoPresupuesto.fecha_inicio == fecha_inicio_val,
                )
                .first()
            )
            presupuesto = db.get(Presupuesto, presupuesto_id_val)
            if not periodo or not presupuesto:
                consejo.medicion = {
                    "resultado": "sin_dato",
                    "antes": str(antes.quantize(Decimal("0.01"))),
                    "despues": "0.00",
                }
                consejo.fecha_medicion = hoy
                medidos += 1
                continue
            despues = uso_periodo(db, presupuesto, periodo, hoy=hoy, usuario=usuario).calculado

        elif tipo == "sin_presupuesto":
            antes = Decimal("0")
            cat_id_target = UUID(str(consejo.valores["categoria_id"]))
            presus = (
                db.query(Presupuesto)
                .options(
                    selectinload(Presupuesto.categorias).joinedload(PresupuestoCategoria.subcategoria)
                )
                .filter(
                    Presupuesto.usuario_id == usuario.id,
                    Presupuesto.eliminado_en.is_(None),
                )
                .all()
            )
            cant = 0
            for p in presus:
                p_fecha = (
                    p.fecha_creacion.astimezone(TZ_ARGENTINA).date()
                    if p.fecha_creacion.tzinfo
                    else p.fecha_creacion.date()
                )
                if p_fecha < consejo.fecha_emision:
                    continue
                coincide = False
                for pc in p.categorias:
                    if pc.categoria_id == cat_id_target:
                        coincide = True
                        break
                    if pc.subcategoria and pc.subcategoria.categoria_id == cat_id_target:
                        coincide = True
                        break
                if coincide:
                    cant += 1
            despues = Decimal(cant)

        elif tipo == "arrastre":
            antes = Decimal(str(consejo.valores["saldo"]))
            tarjeta_id_val = UUID(str(consejo.valores["tarjeta_id"]))
            moneda_val = str(consejo.valores["moneda"])
            saldos_db = (
                db.query(SaldoArrastradoTarjeta)
                .join(TarjetaCredito, SaldoArrastradoTarjeta.tarjeta_id == TarjetaCredito.id)
                .filter(
                    TarjetaCredito.usuario_id == usuario.id,
                    SaldoArrastradoTarjeta.tarjeta_id == tarjeta_id_val,
                    SaldoArrastradoTarjeta.estado == EstadoSaldoArrastrado.ACTIVO,
                )
                .all()
            )
            saldos_filtrados = [
                s for s in saldos_db
                if (s.moneda.value if hasattr(s.moneda, "value") else str(s.moneda)) == moneda_val
            ]
            despues = sum((Decimal(str(s.monto_restante)) for s in saldos_filtrados), Decimal("0"))

        elif tipo == "meta":
            antes = Decimal(str(consejo.valores["ritmo"]))
            partes = consejo.clave.split("/")
            meta_obj = (
                db.query(Meta)
                .filter(Meta.id == UUID(partes[1]), Meta.usuario_id == usuario.id)
                .first()
            )
            if meta_obj is None:
                despues = Decimal("0")
            else:
                m_moneda = meta_obj.moneda.value if hasattr(meta_obj.moneda, "value") else str(meta_obj.moneda)
                netos = []
                for c_ini, c_fin in ((c1_inicio, c1_fin), (c2_inicio, c2_fin)):
                    movs = (
                        db.query(MovimientoMeta)
                        .filter(
                            MovimientoMeta.meta_id == meta_obj.id,
                            MovimientoMeta.fecha >= c_ini,
                            MovimientoMeta.fecha <= c_fin,
                        )
                        .all()
                    )
                    tot_c = Decimal("0")
                    for mov in movs:
                        mov_mon = (
                            mov.moneda_movimiento.value
                            if hasattr(mov.moneda_movimiento, "value")
                            else str(mov.moneda_movimiento)
                        )
                        if mov_mon == m_moneda:
                            monto_conv = Decimal(str(mov.monto))
                        else:
                            if not mov.cotizacion_usada or mov.cotizacion_usada <= Decimal("0"):
                                continue
                            cot_usada = Decimal(str(mov.cotizacion_usada))
                            if m_moneda == "USD" and mov_mon == "ARS":
                                monto_conv = Decimal(str(mov.monto)) / cot_usada
                            elif m_moneda == "ARS" and mov_mon == "USD":
                                monto_conv = Decimal(str(mov.monto)) * cot_usada
                            else:
                                continue

                        if mov.tipo == TipoMovimientoMeta.APORTE:
                            tot_c += monto_conv
                        elif mov.tipo == TipoMovimientoMeta.RETIRO:
                            tot_c -= monto_conv
                    netos.append(tot_c)
                despues = sum(netos, Decimal("0")) / Decimal("2")

        elif tipo == "extra":
            antes = Decimal("0")
            f_ini = consejo.fecha_emision
            f_fin = consejo.fecha_emision + timedelta(days=7)
            aportes_cant = (
                db.query(func.count(MovimientoMeta.id))
                .join(Meta, MovimientoMeta.meta_id == Meta.id)
                .filter(
                    Meta.usuario_id == usuario.id,
                    MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
                    MovimientoMeta.fecha >= f_ini,
                    MovimientoMeta.fecha <= f_fin,
                )
                .scalar()
                or 0
            )
            metas_usr = db.query(Meta).filter(Meta.usuario_id == usuario.id).all()
            metas_creadas = sum(
                1 for m in metas_usr
                if f_ini <= (m.fecha_creacion.astimezone(TZ_ARGENTINA).date() if m.fecha_creacion.tzinfo else m.fecha_creacion.date()) <= f_fin
            )
            despues = Decimal(aportes_cant + metas_creadas)

        elif tipo in ("carga", "subio", "dolares"):
            consejo.medicion = {
                "resultado": "sin_dato",
                "antes": "0.00",
                "despues": "0.00",
            }
            consejo.fecha_medicion = hoy
            medidos += 1
            continue

        res_medicion = comparar_medicion(tipo, antes, despues)
        consejo.medicion = {
            "resultado": res_medicion,
            "antes": str(antes.quantize(Decimal("0.01"))),
            "despues": str(despues.quantize(Decimal("0.01"))),
        }
        consejo.fecha_medicion = hoy
        medidos += 1

    if commit:
        db.commit()
    else:
        db.flush()

    return medidos
