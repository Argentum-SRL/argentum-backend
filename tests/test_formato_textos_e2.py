"""
tests/test_formato_textos_e2.py

Verifica el formato exacto de los textos modificados en la Fase 3 E2:
1. Detalle de error de cotización en meta_service.registrar_movimiento.
2. Descripción de pago en pesificación en pago_resumen_service.pagar_resumen_tarjeta.
3. Las cuatro interpretaciones de calcular_perfil_nuevo (usando parámetro data).
4. Los mensajes ARS y USD de _job_proyeccion_negativa.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.billetera import Billetera, EstadoBilletera
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.meta import EstadoMeta, Meta
from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.movimiento_meta import MovimientoMetaCreate
from app.services import meta_service, notificacion_scheduler_service, pago_resumen_service, perfil_financiero_service
from app.services.definiciones_service import ContextoDefiniciones


def test_meta_service_error_cotizacion_formato():
    """Verifica el texto exacto del error de cotización en registrar_movimiento."""
    usuario_id = uuid4()
    meta_id = uuid4()
    billetera_id = uuid4()

    meta = Meta(
        id=meta_id,
        usuario_id=usuario_id,
        nombre="Fondo de Emergencia",
        moneda=Moneda.USD,
        monto_objetivo=Decimal("1000"),
        estado=EstadoMeta.ACTIVA,
    )

    billetera = Billetera(
        id=billetera_id,
        usuario_id=usuario_id,
        nombre="Galicia ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000"),
        estado=EstadoBilletera.ACTIVA,
    )

    db = MagicMock()
    db.get.side_effect = lambda model, oid: meta if model == Meta else (billetera if model == Billetera else None)

    cot_mock_data = {
        "cotizaciones": {
            "blue": {"promedio": 1000.0, "compra": 990.0, "venta": 1010.0}
        }
    }

    with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value=cot_mock_data):
        # Movimiento en ARS con cotización de $500 (difiere 50% de $1.000, supera tolerancia del 30%)
        data = MovimientoMetaCreate(
            tipo="aporte",
            monto=Decimal("50000"),
            moneda_movimiento=Moneda.ARS,
            cotizacion_usada=Decimal("500.00"),
            billetera_id=billetera_id,
            fecha=date.today(),
        )

        with pytest.raises(HTTPException) as exc_info:
            meta_service.registrar_movimiento(db, usuario_id, meta_id, data)

        assert exc_info.value.status_code == 400
        esperado = (
            "La cotización ingresada ($500) difiere más del 30% de la cotización actual ($1.000). "
            "Ingresá un valor entre $700 y $1.300."
        )
        assert exc_info.value.detail == esperado


def test_pago_resumen_descripcion_pesificacion_formato():
    """Verifica el texto exacto de la descripción de pago en pesificación."""
    usuario_id = uuid4()
    tarjeta_id = uuid4()
    billetera_id = uuid4()

    tarjeta = TarjetaCredito(
        id=tarjeta_id,
        usuario_id=usuario_id,
        nombre="Visa Santander 1234",
        dia_cierre=20,
        dia_vencimiento=5,
        estado=EstadoTarjeta.ACTIVA,
        percepcion_moneda_extranjera=Decimal("30.00"),
    )

    billetera = Billetera(
        id=billetera_id,
        usuario_id=usuario_id,
        nombre="Galicia ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("500000"),
        estado=EstadoBilletera.ACTIVA,
    )

    cuota_mock = MagicMock()
    cuota_mock.monto_real = None
    cuota_mock.monto_proyectado = Decimal("150.00")
    cuota_mock.monto_restante = Decimal("150.00")
    cuota_mock.grupo.moneda = Moneda.USD
    cuota_mock.fecha_vencimiento = date(2026, 10, 5)
    cuota_mock.pagada = False

    cat_id = uuid4()
    subcat_id = uuid4()
    categoria_mock = MagicMock(id=cat_id)
    subcategoria_mock = MagicMock(id=subcat_id)

    def mock_query(model):
        q = MagicMock()
        q.join.return_value = q
        q.filter.return_value = q
        q.options.return_value = q
        q.order_by.return_value = q
        name = getattr(model, "__name__", "")
        if model == TarjetaCredito:
            q.first.return_value = tarjeta
        elif model == Billetera:
            q.first.return_value = billetera
        elif name == "Cuota":
            q.all.return_value = [cuota_mock]
            q.first.return_value = None
        elif name == "Categoria":
            q.first.return_value = categoria_mock
        elif name == "Subcategoria":
            q.first.return_value = subcategoria_mock
        else:
            q.all.return_value = []
            q.first.return_value = None
        return q

    db = MagicMock()
    db.query.side_effect = mock_query
    db.get.side_effect = lambda model, oid: tarjeta if model == TarjetaCredito else (billetera if model == Billetera else None)

    cot_oficial = CotizacionDolar(
        fecha=date(2026, 9, 20),
        tipo="oficial",
        promedio=Decimal("1400.00"),
        compra=Decimal("1380.00"),
        venta=Decimal("1420.00"),
    )

    with patch("app.services.dolar_service.obtener_cotizacion_por_fecha", return_value=cot_oficial), \
         patch("app.services.transaccion_service.crear_transaccion") as mock_crear_tx:

        tx_creada = Transaccion(
            id=uuid4(),
            usuario_id=usuario_id,
            billetera_id=billetera_id,
            monto=Decimal("210000.00"),
            moneda=Moneda.ARS,
            tipo=TipoTransaccion.EGRESO,
        )
        mock_crear_tx.return_value = tx_creada

        pago_resumen_service.pagar_resumen_tarjeta(
            db=db,
            usuario_id=usuario_id,
            tarjeta_id=tarjeta_id,
            fecha_resumen=date(2026, 10, 5),
            moneda=Moneda.USD,
            pesificar=True,
            billetera_id=billetera_id,
            cotizacion_personalizada=Decimal("1400.00"),
            commit=False,
        )

        assert mock_crear_tx.call_count >= 1
        primera_llamada = mock_crear_tx.call_args_list[0]
        args, kwargs = primera_llamada
        tx_data = kwargs.get("data") or args[2]
        assert tx_data.descripcion == "Pago resumen 1234 (US$150)"


def test_calcular_perfil_nuevo_cuatro_interpretaciones_formato():
    """Verifica las cuatro interpretaciones relativas de calcular_perfil_nuevo con data."""
    usuario = Usuario(
        id=uuid4(),
        email="test_perfil_formato@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        ciclo_tipo=CicloTipo.REGLA,
        ciclo_valor="dia_1",
    )

    hoy = date(2026, 4, 1)

    txs = []
    fechas_ciclos = [
        date(2025, 12, 10),
        date(2026, 1, 10),
        date(2026, 2, 10),
        date(2026, 3, 10),
    ]

    for f in fechas_ciclos:
        tx_ing = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            fecha=f,
            tipo=TipoTransaccion.INGRESO,
            monto=Decimal("1000000"),
            moneda=Moneda.ARS,
            descripcion="Sueldo",
        )
        tx_egr = Transaccion(
            id=uuid4(),
            usuario_id=usuario.id,
            fecha=f,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("500000"),
            moneda=Moneda.ARS,
            descripcion="Gastos del mes",
        )
        txs.extend([tx_ing, tx_egr])

    ipc_dict = {
        "2025-12": Decimal("100"),
        "2026-01": Decimal("100"),
        "2026-02": Decimal("100"),
        "2026-03": Decimal("100"),
        "2026-04": Decimal("100"),
    }

    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Banco",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1500000"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
    )

    cuota_mock = MagicMock()
    cuota_mock.monto_proyectado = Decimal("50000")
    cuota_mock.moneda = Moneda.ARS
    cuota_mock.fecha_vencimiento = date(2026, 5, 1)
    cuota_mock.pagada = False

    ctx = ContextoDefiniciones(
        grupos_cuotas_cantidades={},
        billeteras_inversion_ids=set(),
        categoria_ahorro_ids=set(),
        subcategoria_tarjeta_id=None,
        subcategoria_tarjeta_ids=set(),
        hoy=hoy,
    )

    data = {
        "hoy": hoy,
        "txs": txs,
        "ipc": ipc_dict,
        "cuotas": [(cuota_mock, None)],
        "suscripciones": [],
        "historial_subs": [],
        "billeteras": [billetera],
        "ctx": ctx,
    }

    mock_db = MagicMock()

    def mock_execute(stmt):
        s = str(stmt)
        res = MagicMock()
        if "meta" in s.lower():
            res.scalars.return_value = []
        else:
            res.scalars.return_value = [billetera]
        return res

    mock_db.execute.side_effect = mock_execute

    mock_stream = MagicMock(clase="HABITO", moneda=Moneda.ARS, estado="MADURO")

    with patch("app.services.perfil_financiero_service.ciclos_anteriores", return_value=[
        (date(2025, 12, 1), date(2025, 12, 31)),
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 28)),
        (date(2026, 3, 1), date(2026, 3, 31)),
    ]), \
         patch("app.services.perfil_financiero_service.obtener_ingreso_habitual", return_value=MagicMock(monto=Decimal("1000000"))), \
         patch("app.services.perfil_financiero_service.calcular_compromisos_memoria", return_value=MagicMock(total=Decimal("50000"))), \
         patch("app.services.perfil_financiero_service.clasificar_gastos") as mock_clasif, \
         patch("app.services.perfil_financiero_service.monto_mensual_deflactado_stream", return_value=Decimal("30000")), \
         patch("app.services.perfil_financiero_service.mad", return_value=Decimal("100000")):

        mock_clasif.return_value = MagicMock(
            comprometidos=set(),
            habitos=set(),
            variables=set(txs),
            streams=[mock_stream],
        )

        perfil = perfil_financiero_service.calcular_perfil_nuevo(mock_db, usuario, data=data)

    interp = perfil["interpretaciones_relativas"]

    # 1. Gasto comprometido
    assert "gasto_comprometido" in interp
    assert interp["gasto_comprometido"] == "Demanda el 5.0% de tu ingreso típico mensual ($50.000 / mes en cuotas, suscripciones y gastos fijos)."

    # 2. Gasto en hábitos
    assert "gasto_habitos" in interp
    assert interp["gasto_habitos"] == "Representa el 3.0% de tu ingreso típico mensual ($30.000 / mes en consumos habituales elegibles)."

    # 3. Runway
    assert "runway" in interp
    assert interp["runway"] == "Tu liquidez actual ($1.500.000) cubre 3.0 meses de tu gasto típico mensual deflactado ($500.000/mes)."

    # 4. Volatilidad
    assert "volatilidad" in interp
    assert interp["volatilidad"] == "Tus gastos variables fluctúan típicamente un ±20.0% respecto de tu mediana mensual ($500.000)."


def test_job_proyeccion_negativa_mensajes_ars_y_usd():
    """Verifica los textos de los mensajes en ARS y USD de _job_proyeccion_negativa."""
    usuario_id = uuid4()
    usuario = Usuario(
        id=usuario_id,
        nombre="Sebastián",
        telefono="+5491112345678",
        telefono_verificado=True,
    )

    mock_db = MagicMock()
    mock_db.execute.return_value.scalars.return_value.all.return_value = [usuario_id]
    mock_db.get.return_value = usuario

    proyecciones = {
        "ars": {
            "datos_suficientes": True,
            "calibracion": {"pasa_puerta": True},
            "balance_proyectado": Decimal("-123456.78"),
        },
        "usd": {
            "datos_suficientes": True,
            "calibracion": {"pasa_puerta": True},
            "balance_proyectado": Decimal("-150.50"),
        },
    }

    mensajes_creados = []

    def mock_crear_notif(**kwargs):
        mensajes_creados.append(kwargs)
        return MagicMock()

    with patch("app.services.notificacion_scheduler_service.intentar_tomar_lock_job", return_value=True), \
         patch("app.services.notificacion_scheduler_service.liberar_lock_job"), \
         patch("app.services.proyeccion_service.calcular_proyeccion", return_value=proyecciones), \
         patch("app.services.dashboard_service.get_ciclo_fechas", return_value=(date(2026, 10, 1), date(2026, 10, 31))), \
         patch("app.services.notificacion_scheduler_service.crear_notificacion", side_effect=mock_crear_notif), \
         patch("app.services.notificacion_service.obtener_configuracion"), \
         patch("app.services.notificacion_service.resolver_canales_notificacion", return_value=(True, True)):

        notificacion_scheduler_service._job_proyeccion_negativa(lambda: mock_db)

    assert len(mensajes_creados) == 2
    msg_ars = mensajes_creados[0]["mensaje"]
    msg_usd = mensajes_creados[1]["mensaje"]

    esperado_ars = "¡Atención! Estimamos que tu saldo en pesos va a terminar este ciclo en negativo por $123.457. Te sugerimos revisar tus gastos."
    esperado_usd = "¡Atención! Estimamos que tu saldo en dólares va a terminar este ciclo en negativo por US$150,50. Te sugerimos revisar tus gastos."

    assert msg_ars == esperado_ars
    assert msg_usd == esperado_usd
