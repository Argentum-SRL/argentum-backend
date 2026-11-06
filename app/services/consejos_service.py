# -*- coding: utf-8 -*-
"""
app/services/consejos_service.py
Lógica de persistencia, emisión, respuesta, vencimiento y medición de consejos.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.consejo import ConsejoEmitido, ConsejoSilenciado
from app.models.meta import Meta
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto import Presupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.consejos_datos import recolectar
from app.services.presupuesto_service import uso_periodo
from app.services.consejos_reglas import (
    FilaGasto,
    FilaHistorial,
    comparar_medicion,
    elegir,
    evaluar_todos,
    fuera_de_consejos,
)
from app.services.dashboard_service import get_ciclo_fechas
from app.services.definiciones_service import condicion_gasto, condicion_ingreso
from app.services.inflacion_esperada_service import InflacionEsperada
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina

logger = logging.getLogger(__name__)


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


def emitir_si_corresponde(
    db: Session,
    usuario: Usuario,
    hoy: date,
    inflacion: InflacionEsperada | None,
    commit: bool = True,
) -> ConsejoEmitido | None:
    """Evalúa las reglas y emite un consejo si corresponde."""
    datos = recolectar(db, usuario, hoy, inflacion)
    candidatos = evaluar_todos(datos)

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
