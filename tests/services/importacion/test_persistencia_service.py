import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

# Override JSONB type compilation for SQLite so that the test schema constructs successfully
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, AuthProvider, RolUsuario, EstadoUsuario, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta, EstadoTarjeta
from app.models.importacion import ImportacionResumen, EstadoImportacion, CorreccionImportacion, TipoCorreccion
from app.models.transaccion import Transaccion, TipoTransaccion, EstadoVerificacionTransaccion, OrigenTransaccion, MetodoPago
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.cuota import Cuota
from app.services.importacion.schemas import TransaccionCruda
from app.services.importacion import persistencia_service


@pytest.fixture(name="db")
def db_fixture():
    """
    Inicializa una base de datos SQLite en memoria con el esquema completo y semillas
    para realizar pruebas de integración de base de datos aisladas.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    
    # 1. Crear Usuario Semilla
    usuario = Usuario(
        id=uuid4(),
        nombre="Juan",
        apellido="Perez",
        email="juan.perez@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS
    )
    session.add(usuario)
    session.flush()
    
    # 2. Crear Billetera en Pesos (ARS)
    billetera_ars = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("10000.00"),
        saldo_inicial=Decimal("10000.00"),
        estado=EstadoBilletera.ACTIVA
    )
    session.add(billetera_ars)
    session.flush()
    
    # 3. Crear Billetera en Dólares (USD)
    billetera_usd = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera USD",
        moneda=Moneda.USD,
        saldo_actual=Decimal("500.00"),
        saldo_inicial=Decimal("500.00"),
        estado=EstadoBilletera.ACTIVA
    )
    session.add(billetera_usd)
    session.flush()
    
    # 4. Crear Tarjeta de Crédito Semilla
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera_ars.id,
        nombre="Visa Galicia",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=30,
        limite_credito=Decimal("50000.00"),
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA
    )
    session.add(tarjeta)
    session.flush()
    
    # 5. Crear Resumen de Importación de control
    importacion = ImportacionResumen(
        id=uuid4(),
        usuario_id=usuario.id,
        admin_id=usuario.id,
        tarjeta_id=tarjeta.id,
        banco_detectado="galicia",
        nombre_archivo="resumen_julio_2026.pdf",
        estado=EstadoImportacion.PROCESANDO,
        total_detectadas=0,
        total_importadas=0,
        total_duplicadas=0
    )
    session.add(importacion)
    session.commit()
    
    # Exponer las variables útiles agregándolas dinámicamente al objeto sesión del test
    session.usuario_id = usuario.id
    session.tarjeta_id = tarjeta.id
    session.billetera_id = billetera_ars.id
    session.billetera_usd_id = billetera_usd.id
    session.importacion_id = importacion.id
    
    try:
        yield session
    finally:
        session.close()


def test_normalizar_descripcion():
    """
    Verifica que la normalización de descripciones limpie y colapse espacios y sufijos de cuotas correctamente.
    """
    assert persistencia_service.normalizar_descripcion("  COMPRA DIA 3/12  ") == "compra dia"
    assert persistencia_service.normalizar_descripcion("Supermercado Coto 02/06") == "supermercado coto"
    assert persistencia_service.normalizar_descripcion("Coto 4 de 12") == "coto"
    assert persistencia_service.normalizar_descripcion("compra normal sin cuota") == "compra normal sin cuota"
    assert persistencia_service.normalizar_descripcion("") == ""


def test_calcular_import_hash():
    """
    Verifica que el cálculo de hash unívoco sea consistente y maneje el valor absoluto del monto.
    """
    uid = uuid4()
    tid = uuid4()
    fecha_tx = date(2026, 7, 8)
    
    hash_1 = persistencia_service.calcular_import_hash(uid, tid, fecha_tx, Decimal("-150.50"), "COMPRA DIA 3/12", 3)
    # Misma transacción pero con signo positivo (ej: reversión vs consumo), misma descripción sin limpiar
    hash_2 = persistencia_service.calcular_import_hash(uid, tid, fecha_tx, Decimal("150.50"), "compra dia", 3)
    
    assert hash_1 == hash_2
    assert len(hash_1) == 64


def test_importar_transacciones_simples(db):
    """
    Prueba que se importen correctamente cargos comunes y reversiones (monto negativo -> ingreso).
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Compra en Dia %",
            monto=Decimal("1500.50"),
            moneda="ARS"
        ),
        TransaccionCruda(
            fecha=date(2026, 7, 9),
            descripcion="Reversion Cargo",
            monto=Decimal("-250.00"),  # Reversión (monto negativo)
            moneda="ARS"
        )
    ]
    
    resultado = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas
    )
    
    assert resultado["importadas"] == 2
    assert resultado["duplicadas"] == 0
    assert resultado["total_procesadas"] == 2
    
    # Verificar persistencia en base de datos
    txs = db.query(Transaccion).filter(Transaccion.importacion_id == db.importacion_id).all()
    assert len(txs) == 2
    
    # La compra normal debe ser un egreso
    tx_compra = next(t for t in txs if t.descripcion == "Compra en Dia %")
    assert tx_compra.tipo == TipoTransaccion.EGRESO
    assert tx_compra.monto == Decimal("1500.50")
    assert tx_compra.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert tx_compra.origen == OrigenTransaccion.IA_PDF
    
    # La reversión debe ser un ingreso con monto positivo en la base de datos
    tx_reversion = next(t for t in txs if t.descripcion == "Reversion Cargo")
    assert tx_reversion.tipo == TipoTransaccion.INGRESO
    assert tx_reversion.monto == Decimal("250.00")
    assert tx_reversion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA


def test_importar_transacciones_duplicadas(db):
    """
    Verifica que la deduplicación por hash evite la inserción de transacciones repetidas en la base de datos.
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Pago de Servicio Gas",
            monto=Decimal("3000.00"),
            moneda="ARS"
        )
    ]
    
    # Primera importación
    resultado_1 = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas
    )
    assert resultado_1["importadas"] == 1
    assert resultado_1["duplicadas"] == 0
    
    # Segunda importación del mismo lote
    resultado_2 = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas
    )
    assert resultado_2["importadas"] == 0
    assert resultado_2["duplicadas"] == 1
    
    # Comprobar que en base de datos haya exactamente 1 fila
    count = db.query(Transaccion).filter(Transaccion.usuario_id == db.usuario_id, Transaccion.descripcion == "Pago de Servicio Gas").count()
    assert count == 1


def test_importar_cuota_inicial(db):
    """
    Valida la creación de un nuevo grupo de cuotas y transacciones hijas
    cuando llega la cuota_actual = 1 de una compra financiada.
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Celular Movistar 1/3",
            monto=Decimal("15000.00"),
            moneda="ARS",
            cuota_actual=1,
            cuota_total=3
        )
    ]
    
    resultado = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas
    )
    
    assert resultado["importadas"] == 1
    
    # 1. Comprobar que existe la transacción padre por el monto total ($45.000)
    tx_padre = db.query(Transaccion).filter(
        Transaccion.usuario_id == db.usuario_id,
        Transaccion.es_padre_cuotas == True
    ).first()
    assert tx_padre is not None
    assert tx_padre.monto == Decimal("45000.00")
    
    # 2. Comprobar que se creó el grupo de cuotas correspondiente
    grupo = db.query(GrupoCuotas).filter(GrupoCuotas.transaccion_padre_id == tx_padre.id).first()
    assert grupo is not None
    assert grupo.cantidad_cuotas == 3
    assert grupo.monto_total == Decimal("45000.00")
    
    # 3. Comprobar que se generaron las 3 cuotas en el plan
    cuotas = db.query(Cuota).filter(Cuota.grupo_id == grupo.id).all()
    assert len(cuotas) == 3
    
    # 4. Verificar que la cuota 1 está confirmada y pagada, y tiene el hash
    cuota_1 = next(c for c in cuotas if c.numero_cuota == 1)
    assert cuota_1.pagada is True
    assert cuota_1.monto_real == Decimal("15000.00")
    assert cuota_1.transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert cuota_1.transaccion.import_hash is not None
    
    # 5. Verificar que las cuotas 2 y 3 permanecen como PENDIENTE
    cuota_2 = next(c for c in cuotas if c.numero_cuota == 2)
    assert cuota_2.pagada is False
    assert cuota_2.transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE


def test_importar_cuota_intermedia_sin_grupo_previo(db):
    """
    Valida la importación de una cuota intermedia (ej. 3 de 6) cuando NO existe grupo
    previo registrado. Debe generar el grupo completo con cuota_inicial = 3
    (solo persistiendo cuotas de la 3 a la 6).
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Mesa de Comedor 3/6",
            monto=Decimal("8000.00"),
            moneda="ARS",
            cuota_actual=3,
            cuota_total=6
        )
    ]
    
    resultado = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas
    )
    
    assert resultado["importadas"] == 1
    
    # Verificar grupo
    grupo = db.query(GrupoCuotas).filter(
        GrupoCuotas.usuario_id == db.usuario_id,
        GrupoCuotas.cantidad_cuotas == 6
    ).first()
    assert grupo is not None
    
    # Dado que cuota_inicial = 3, sólo deben haberse generado las cuotas: 3, 4, 5 y 6 (4 cuotas en total)
    cuotas = db.query(Cuota).filter(Cuota.grupo_id == grupo.id).all()
    assert len(cuotas) == 4
    
    numeros_cuota = {c.numero_cuota for c in cuotas}
    assert numeros_cuota == {3, 4, 5, 6}
    
    # Cuota 3 debe estar pagada y confirmada
    cuota_3 = next(c for c in cuotas if c.numero_cuota == 3)
    assert cuota_3.pagada is True
    assert cuota_3.monto_real == Decimal("8000.00")
    assert cuota_3.transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert cuota_3.transaccion.import_hash is not None
    
    # Cuota 4 debe estar pendiente
    cuota_4 = next(c for c in cuotas if c.numero_cuota == 4)
    assert cuota_4.pagada is False
    assert cuota_4.transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE


def test_importar_cuota_intermedia_con_grupo_existente(db):
    """
    Prueba que si ya existe un plan de financiamiento en base de datos y se importa
    la cuota posterior (ej. cuota 4 de 6), el servicio vincule la cuota existente
    actualizando su monto_real y estado a confirmado, sin duplicar grupos o transacciones padres.
    """
    # 1. Crear previamente el plan de cuotas importando primero la cuota 3
    crudas_3 = [
        TransaccionCruda(
            fecha=date(2026, 6, 8),
            descripcion="Mesa de Comedor 3/6",
            monto=Decimal("8000.00"),
            moneda="ARS",
            cuota_actual=3,
            cuota_total=6
        )
    ]
    persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas_3
    )
    
    # Comprobar estado inicial del grupo
    grupo_inicial = db.query(GrupoCuotas).first()
    assert grupo_inicial is not None
    cuotas_iniciales = db.query(Cuota).filter(Cuota.grupo_id == grupo_inicial.id).all()
    assert len(cuotas_iniciales) == 4
    
    cuota_4_inicial = next(c for c in cuotas_iniciales if c.numero_cuota == 4)
    assert cuota_4_inicial.pagada is False
    assert cuota_4_inicial.monto_real is None
    assert cuota_4_inicial.transaccion.estado_verificacion == EstadoVerificacionTransaccion.PENDIENTE
    assert cuota_4_inicial.transaccion.import_hash is None
    
    # 2. Importar cuota 4 (con descripción algo distinta que se normaliza igual, ej: "Mesa De Comedor 4/6")
    crudas_4 = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Mesa De Comedor 4/6",
            monto=Decimal("8200.00"),  # Varió ligeramente el monto
            moneda="ARS",
            cuota_actual=4,
            cuota_total=6
        )
    ]
    
    resultado = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas_4
    )
    
    assert resultado["importadas"] == 1
    
    # Comprobar que no se crearon nuevos grupos
    assert db.query(GrupoCuotas).count() == 1
    
    # Verificar que la cuota 4 se actualizó correctamente
    db.refresh(cuota_4_inicial)
    assert cuota_4_inicial.pagada is True
    assert cuota_4_inicial.monto_real == Decimal("8200.00")
    
    # La transacción de la cuota 4 ahora debe estar confirmada con su hash correspondiente
    tx_hija = cuota_4_inicial.transaccion
    assert tx_hija.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA
    assert tx_hija.monto == Decimal("8200.00")
    assert tx_hija.fecha == date(2026, 7, 30)
    assert tx_hija.import_hash is not None


def test_importar_usd_sin_billetera_usd(db):
    """
    Verifica que si llega una transacción en dólares pero el parámetro billetera_usd_id
    es None, el registro sea omitido e incrementado en el contador correspondiente.
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Netflix USD",
            monto=Decimal("15.99"),
            moneda="USD"
        )
    ]
    
    resultado = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=None,  # No se provee billetera USD
        transacciones_crudas=crudas
    )
    
    assert resultado["importadas"] == 0
    assert resultado["sin_billetera_usd"] == 1
    assert resultado["total_procesadas"] == 1
    
    # Validar que no se haya insertado en la base de datos
    count = db.query(Transaccion).filter(Transaccion.usuario_id == db.usuario_id).count()
    assert count == 0


def test_importar_usd_con_billetera_usd(db):
    """
    Verifica que si se provee billetera_usd_id, la transacción en dólares se guarde
    correctamente utilizando dicho identificador de billetera.
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Spotify USD",
            monto=Decimal("5.99"),
            moneda="USD"
        )
    ]
    
    resultado = persistencia_service.importar_transacciones_resumen(
        db=db,
        usuario_id=db.usuario_id,
        tarjeta_id=db.tarjeta_id,
        importacion_id=db.importacion_id,
        billetera_id=db.billetera_id,
        billetera_usd_id=db.billetera_usd_id,
        transacciones_crudas=crudas
    )
    
    assert resultado["importadas"] == 1
    
    tx = db.query(Transaccion).filter(Transaccion.descripcion == "Spotify USD").first()
    assert tx is not None
    assert tx.billetera_id == db.billetera_usd_id
    assert tx.moneda == Moneda.USD
    assert tx.monto == Decimal("5.99")


def test_importar_fallo_transaccional_completo(db):
    """
    Verifica que si ocurre una excepción inesperada (por ejemplo, clave duplicada en la base)
    a mitad del procesamiento, se realice rollback completo de todos los cambios de base de datos
    y el ImportacionResumen quede guardado en estado de ERROR con mensaje legible.
    """
    crudas = [
        TransaccionCruda(
            fecha=date(2026, 7, 8),
            descripcion="Compra Exito 1",
            monto=Decimal("100.00"),
            moneda="ARS"
        ),
        # La segunda transacción llama a crear_cuotas, la cual mockearemos para que falle
        TransaccionCruda(
            fecha=date(2026, 7, 9),
            descripcion="Compra Fallida 1/3",
            monto=Decimal("200.00"),
            moneda="ARS",
            cuota_actual=1,
            cuota_total=3
        )
    ]
    
    from unittest.mock import patch
    with patch("app.services.cuotas_service.crear_cuotas", side_effect=ValueError("Simulated database error")):
        with pytest.raises(Exception):
            persistencia_service.importar_transacciones_resumen(
                db=db,
                usuario_id=db.usuario_id,
                tarjeta_id=db.tarjeta_id,
                importacion_id=db.importacion_id,
                billetera_id=db.billetera_id,
                billetera_usd_id=db.billetera_usd_id,
                transacciones_crudas=crudas
            )
        
    # Verificar que NO se persistió la primera transacción exitosa ("Compra Exito 1") por el rollback completo
    compra_1 = db.query(Transaccion).filter(Transaccion.descripcion == "Compra Exito 1").first()
    assert compra_1 is None
    
    # Verificar que el estado de la importación quedó en ERROR
    importacion = db.query(ImportacionResumen).filter(ImportacionResumen.id == db.importacion_id).first()
    assert importacion.estado == EstadoImportacion.ERROR
    assert importacion.mensaje_error is not None
    assert "Error en la persistencia del lote" in importacion.mensaje_error


def test_registrar_correccion_anonimizada(db):
    """
    Verifica que la función registrar_correccion guarde correctamente el registro en la tabla
    CorreccionImportacion y que esta no contenga ningún dato de texto libre de la transacción original.
    """
    persistencia_service.registrar_correccion(
        db=db,
        importacion_id=db.importacion_id,
        banco="galicia",
        capa_parser_usada="deterministic",
        tipo_correccion=TipoCorreccion.CATEGORIA_CAMBIADA
    )
    
    # Comprobar inserción
    corr = db.query(CorreccionImportacion).filter(CorreccionImportacion.importacion_id == db.importacion_id).first()
    assert corr is not None
    assert corr.banco == "galicia"
    assert corr.capa_parser_usada == "deterministic"
    assert corr.tipo_correccion == TipoCorreccion.CATEGORIA_CAMBIADA
    
    # Asegurar que el modelo no contiene campos para montos, descripciones o PII
    assert not hasattr(corr, "monto")
    assert not hasattr(corr, "descripcion")
    assert not hasattr(corr, "monto_real")
