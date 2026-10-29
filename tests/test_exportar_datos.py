# -*- coding: utf-8 -*-
"""
tests/test_exportar_datos.py — Pruebas de exportación de datos del usuario ("Descargar mis datos").
"""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.auth import get_current_user
from app.core.database import Base, get_db
from app.main import app
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.cuota import Cuota
from app.models.evento_uso import EventoUso
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta, TipoMovimientoMeta
from app.models.presupuesto import EstadoPresupuesto, PeriodoPresupuestoTipo, Presupuesto, RenovacionPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.transaccion import MetodoPago, OrigenTransaccion, TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services import historial_service
from app.services.exportacion_datos_service import formatear_monto_csv
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina


@pytest.fixture(autouse=True)
def setup_sqlite_compat(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)

    import sqlite3
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db", scope="function")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(name="usuario_test")
def usuario_test_fixture(db: Session) -> Usuario:
    usuario = Usuario(
        id=uuid4(),
        email="test_exportar@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)
    return usuario


@pytest.fixture(name="client")
def client_fixture(db: Session, usuario_test: Usuario):
    def override_get_db():
        yield db

    def override_get_current_user():
        return usuario_test

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_formatear_monto_negativo():
    """Un importe negativo se escribe como '-25000,00' (test de la función de formato)."""
    assert formatear_monto_csv(Decimal("-25000.00")) == "-25000,00"
    assert formatear_monto_csv(-25000) == "-25000,00"
    assert formatear_monto_csv(1234.5) == "1234,50"
    assert formatear_monto_csv(0) == "0,00"
    assert formatear_monto_csv(None) == ""


def test_exportar_datos_zip_completo(client: TestClient, db: Session, usuario_test: Usuario):
    """
    Escenario completo de exportación de datos con datos de usuario_test y datos de segundo usuario ajeno.
    """
    hoy = hoy_argentina()

    # Categorías
    cat_gral = Categoria(id=uuid4(), nombre="General", tipo=TipoCategoria.EGRESO)
    cat_ingr = Categoria(id=uuid4(), nombre="Sueldo", tipo=TipoCategoria.INGRESO)
    subcat_gral = Subcategoria(id=uuid4(), categoria_id=cat_gral.id, nombre="Varios")
    db.add_all([cat_gral, cat_ingr, subcat_gral])
    db.commit()

    # 1. Billeteras usuario_test: Galicia en pesos y una segunda billetera
    billetera_galicia = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("150000.00"),
        saldo_inicial=Decimal("150000.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=hoy,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    billetera_secundaria = Billetera(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Ahorro",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("50000.00"),
        saldo_inicial=Decimal("50000.00"),
        es_inversion=True,
        estado=EstadoBilletera.ACTIVA,
        fecha_saldo=hoy,
        fecha_creacion=datetime(2026, 10, 2, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add_all([billetera_galicia, billetera_secundaria])
    db.commit()

    # 2. Transacciones:
    # - Compra de 100.000 con Galicia
    tx_compra = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        categoria_id=cat_gral.id,
        subcategoria_id=subcat_gral.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("100000.00"),
        moneda=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 10, 12, 0, tzinfo=TZ_ARGENTINA),
        descripcion="Compra principal",
        origen=OrigenTransaccion.MANUAL,
    )
    # - Reintegro recibido de 25.000
    tx_reintegro = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        categoria_id=cat_gral.id,
        subcategoria_id=subcat_gral.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("25000.00"),
        moneda=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 11, 12, 0, tzinfo=TZ_ARGENTINA),
        descripcion="Reintegro promo",
        origen=OrigenTransaccion.MANUAL,
        reintegro_de_id=tx_compra.id,
    )
    # - Ingreso
    tx_ingreso = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        categoria_id=cat_ingr.id,
        tipo=TipoTransaccion.INGRESO,
        monto=Decimal("200000.00"),
        moneda=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 5, 12, 0, tzinfo=TZ_ARGENTINA),
        descripcion="Sueldo mensual",
        origen=OrigenTransaccion.MANUAL,
    )
    db.add_all([tx_compra, tx_reintegro, tx_ingreso])
    db.commit()

    # 3. Transferencia a la segunda billetera
    transf = TransferenciaInterna(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_origen_id=billetera_galicia.id,
        billetera_destino_id=billetera_secundaria.id,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 15, 14, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(transf)
    db.commit()

    # 4. Tarjeta y compra en 3 cuotas
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        nombre="Visa Galicia",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=5,
        limite_credito=Decimal("500000.00"),
        moneda=Moneda.ARS,
        percepcion_moneda_extranjera=Decimal("30.00"),
        estado=EstadoTarjeta.ACTIVA,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(tarjeta)
    db.commit()

    tx_padre_id = uuid4()
    grupo_cuotas = GrupoCuotas(
        id=uuid4(),
        usuario_id=usuario_test.id,
        transaccion_padre_id=tx_padre_id,
        tarjeta_id=tarjeta.id,
        descripcion="Smart TV",
        monto_total=Decimal("90000.00"),
        total_financiado=Decimal("90000.00"),
        cantidad_cuotas=3,
        moneda=Moneda.ARS,
        fecha_creacion=datetime(2026, 10, 12, 11, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(grupo_cuotas)
    db.commit()

    # Transacción padre de cuotas
    tx_padre = Transaccion(
        id=tx_padre_id,
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("90000.00"),
        moneda=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 12, 11, 0, tzinfo=TZ_ARGENTINA),
        descripcion="Smart TV",
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_padre_cuotas=True,
        grupo_cuotas_id=grupo_cuotas.id,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add(tx_padre)
    db.commit()

    # Transacciones hijas de cada cuota
    tx_c1 = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 11, 5),
        descripcion="Smart TV (cuota 1/3)",
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_cuota_hija=True,
        grupo_cuotas_id=grupo_cuotas.id,
        origen=OrigenTransaccion.MANUAL,
    )
    tx_c2 = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2026, 12, 5),
        descripcion="Smart TV (cuota 2/3)",
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_cuota_hija=True,
        grupo_cuotas_id=grupo_cuotas.id,
        origen=OrigenTransaccion.MANUAL,
    )
    tx_c3 = Transaccion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        billetera_id=billetera_galicia.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("30000.00"),
        moneda=Moneda.ARS,
        fecha=date(2027, 1, 5),
        descripcion="Smart TV (cuota 3/3)",
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        es_cuota_hija=True,
        grupo_cuotas_id=grupo_cuotas.id,
        origen=OrigenTransaccion.MANUAL,
    )
    db.add_all([tx_c1, tx_c2, tx_c3])
    db.commit()

    c1 = Cuota(
        id=uuid4(),
        grupo_id=grupo_cuotas.id,
        transaccion_id=tx_c1.id,
        numero_cuota=1,
        monto_proyectado=Decimal("30000.00"),
        monto_real=Decimal("30000.00"),
        fecha_vencimiento=date(2026, 11, 5),
        pagada=False,
    )
    c2 = Cuota(
        id=uuid4(),
        grupo_id=grupo_cuotas.id,
        transaccion_id=tx_c2.id,
        numero_cuota=2,
        monto_proyectado=Decimal("30000.00"),
        monto_real=Decimal("30000.00"),
        fecha_vencimiento=date(2026, 12, 5),
        pagada=False,
    )
    c3 = Cuota(
        id=uuid4(),
        grupo_id=grupo_cuotas.id,
        transaccion_id=tx_c3.id,
        numero_cuota=3,
        monto_proyectado=Decimal("30000.00"),
        monto_real=Decimal("30000.00"),
        fecha_vencimiento=date(2027, 1, 5),
        pagada=False,
    )
    db.add_all([c1, c2, c3])
    db.commit()

    # 5. Meta con un aporte
    meta = Meta(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Vacaciones",
        monto_objetivo=Decimal("500000.00"),
        monto_actual=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha_limite=date(2027, 2, 1),
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(meta)
    db.commit()

    mov_meta = MovimientoMeta(
        id=uuid4(),
        meta_id=meta.id,
        billetera_id=billetera_galicia.id,
        monto=Decimal("50000.00"),
        tipo=TipoMovimientoMeta.APORTE,
        moneda_movimiento=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 15, 15, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(mov_meta)
    db.commit()

    # 6. Presupuestos: uno activo y uno eliminado
    pres_activo = Presupuesto(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Gastos Varios",
        monto=Decimal("80000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.ACTIVO,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    pres_eliminado = Presupuesto(
        id=uuid4(),
        usuario_id=usuario_test.id,
        nombre="Presupuesto Viejo",
        monto=Decimal("10000.00"),
        moneda=Moneda.ARS,
        periodo=PeriodoPresupuestoTipo.MENSUAL,
        renovacion=RenovacionPresupuesto.AUTOMATICA,
        estado=EstadoPresupuesto.FINALIZADO,
        fecha_creacion=datetime(2026, 9, 1, 10, 0, tzinfo=TZ_ARGENTINA),
        eliminado_en=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add_all([pres_activo, pres_eliminado])
    db.commit()

    pc_activo = PresupuestoCategoria(
        id=uuid4(),
        presupuesto_id=pres_activo.id,
        categoria_id=cat_gral.id,
        subcategoria_id=subcat_gral.id,
    )
    db.add(pc_activo)
    db.commit()

    # 7. Suscripción con precio
    susc = Suscripcion(
        id=uuid4(),
        usuario_id=usuario_test.id,
        tarjeta_id=tarjeta.id,
        nombre="Netflix",
        frecuencia=FrecuenciaSuscripcion.MENSUAL,
        proximo_cobro=date(2026, 11, 15),
        estado=EstadoSuscripcion.ACTIVA,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    db.add(susc)
    db.commit()

    hist_susc = HistorialSuscripcion(
        id=uuid4(),
        suscripcion_id=susc.id,
        monto=Decimal("6500.00"),
        moneda=Moneda.ARS,
        vigente_desde=date(2026, 10, 1),
    )
    db.add(hist_susc)
    db.commit()

    # 8. Conversaciones WhatsApp: una de hace 10 días y una de hace 100
    conv_10d = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario_test.id,
        fecha=datetime.now(TZ_ARGENTINA) - timedelta(days=10),
        mensaje_usuario="gaste 1000 en cafe",
        transcripcion="gaste mil en cafe",
        mensaje_bot="Listo, anoté $1.000 en Café.",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
    )
    conv_100d = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario_test.id,
        fecha=datetime.now(TZ_ARGENTINA) - timedelta(days=100),
        mensaje_usuario="mensaje muy viejo",
        mensaje_bot="Respuesta vieja",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
    )
    db.add_all([conv_10d, conv_100d])
    db.commit()

    # 9. SEGUNDO USUARIO (Datos ajenos)
    usuario_ajeno = Usuario(
        id=uuid4(),
        email="ajeno@test.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )
    db.add(usuario_ajeno)
    db.commit()

    billetera_ajena = Billetera(
        id=uuid4(),
        usuario_id=usuario_ajeno.id,
        nombre="Ajena",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("99999.00"),
        saldo_inicial=Decimal("99999.00"),
        es_inversion=False,
        estado=EstadoBilletera.ACTIVA,
        fecha_creacion=datetime(2026, 10, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    tx_ajena = Transaccion(
        id=uuid4(),
        usuario_id=usuario_ajeno.id,
        billetera_id=billetera_ajena.id,
        categoria_id=cat_gral.id,
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("50000.00"),
        moneda=Moneda.ARS,
        fecha=hoy,
        fecha_creacion=datetime(2026, 10, 10, 12, 0, tzinfo=TZ_ARGENTINA),
        descripcion="gasto ajeno",
        origen=OrigenTransaccion.MANUAL,
    )
    conv_ajena = ConversacionWpp(
        id=uuid4(),
        usuario_id=usuario_ajeno.id,
        fecha=datetime.now(TZ_ARGENTINA) - timedelta(days=5),
        mensaje_usuario="charla ajena",
        mensaje_bot="bot ajeno",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
    )
    db.add_all([billetera_ajena, tx_ajena, conv_ajena])
    db.commit()

    # --- PETICION AL ENDPOINT ---
    resp = client.get("/usuarios/me/exportar")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    expected_filename = f'attachment; filename="argentum_mis_datos_{hoy:%Y%m%d}.zip"'
    assert resp.headers["content-disposition"] == expected_filename

    # Validar ZIP
    zip_bytes = resp.content
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        nombres = zf.namelist()
        esperados_nombres = [
            "movimientos.csv",
            "billeteras.csv",
            "tarjetas.csv",
            "cuotas.csv",
            "metas.csv",
            "movimientos_metas.csv",
            "presupuestos.csv",
            "suscripciones.csv",
            "mensajes_whatsapp.csv",
        ]
        assert nombres == esperados_nombres

        # Verificar contenido de cada CSV
        csv_data = {}
        for nombre in nombres:
            raw_csv = zf.read(nombre)
            # Debe comenzar con BOM utf-8-sig
            assert raw_csv.startswith(b"\xef\xbb\xbf"), f"{nombre} no tiene BOM"
            texto = raw_csv.decode("utf-8-sig")
            csv_data[nombre] = texto

            # Ninguna línea contiene 'Ajena' ni 'ajeno' ni 'ajena'
            assert "Ajena" not in texto, f"{nombre} contiene 'Ajena'"
            assert "ajeno" not in texto, f"{nombre} contiene 'ajeno'"
            assert "ajena" not in texto, f"{nombre} contiene 'ajena'"

    # Validaciones específicas de negocio:
    # 1. movimientos.csv: filas = total_items de listar_movimientos
    resp_hist = historial_service.listar_movimientos(db, usuario_test.id, modo="general")
    lineas_movs = [line for line in csv_data["movimientos.csv"].split("\r\n") if line.strip()]
    header_movs = lineas_movs[0]
    assert header_movs == "Fecha;Tipo;Descripción;Importe;Moneda;Billetera;Cuenta como;Cuota"
    filas_movs = lineas_movs[1:]
    assert len(filas_movs) == resp_hist.total_items

    # -(suma de Importe de las filas con Cuenta como 'Gasto' y Moneda 'ARS') = totales['ARS'].gastos
    suma_gastos_csv = Decimal("0.00")
    for f in filas_movs:
        cols = f.split(";")
        imp_str = cols[3].replace(",", ".")
        moneda_col = cols[4]
        cuenta_como_col = cols[6]
        if cuenta_como_col == "Gasto" and moneda_col == "ARS":
            suma_gastos_csv += Decimal(imp_str)
    assert -suma_gastos_csv == resp_hist.totales["ARS"].gastos

    # 2. cuotas.csv con 3 filas '1/3', '2/3' y '3/3'
    lineas_cuotas = [line for line in csv_data["cuotas.csv"].split("\r\n") if line.strip()]
    assert lineas_cuotas[0] == "Compra;Tarjeta;Cuota;Monto;Vencimiento;Pagada"
    filas_cuotas = lineas_cuotas[1:]
    assert len(filas_cuotas) == 3
    cuotas_col = [f.split(";")[2] for f in filas_cuotas]
    assert cuotas_col == ["1/3", "2/3", "3/3"]

    # 3. presupuestos.csv sin el eliminado
    lineas_pres = [line for line in csv_data["presupuestos.csv"].split("\r\n") if line.strip()]
    assert lineas_pres[0] == "Nombre;Monto;Moneda;Período;Estado;Categorías"
    assert "Presupuesto Viejo" not in csv_data["presupuestos.csv"]
    assert "Gastos Varios" in csv_data["presupuestos.csv"]

    # 4. mensajes_whatsapp.csv con 1 fila (la de 10 días, no la de 100)
    lineas_wpp = [line for line in csv_data["mensajes_whatsapp.csv"].split("\r\n") if line.strip()]
    assert lineas_wpp[0] == "Fecha y hora;Tu mensaje;Transcripción;Respuesta"
    filas_wpp = lineas_wpp[1:]
    assert len(filas_wpp) == 1
    assert "gaste 1000 en cafe" in filas_wpp[0]
    assert "mensaje muy viejo" not in csv_data["mensajes_whatsapp.csv"]

    # 5. Evento registrado: 1 fila 'descargar_datos' en eventos_uso
    filas_ev = db.query(EventoUso).filter(
        EventoUso.usuario_id == usuario_test.id,
        EventoUso.evento == "descargar_datos",
    ).all()
    assert len(filas_ev) == 1

