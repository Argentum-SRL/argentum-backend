# -*- coding: utf-8 -*-
"""
Generador del usuario demo (Lucía Demo).
Aplica el plan determinístico usando los servicios oficiales de la app.
"""
from __future__ import annotations

import calendar
from contextlib import contextmanager
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.core.security import get_password_hash, validar_reglas_password
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.schemas.transaccion import InfoCuotas, TransaccionCreate
from app.schemas.transferencia_interna import TransferenciaInternaCreate
from app.services import (
    calendario_tarjeta_service,
    cobro_suscripcion_service,
    dolar_service,
    meta_service,
    pago_resumen_service,
    que_es_plata_service,
    reintegro_service,
    rendimiento_billetera_service,
    saldo_billetera_service,
    saldo_programado_service,
    transaccion_service,
    transferencia_service,
    usuario_service,
)
from app.services.saldo_billetera_service import aplicar_impacto_saldo
from app.utils.fecha import hoy_argentina
from scripts.demo.catalogo import CatalogoDemo
from scripts.demo.resumen import (
    construir_resumen as _construir_resumen,
    fecha_a_utc_12_argentina,
)
from scripts.demo.persona import (
    EMAIL_DEMO,
    NOMBRE_DEMO,
    APELLIDO_DEMO,
    SEMILLA_DEMO,
    construir_plan_completo,
)

logger = logging.getLogger(__name__)

def crear_usuario_demo(db: Session, password: str) -> Usuario:
    """Crea la cuenta del usuario demo con todas las validaciones de seguridad."""
    usuario_existente = db.execute(
        select(Usuario).where(Usuario.email == EMAIL_DEMO)
    ).scalar_one_or_none()
    if usuario_existente:
        raise ValueError("El usuario demo ya existe")

    validar_reglas_password(password)
    hashed_pw = get_password_hash(password)

    usuario = Usuario(
        id=uuid4(),
        email=EMAIL_DEMO,
        auth_provider=AuthProvider.EMAIL,
        password_hash=hashed_pw,
        password_configurada=True,
        nombre=NOMBRE_DEMO,
        apellido=APELLIDO_DEMO,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="25",
        onboarding_completo=True,
        modal_bienvenida_financiera_visto=True,
        email_verificado=True,
        telefono=None,
    )
    db.add(usuario)
    db.flush()

    # Apagar TODOS los avisos por mail y WhatsApp
    cfg = ConfiguracionNotificacion(
        usuario_id=usuario.id,
        cuota_vence_web=False, cuota_vence_whatsapp=False, presupuesto_umbral_1_web=False, presupuesto_umbral_1_whatsapp=False,
        presupuesto_umbral_2_web=False, presupuesto_umbral_2_whatsapp=False, suscripcion_hoy_web=False, suscripcion_hoy_whatsapp=False,
        suscripcion_recordatorio_activo=False, suscripcion_recordatorio_web=False, suscripcion_recordatorio_whatsapp=False,
        meta_alcanzada_activo=False, meta_alcanzada_web=False, meta_alcanzada_whatsapp=False, saldo_cero_activo=False,
        saldo_cero_web=False, saldo_cero_whatsapp=False, gasto_inusual_activo=False, gasto_inusual_web=False,
        gasto_inusual_whatsapp=False, resumen_semanal_activo=False, resumen_semanal_web=False, resumen_semanal_whatsapp=False,
    )
    db.add(cfg)
    db.flush()

    return usuario

@contextmanager
def _fijar_hoy(hoy: date):
    import sys
    saved = {}
    for mod_name, mod in list(sys.modules.items()):
        if mod_name.startswith("app."):
            if hasattr(mod, "hoy_argentina"):
                saved[(mod, "hoy_argentina")] = getattr(mod, "hoy_argentina")
                setattr(mod, "hoy_argentina", lambda: hoy)
            if hasattr(mod, "_hoy_argentina"):
                saved[(mod, "_hoy_argentina")] = getattr(mod, "_hoy_argentina")
                setattr(mod, "_hoy_argentina", lambda: hoy)
    try:
        yield
    finally:
        for (mod, attr), val in saved.items():
            setattr(mod, attr, val)

def ejecutar_crear(
    db: Session,
    password: str,
    hoy: Optional[date] = None,
    seed: int = SEMILLA_DEMO,
) -> Dict[str, Any]:
    """Ejecuta el modo --crear del demo."""
    if hoy is None:
        hoy = hoy_argentina()

    usuario = crear_usuario_demo(db, password)

    with _fijar_hoy(hoy):
        cat = CatalogoDemo(db, usuario)
        return _cargar_datos(db, usuario, cat, hoy, seed=seed, modo="crear")

def ejecutar_ponerse_al_dia(
    db: Session,
    hoy: Optional[date] = None,
    seed: int = SEMILLA_DEMO,
) -> Dict[str, Any]:
    """Ejecuta el modo --ponerse-al-dia del demo (idempotente)."""
    if hoy is None:
        hoy = hoy_argentina()

    usuario = db.execute(
        select(Usuario).where(Usuario.email == EMAIL_DEMO)
    ).scalar_one_or_none()
    if not usuario:
        raise ValueError("El usuario demo no existe. Corré primero con --crear.")

    with _fijar_hoy(hoy):
        cat = CatalogoDemo(db, usuario)
        return _cargar_datos(db, usuario, cat, hoy, seed=seed, modo="ponerse_al_dia")

def ejecutar_borrar(db: Session, confirmacion: str) -> bool:
    """Borra el usuario demo y todo lo suyo usando eliminar_usuario."""
    if confirmacion.strip() != EMAIL_DEMO:
        print(f"Confirmación no coincide con '{EMAIL_DEMO}'. Operación cancelada.")
        return False

    usuario = db.execute(
        select(Usuario).where(Usuario.email == EMAIL_DEMO)
    ).scalar_one_or_none()
    if not usuario:
        print(f"El usuario '{EMAIL_DEMO}' no existe en la base de datos.")
        return False

    usuario_service.eliminar_usuario(db, usuario)
    print(f"Usuario demo '{EMAIL_DEMO}' y todos sus registros han sido eliminados correctamente.")
    return True

def _crear_transferencia_demo(db: Session, usuario_id: UUID, data: TransferenciaInternaCreate) -> TransferenciaInterna:
    try:
        return transferencia_service.crear_transferencia(db, usuario_id, data)
    except HTTPException:
        b_o, b_d = db.get(Billetera, data.billetera_origen_id), db.get(Billetera, data.billetera_destino_id)
        m_d = data.monto_destino or data.monto
        tr = TransferenciaInterna(
            usuario_id=usuario_id, billetera_origen_id=b_o.id, billetera_destino_id=b_d.id,
            monto=data.monto, moneda=data.moneda or b_o.moneda, monto_origen=data.monto, monto_destino=m_d,
            moneda_origen=b_o.moneda, moneda_destino=b_d.moneda, cotizacion=data.cotizacion,
            fecha=data.fecha, notas=data.notas,
        )
        aplicar_impacto_saldo(b_o, -data.monto, data.fecha)
        aplicar_impacto_saldo(b_d, m_d, data.fecha)
        db.add(tr); db.flush()
        return tr
    try:
        return transferencia_service.crear_transferencia(db, usuario_id, data)
    except HTTPException:
        b_o, b_d = db.get(Billetera, data.billetera_origen_id), db.get(Billetera, data.billetera_destino_id)
        m_d = data.monto_destino or data.monto
        tr = TransferenciaInterna(
            usuario_id=usuario_id, billetera_origen_id=b_o.id, billetera_destino_id=b_d.id,
            monto=data.monto, moneda=data.moneda or b_o.moneda, monto_origen=data.monto, monto_destino=m_d,
            moneda_origen=b_o.moneda, moneda_destino=b_d.moneda, cotizacion=data.cotizacion,
            fecha=data.fecha, notas=data.notas,
        )
        aplicar_impacto_saldo(b_o, -data.monto, data.fecha)
        aplicar_impacto_saldo(b_d, m_d, data.fecha)
        db.add(tr); db.flush()
        return tr

def _registrar_movimiento_meta_demo(db: Session, usuario_id: UUID, meta_id: UUID, data: MovimientoMetaCreate) -> MovimientoMeta:
    try:
        return meta_service.registrar_movimiento(db, usuario_id, meta_id, data)
    except HTTPException:
        billetera, meta = db.get(Billetera, data.billetera_id), db.get(Meta, meta_id)
        cat_id = meta_service.obtener_o_crear_categoria_ahorro(db, usuario_id, "egreso")
        tx = transaccion_service.crear_transaccion(
            db, usuario_id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO, monto=data.monto, moneda=data.moneda_movimiento, fecha=data.fecha,
                descripcion=f"Aporte a la meta: {meta.nombre}", categoria_id=cat_id, billetera_id=billetera.id,
                metodo_pago=MetodoPago.TRANSFERENCIA, origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
            commit=False,
        )
        mov = MovimientoMeta(
            meta_id=meta.id, billetera_id=billetera.id, tipo=data.tipo,
            monto=data.monto, moneda_movimiento=data.moneda_movimiento,
            cotizacion_usada=data.cotizacion_usada, tipo_dolar_usado=data.tipo_dolar_usado,
            fecha=data.fecha,
        )
        meta.monto_actual += data.monto
        db.add(mov); db.flush()
        tx.movimiento_meta_id = mov.id
        db.flush()
        return mov

def _cargar_datos(
    db: Session,
    usuario: Usuario,
    cat: CatalogoDemo,
    hoy: date,
    seed: int,
    modo: str,
) -> Dict[str, Any]:
    plan = construir_plan_completo(seed=seed)

    # 1. Sueldos y aguinaldos
    for s in plan["sueldos"]:
        f = s["fecha"]
        # En modo ponerse_al_dia, solo si no existe
        if modo == "ponerse_al_dia":
            existe = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f,
                    Transaccion.descripcion == s["descripcion"],
                )
            ).first()
            if existe:
                continue

        transaccion_service.crear_transaccion(
            db,
            usuario.id,
            TransaccionCreate(
                tipo=TipoTransaccion.INGRESO,
                monto=s["monto"],
                moneda=Moneda.ARS,
                fecha=f,
                descripcion=s["descripcion"],
                categoria_id=cat.c_trabajo.id,
                subcategoria_id=cat.s_sueldo.id,
                metodo_pago=MetodoPago.TRANSFERENCIA,
                billetera_id=cat.b_galicia.id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
        )

    for a in plan["aguinaldos"]:
        f = a["fecha"]
        if modo == "ponerse_al_dia":
            existe = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f,
                    Transaccion.descripcion == a["descripcion"],
                )
            ).first()
            if existe:
                continue

        transaccion_service.crear_transaccion(
            db,
            usuario.id,
            TransaccionCreate(
                tipo=TipoTransaccion.INGRESO,
                monto=a["monto"],
                moneda=Moneda.ARS,
                fecha=f,
                descripcion=a["descripcion"],
                categoria_id=cat.c_trabajo.id,
                subcategoria_id=cat.s_sac.id,
                metodo_pago=MetodoPago.TRANSFERENCIA,
                billetera_id=cat.b_galicia.id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
        )

    # 2. Fijos
    fijos_configs = [
        (plan["alquileres"], cat.c_viv.id, cat.s_alquiler.id),
        (plan["expensas"], cat.c_viv.id, cat.s_expensas.id),
        (plan["luz"], cat.c_viv.id, cat.s_luz.id),
        (plan["gas"], cat.c_viv.id, cat.s_gas.id),
        (plan["agua"], cat.c_viv.id, cat.s_agua.id),
    ]
    for lista_fijos, cid, sid in fijos_configs:
        for item in lista_fijos:
            f = item["fecha"]
            if modo == "ponerse_al_dia":
                existe = db.execute(
                    select(Transaccion.id).where(
                        Transaccion.usuario_id == usuario.id,
                        Transaccion.fecha == f,
                        Transaccion.descripcion == item["descripcion"],
                    )
                ).first()
                if existe:
                    continue

            transaccion_service.crear_transaccion(
                db,
                usuario.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=item["monto"],
                    moneda=Moneda.ARS,
                    fecha=f,
                    descripcion=item["descripcion"],
                    categoria_id=cid,
                    subcategoria_id=sid,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_galicia.id,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
            )

    # 3. Cuotas con Visa Galicia (se crean una sola vez en --crear; sus cuotas futuras salen solas)
    if modo == "crear":
        for cp in plan["cuotas_plan"]:
            transaccion_service.crear_transaccion(
                db,
                usuario.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=cp["monto_total"],
                    moneda=Moneda.ARS,
                    fecha=cp["fecha"],
                    descripcion=cp["descripcion"],
                    categoria_id=cat.c_indum.id if "Zapatillas" in cp["descripcion"] else cat.c_tecno.id,
                    subcategoria_id=cat.s_calzado.id if "Zapatillas" in cp["descripcion"] else cat.s_equipos.id,
                    metodo_pago=MetodoPago.CREDITO,
                    tarjeta_id=cat.t_visa.id,
                    billetera_id=cat.b_galicia.id,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    info_cuotas=InfoCuotas(
                        cantidad_cuotas=cp["cantidad_cuotas"],
                        cuota_inicial=1,
                        tiene_interes=cp["tiene_interes"],
                        tasa_interes=cp["tasa_interes"],
                        monto_total=cp["monto_total"],
                        proximo_resumen=False,
                    ),
                ),
            )

    # 4. Suscripciones (cobros pasados hasta hoy)
    for s in cat.suscripciones.values():
        while s.proximo_cobro <= hoy:
            f_cobro = s.proximo_cobro
            prev_cobro = s.proximo_cobro
            ok = cobro_suscripcion_service._cobrar_suscripcion(db, s, hoy=f_cobro)
            db.commit()
            if not ok or s.proximo_cobro <= prev_cobro:
                frec = s.frecuencia.value if hasattr(s.frecuencia, "value") else str(s.frecuencia)
                s.proximo_cobro = cobro_suscripcion_service.calcular_siguiente_cobro(prev_cobro, frec)
                db.commit()

    # 5. Variables cotidianas
    for var in plan["variables"]:
        f = var.fecha
        if modo == "ponerse_al_dia":
            existe = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f,
                    Transaccion.descripcion == var.descripcion,
                )
            ).first()
            if existe:
                continue

        bid = cat.b_galicia.id if var.billetera_clave == "galicia" else (
            cat.b_mp.id if var.billetera_clave == "mercado_pago" else cat.b_efectivo.id
        )
        metodo = MetodoPago.CREDITO if var.es_credito else (
            MetodoPago.EFECTIVO if var.billetera_clave == "efectivo" else MetodoPago.DEBITO
        )
        tid = cat.t_visa.id if var.es_credito else None

        # Resolver categoría y subcategoría según subcategoría real
        if var.subcategoria_nombre == "Supermercado":
            cid, sid = cat.c_alim.id, cat.s_super.id
        elif var.subcategoria_nombre == "Delivery":
            cid, sid = cat.c_gastro.id, cat.s_delivery.id
        elif var.subcategoria_nombre == "Cafetería":
            cid, sid = cat.c_gastro.id, cat.s_cafe.id
        elif var.subcategoria_nombre in ("Restaurante", "Restaurantes"):
            cid, sid = cat.c_gastro.id, cat.s_resto.id
        elif var.subcategoria_nombre == "Salidas":
            cid, sid = cat.c_ocio.id, cat.s_salidas.id
        elif var.subcategoria_nombre == "Transporte público":
            cid, sid = cat.c_transp.id, cat.s_sube.id
        elif var.subcategoria_nombre == "Farmacia":
            cid, sid = cat.c_salud.id, cat.s_farmacia.id
        elif var.subcategoria_nombre == "Ropa":
            cid, sid = cat.c_indum.id, cat.s_ropa.id
        elif var.subcategoria_nombre == "Regalos":
            cid, sid = cat.c_otros_egr.id, cat.s_regalos.id
        else:
            cid, sid = cat.c_otros_egr.id, cat.s_regalos.id

        tx = transaccion_service.crear_transaccion(
            db,
            usuario.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=var.monto,
                moneda=Moneda.ARS,
                fecha=f,
                descripcion=var.descripcion,
                categoria_id=cid,
                subcategoria_id=sid,
                metodo_pago=metodo,
                billetera_id=bid,
                tarjeta_id=tid,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                reintegro_esperado=var.reintegro_monto if var.es_reintegro_25 else None,
            ),
        )

        # Si lleva reintegro y ya venció fecha de reintegro (3 días después) y está dentro de hoy:
        if var.es_reintegro_25 and var.reintegro_monto and var.reintegro_fecha:
            if var.reintegro_fecha <= hoy:
                reintegro_service.registrar_o_actualizar_reintegro(
                    db,
                    usuario.id,
                    tx.id,
                    var.reintegro_monto,
                    recibido=True,
                    fecha_recibido=var.reintegro_fecha,
                )

    # 6. Devolución de compra de ropa (15/10/2026: $70.000 Visa Galicia devuelta entera)
    f_ropa = date(2026, 10, 15)
    tx_ropa_dev = db.execute(
        select(Transaccion).where(
            Transaccion.usuario_id == usuario.id,
            Transaccion.fecha == f_ropa,
            Transaccion.descripcion == "Compra ropa Zara (para devolver)",
        )
    ).scalar_one_or_none()
    if not tx_ropa_dev:
        tx_ropa_dev = transaccion_service.crear_transaccion(
            db,
            usuario.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=Decimal("70000.00"),
                moneda=Moneda.ARS,
                fecha=f_ropa,
                descripcion="Compra ropa Zara (para devolver)",
                categoria_id=cat.c_indum.id,
                subcategoria_id=cat.s_ropa.id,
                metodo_pago=MetodoPago.CREDITO,
                tarjeta_id=cat.t_visa.id,
                billetera_id=cat.b_galicia.id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
        )
    if f_ropa <= hoy:
        # Verificar si ya tiene el reintegro completo
        tiene_reint = db.execute(
            select(Transaccion.id).where(
                Transaccion.reintegro_de_id == tx_ropa_dev.id,
                Transaccion.tipo_espejo == "reintegro",
            )
        ).first()
        if not tiene_reint:
            reintegro_service.registrar_o_actualizar_reintegro(
                db,
                usuario.id,
                tx_ropa_dev.id,
                Decimal("70000.00"),
                recibido=True,
                fecha_recibido=f_ropa,
            )

    # 7. Gastos en Brasil (enero 2027)
    for gb in plan["gastos_brasil"]:
        f = gb["fecha"]
        if modo == "ponerse_al_dia":
            existe = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f,
                    Transaccion.descripcion == gb["descripcion"],
                )
            ).first()
            if existe:
                continue

        transaccion_service.crear_transaccion(
            db,
            usuario.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=gb["monto"],
                moneda=Moneda.USD,
                fecha=f,
                descripcion=gb["descripcion"],
                categoria_id=cat.c_ocio.id,
                subcategoria_id=cat.s_viajes.id,
                metodo_pago=MetodoPago.EFECTIVO,
                billetera_id=cat.b_dolares.id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            ),
        )

    # 8. Gasto en dólares pagado con pesos (Steam US$15 mensual con débito de Galicia el día 22)
    for year, month in [
        (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
        (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6), (2027, 7), (2027, 8)
    ]:
        f_steam = date(year, month, 22)
        if modo == "ponerse_al_dia":
            existe = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f_steam,
                    Transaccion.descripcion == "Steam Games Videojuego",
                )
            ).first()
            if existe:
                continue

        cotiz_ref = Decimal("1350.00")
        try:
            c_val = dolar_service.obtener_cotizacion_por_fecha(db, "oficial", f_steam)
            if c_val and c_val.venta:
                cotiz_ref = Decimal(str(c_val.venta))
        except Exception:
            pass

        monto_pesos = (Decimal("15.00") * cotiz_ref).quantize(Decimal("1.00"))

        transaccion_service.crear_transaccion(
            db,
            usuario.id,
            TransaccionCreate(
                tipo=TipoTransaccion.EGRESO,
                monto=monto_pesos,
                moneda=Moneda.ARS,
                fecha=f_steam,
                descripcion="Steam Games Videojuego",
                categoria_id=cat.c_ocio.id,
                subcategoria_id=cat.s_juegos.id,
                metodo_pago=MetodoPago.DEBITO,
                billetera_id=cat.b_galicia.id,
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                moneda_original=Moneda.USD,
                monto_original=Decimal("15.00"),
                cotizacion_aplicada=cotiz_ref,
                tipo_dolar_usado="oficial",
            ),
        )

    # 9. Transferencias y operaciones periódicas (fondeos y compras)
    # 9-ef. Retiro de efectivo Galicia -> Efectivo ($90.000 el día 2 de cada mes)
    for year, month in [
        (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
        (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6), (2027, 7), (2027, 8)
    ]:
        f_2 = date(year, month, 2)
        if f_2 <= hoy:
            with _fijar_hoy(f_2):
                saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
            existe_ef = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_efectivo.id,
                    TransferenciaInterna.fecha == f_2,
                )
            ).first()
            if not existe_ef:
                _crear_transferencia_demo(
                    db,
                    usuario.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=cat.b_galicia.id,
                        billetera_destino_id=cat.b_efectivo.id,
                        monto=Decimal("90000.00"),
                        moneda=Moneda.ARS,
                        fecha=f_2,
                        notas="Retiro de efectivo",
                    ),
                )
        else:
            existe_ef = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_efectivo.id,
                    TransferenciaInterna.fecha == f_2,
                )
            ).first()
            if not existe_ef:
                db.add(TransferenciaInterna(
                    usuario_id=usuario.id,
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_efectivo.id,
                    monto=Decimal("90000.00"),
                    moneda=Moneda.ARS,
                    monto_origen=Decimal("90000.00"),
                    monto_destino=Decimal("90000.00"),
                    moneda_origen=Moneda.ARS,
                    moneda_destino=Moneda.ARS,
                    fecha=f_2,
                    notas="Retiro de efectivo",
                ))
                db.flush()

    # 9a y 9b. Fondeo MP ($350.000) y Compra USD ($100) el día 26 de cada mes
    for year, month in [
        (2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
        (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6), (2027, 7)
    ]:
        f_26 = date(year, month, 26)
        if f_26 <= hoy:
            with _fijar_hoy(f_26):
                saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
            existe_mp = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_mp.id,
                    TransferenciaInterna.fecha == f_26,
                )
            ).first()
            if not existe_mp:
                _crear_transferencia_demo(
                    db,
                    usuario.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=cat.b_galicia.id,
                        billetera_destino_id=cat.b_mp.id,
                        monto=Decimal("350000.00"),
                        moneda=Moneda.ARS,
                        fecha=f_26,
                        notas="Fondeo Mercado Pago",
                    ),
                )

            existe_usd = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_dolares.id,
                    TransferenciaInterna.fecha == f_26,
                )
            ).first()
            if not existe_usd:
                cotiz_oficial = Decimal("1300.00")
                try:
                    c_data = dolar_service.obtener_cotizacion_por_fecha(db, "oficial", f_26)
                    if c_data and c_data.venta:
                        cotiz_oficial = Decimal(str(c_data.venta))
                except Exception:
                    pass
                monto_pesos_usd = (Decimal("100.00") * cotiz_oficial).quantize(Decimal("1.00"))
                _crear_transferencia_demo(
                    db,
                    usuario.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=cat.b_galicia.id,
                        billetera_destino_id=cat.b_dolares.id,
                        monto=monto_pesos_usd,
                        moneda=Moneda.ARS,
                        monto_destino=Decimal("100.00"),
                        cotizacion=cotiz_oficial,
                        fecha=f_26,
                        notas="Compra USD ahorro oficial",
                    ),
                )
        else:
            existe_mp = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_mp.id,
                    TransferenciaInterna.fecha == f_26,
                )
            ).first()
            if not existe_mp:
                db.add(TransferenciaInterna(
                    usuario_id=usuario.id,
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_mp.id,
                    monto=Decimal("350000.00"),
                    moneda=Moneda.ARS,
                    monto_origen=Decimal("350000.00"),
                    monto_destino=Decimal("350000.00"),
                    moneda_origen=Moneda.ARS,
                    moneda_destino=Moneda.ARS,
                    fecha=f_26,
                    notas="Fondeo Mercado Pago",
                ))
                db.flush()

            existe_usd = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_dolares.id,
                    TransferenciaInterna.fecha == f_26,
                )
            ).first()
            if not existe_usd:
                cotiz_oficial = Decimal("1300.00")
                try:
                    c_data = dolar_service.obtener_cotizacion_por_fecha(db, "oficial", f_26)
                    if c_data and c_data.venta:
                        cotiz_oficial = Decimal(str(c_data.venta))
                except Exception:
                    pass
                monto_pesos_usd = (Decimal("100.00") * cotiz_oficial).quantize(Decimal("1.00"))
                db.add(TransferenciaInterna(
                    usuario_id=usuario.id,
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_dolares.id,
                    monto=monto_pesos_usd,
                    moneda=Moneda.ARS,
                    monto_origen=monto_pesos_usd,
                    monto_destino=Decimal("100.00"),
                    moneda_origen=Moneda.ARS,
                    moneda_destino=Moneda.USD,
                    cotizacion=cotiz_oficial,
                    fecha=f_26,
                    notas="Compra USD ahorro oficial",
                ))
                db.flush()

        # Día 27: Metas (Viaje a Brasil $150.000 de junio a diciembre 2026, y Fondo de emergencia $100.000 todos los meses)
        f_27 = date(year, month, 27)
        if f_27 <= hoy:
            with _fijar_hoy(f_27):
                saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
            if year == 2026 and 6 <= month <= 12:
                existe_m = db.execute(
                    select(MovimientoMeta.id).where(
                        MovimientoMeta.meta_id == cat.meta_brasil.id,
                        MovimientoMeta.fecha == f_27,
                        MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
                    )
                ).first()
                if not existe_m:
                    _registrar_movimiento_meta_demo(
                        db,
                        usuario.id,
                        cat.meta_brasil.id,
                        MovimientoMetaCreate(
                            tipo=TipoMovimientoMeta.APORTE,
                            monto=Decimal("100000.00"),
                            moneda_movimiento=Moneda.ARS,
                            billetera_id=cat.b_galicia.id,
                            fecha=f_27,
                        ),
                    )

            existe_me = db.execute(
                select(MovimientoMeta.id).where(
                    MovimientoMeta.meta_id == cat.meta_emergencia.id,
                    MovimientoMeta.fecha == f_27,
                    MovimientoMeta.tipo == TipoMovimientoMeta.APORTE,
                )
            ).first()
            if not existe_me:
                _registrar_movimiento_meta_demo(
                    db,
                    usuario.id,
                    cat.meta_emergencia.id,
                    MovimientoMetaCreate(
                        tipo=TipoMovimientoMeta.APORTE,
                        monto=Decimal("50000.00"),
                        moneda_movimiento=Moneda.ARS,
                        billetera_id=cat.b_galicia.id,
                        fecha=f_27,
                    ),
                )

        # 9c. Inversión: Galicia a Balanz $150.000 el día 28 de cada mes
        f_balanz = date(year, month, 28)
        if f_balanz <= hoy:
            with _fijar_hoy(f_balanz):
                saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)
            existe_bal = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_balanz.id,
                    TransferenciaInterna.fecha == f_balanz,
                )
            ).first()
            if not existe_bal:
                _crear_transferencia_demo(
                    db,
                    usuario.id,
                    TransferenciaInternaCreate(
                        billetera_origen_id=cat.b_galicia.id,
                        billetera_destino_id=cat.b_balanz.id,
                        monto=Decimal("150000.00"),
                        moneda=Moneda.ARS,
                        fecha=f_balanz,
                        notas="Transferencia para inversión Balanz",
                    ),
                )
        else:
            existe_bal = db.execute(
                select(TransferenciaInterna.id).where(
                    TransferenciaInterna.usuario_id == usuario.id,
                    TransferenciaInterna.billetera_origen_id == cat.b_galicia.id,
                    TransferenciaInterna.billetera_destino_id == cat.b_balanz.id,
                    TransferenciaInterna.fecha == f_balanz,
                )
            ).first()
            if not existe_bal:
                db.add(TransferenciaInterna(
                    usuario_id=usuario.id,
                    billetera_origen_id=cat.b_galicia.id,
                    billetera_destino_id=cat.b_balanz.id,
                    monto=Decimal("150000.00"),
                    moneda=Moneda.ARS,
                    monto_origen=Decimal("150000.00"),
                    monto_destino=Decimal("150000.00"),
                    moneda_origen=Moneda.ARS,
                    moneda_destino=Moneda.ARS,
                    fecha=f_balanz,
                    notas="Transferencia para inversión Balanz",
                ))
                db.flush()

    # Rendimiento Balanz el último día del mes
        last_day = calendar.monthrange(year, month)[1]
        f_rend = date(year, month, last_day)
        if f_rend <= hoy:
            existe_rend = db.execute(
                select(RendimientoBilletera.id).where(
                    RendimientoBilletera.billetera_id == cat.b_balanz.id,
                    func.date(RendimientoBilletera.fecha) == f_rend,
                )
            ).first()
            if not existe_rend:
                # Calcular rendimiento sobre saldo actual de Balanz (TNA 32% mensual ~ 2.66%)
                s_balanz = cat.b_balanz.saldo_actual or Decimal("0.00")
                if s_balanz > 0:
                    monto_rend = (s_balanz * Decimal("0.32") / Decimal("12")).quantize(Decimal("1.00"))
                    if monto_rend > 0:
                        rendimiento_billetera_service.confirmar_rendimiento(
                            db,
                            usuario.id,
                            cat.b_balanz.id,
                            monto=monto_rend,
                            fecha=fecha_a_utc_12_argentina(f_rend),
                        )

    # Retiro de meta Brasil: 05/01/2027 $900.000
    f_ret_b = date(2027, 1, 5)
    if f_ret_b <= hoy:
        existe_ret = db.execute(
            select(MovimientoMeta.id).where(
                MovimientoMeta.meta_id == cat.meta_brasil.id,
                MovimientoMeta.fecha == f_ret_b,
                MovimientoMeta.tipo == TipoMovimientoMeta.RETIRO,
            )
        ).first()
        if not existe_ret:
            meta_service.registrar_movimiento(
                db,
                usuario.id,
                cat.meta_brasil.id,
                MovimientoMetaCreate(
                    tipo=TipoMovimientoMeta.RETIRO,
                    monto=Decimal("600000.00"),
                    moneda_movimiento=Moneda.ARS,
                    billetera_id=cat.b_galicia.id,
                    fecha=f_ret_b,
                ),
            )

    # 11. ¿Qué es esta plata? (cena amigas cada dos meses el día 16, devolución $30.000 al día siguiente)
    for year, month in [(2026, 6), (2026, 8), (2026, 10), (2026, 12), (2027, 2), (2027, 4), (2027, 6)]:
        f_cena = date(year, month, 27)
        f_dev = date(year, month, 28)

        tx_cena = db.execute(
            select(Transaccion).where(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha == f_cena,
                Transaccion.descripcion == "Cena con amigas",
            )
        ).scalar_one_or_none()
        if not tx_cena:
            tx_cena = transaccion_service.crear_transaccion(
                db,
                usuario.id,
                TransaccionCreate(
                    tipo=TipoTransaccion.EGRESO,
                    monto=Decimal("60000.00"),
                    moneda=Moneda.ARS,
                    fecha=f_cena,
                    descripcion="Cena con amigas",
                    categoria_id=cat.c_gastro.id,
                    subcategoria_id=cat.s_resto.id,
                    metodo_pago=MetodoPago.DEBITO,
                    billetera_id=cat.b_mp.id,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                ),
            )

        if f_dev <= hoy:
            tx_ing_amiga = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f_dev,
                    or_(
                        Transaccion.monto == Decimal("30000.00"),
                        Transaccion.monto == Decimal("-30000.00"),
                    ),
                    Transaccion.billetera_id == cat.b_mp.id,
                )
            ).first()
            if not tx_ing_amiga:
                tx_nueva = transaccion_service.crear_transaccion(
                    db,
                    usuario.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.INGRESO,
                        monto=Decimal("30000.00"),
                        moneda=Moneda.ARS,
                        fecha=f_dev,
                        descripcion="Transferencia amiga por cena",
                        categoria_id=cat.c_otros_ing.id,
                        subcategoria_id=cat.s_cobros.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=cat.b_mp.id,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                )
                que_es_plata_service.responder(
                    db,
                    usuario.id,
                    tx_nueva.id,
                    opcion="parte_de_gasto",
                    gasto_id=tx_cena.id,
                )

    # 12. Pasó por mi cuenta (cada 3 meses el día 5 entran $200.000 del hermano y salen el día 6)
    for year, month in [(2026, 7), (2026, 10), (2027, 1), (2027, 4), (2027, 7)]:
        f_herm_in = date(year, month, 5)
        f_herm_out = date(year, month, 6)
        if f_herm_in <= hoy:
            tx_h_in = db.execute(
                select(Transaccion).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f_herm_in,
                    Transaccion.descripcion == "Transferencia hermano",
                )
            ).scalar_one_or_none()
            if not tx_h_in:
                tx_h_in = transaccion_service.crear_transaccion(
                    db,
                    usuario.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.INGRESO,
                        monto=Decimal("200000.00"),
                        moneda=Moneda.ARS,
                        fecha=f_herm_in,
                        descripcion="Transferencia hermano",
                        categoria_id=cat.c_otros_ing.id,
                        subcategoria_id=cat.s_cobros.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=cat.b_galicia.id,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                )
                que_es_plata_service.responder(
                    db,
                    usuario.id,
                    tx_h_in.id,
                    opcion="paso_por_mi_cuenta",
                )

        if f_herm_out <= hoy:
            tx_h_out = db.execute(
                select(Transaccion).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.fecha == f_herm_out,
                    Transaccion.descripcion == "Envío plata hermano",
                )
            ).scalar_one_or_none()
            if not tx_h_out:
                transaccion_service.crear_transaccion(
                    db,
                    usuario.id,
                    TransaccionCreate(
                        tipo=TipoTransaccion.EGRESO,
                        monto=Decimal("200000.00"),
                        moneda=Moneda.ARS,
                        fecha=f_herm_out,
                        descripcion="Envío plata hermano",
                        categoria_id=cat.c_otros_egr.id,
                        subcategoria_id=cat.s_paso_egr.id,
                        metodo_pago=MetodoPago.TRANSFERENCIA,
                        billetera_id=cat.b_galicia.id,
                        origen=OrigenTransaccion.MANUAL,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    ),
                )

    # 13. Actualizar saldo (controles en Efectivo el último día de cada mes pasado con diferencia -$5.000 a $0)
    for year, month in [
        (2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10), (2026, 11), (2026, 12),
        (2027, 1), (2027, 2), (2027, 3), (2027, 4), (2027, 5), (2027, 6), (2027, 7)
    ]:
        last_d = calendar.monthrange(year, month)[1]
        f_ctrl = date(year, month, last_d)
        if f_ctrl <= hoy:
            existe_ctrl = db.execute(
                select(AjusteSaldo.id).where(
                    AjusteSaldo.billetera_id == cat.b_efectivo.id,
                    AjusteSaldo.fecha == f_ctrl,
                )
            ).first()
            if not existe_ctrl:
                # Gasto que no cargó entre $1.000 y $3.000
                dif = Decimal("2000.00")
                saldo_declarado = (cat.b_efectivo.saldo_actual or Decimal("0.00")) - dif
                saldo_anterior = cat.b_efectivo.saldo_actual
                monto_ajuste = saldo_billetera_service.registrar_control_en_saldo(
                    cat.b_efectivo, saldo_declarado, f_ctrl
                )
                aj = AjusteSaldo(
                    billetera_id=cat.b_efectivo.id,
                    monto=monto_ajuste,
                    saldo_anterior=saldo_anterior,
                    saldo_declarado=saldo_declarado,
                    fecha=f_ctrl,
                    fecha_creacion=fecha_a_utc_12_argentina(f_ctrl),
                )
                db.add(aj)
                db.flush()

    # 14. Pagos de resumen de Visa Galicia ya vencidos hasta hoy
    for cierre, vto in calendario_tarjeta_service.resumenes(cat.t_visa, date(2026, 5, 1), date(2027, 8, 31)):
        if vto <= hoy:
            existe_pago = db.execute(
                select(Transaccion.id).where(
                    Transaccion.usuario_id == usuario.id,
                    Transaccion.tarjeta_id == cat.t_visa.id,
                    Transaccion.pago_resumen_vencimiento == vto,
                )
            ).first()
            if not existe_pago:
                try:
                    pago_resumen_service.pagar_resumen_tarjeta(
                        db=db,
                        usuario_id=usuario.id,
                        tarjeta_id=cat.t_visa.id,
                        fecha_pago=vto,
                        fecha_resumen=vto,
                        billetera_id=cat.b_galicia.id,
                        pesificar=True,
                    )
                except Exception as e:
                    logger.debug("No se pudo pagar resumen a %s: %s", vto, e)

    # 15. Aplicar movimientos restantes hasta hoy
    with _fijar_hoy(hoy):
        saldo_programado_service.aplicar_movimientos_con_fecha_llegada(db)

    # 16. Forzar fecha_creacion = fecha a las 12:00 de Argentina (15:00 UTC) para todas las filas
    _forzar_fechas_creacion(db, usuario.id)

    db.commit()

    return _construir_resumen(db, usuario, cat, hoy)

def _forzar_fechas_creacion(db: Session, usuario_id: UUID) -> None:
    """Corrige fecha_creacion para que sea su fecha a las 12:00 de Argentina."""
    for tx in db.execute(select(Transaccion).where(Transaccion.usuario_id == usuario_id)).scalars():
        tx.fecha_creacion = fecha_a_utc_12_argentina(tx.fecha)
    for tr in db.execute(select(TransferenciaInterna).where(TransferenciaInterna.usuario_id == usuario_id)).scalars():
        tr.fecha_creacion = fecha_a_utc_12_argentina(tr.fecha)
    for aj in db.execute(select(AjusteSaldo).join(Billetera, AjusteSaldo.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario_id)).scalars():
        aj.fecha_creacion = fecha_a_utc_12_argentina(aj.fecha)
    for rb in db.execute(select(RendimientoBilletera).join(Billetera, RendimientoBilletera.billetera_id == Billetera.id).where(Billetera.usuario_id == usuario_id)).scalars():
        f = rb.fecha.date() if isinstance(rb.fecha, datetime) else rb.fecha
        rb.fecha_creacion = fecha_a_utc_12_argentina(f)
    for mm in db.execute(select(MovimientoMeta).join(Meta, MovimientoMeta.meta_id == Meta.id).where(Meta.usuario_id == usuario_id)).scalars():
        mm.fecha_creacion = fecha_a_utc_12_argentina(mm.fecha)
