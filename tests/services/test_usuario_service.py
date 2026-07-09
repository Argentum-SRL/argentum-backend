import pytest
from decimal import Decimal
from datetime import date
from uuid import uuid4

from sqlalchemy import create_engine, select
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
from app.services.usuario_service import eliminar_usuario


@pytest.fixture(name="db")
def db_fixture():
    """
    Inicializa una base de datos SQLite en memoria con el esquema completo
    para realizar pruebas del servicio de usuario.
    """
    engine = create_engine("sqlite:///:memory:")
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
    Base.metadata.create_all(bind=engine)
    
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_eliminar_usuario_cascada_importaciones(db):
    """
    Verifica que al eliminar un usuario se eliminen también sus importaciones
    y las correcciones de importación asociadas (cascada).
    """
    # 1. Crear Usuario
    usuario = Usuario(
        id=uuid4(),
        nombre="Test",
        apellido="User",
        email="test@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS
    )
    db.add(usuario)
    db.flush()

    # 2. Crear Billetera
    billetera = Billetera(
        id=uuid4(),
        usuario_id=usuario.id,
        nombre="Billetera ARS",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000.00"),
        saldo_inicial=Decimal("1000.00"),
        estado=EstadoBilletera.ACTIVA
    )
    db.add(billetera)
    db.flush()

    # 3. Crear Tarjeta
    tarjeta = TarjetaCredito(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        nombre="Visa Galicia",
        red=RedTarjeta.VISA,
        dia_cierre=20,
        dia_vencimiento=30,
        limite_credito=Decimal("50000.00"),
        moneda=Moneda.ARS,
        estado=EstadoTarjeta.ACTIVA
    )
    db.add(tarjeta)
    db.flush()

    # 4. Crear ImportacionResumen
    importacion = ImportacionResumen(
        id=uuid4(),
        usuario_id=usuario.id,
        admin_id=usuario.id,
        tarjeta_id=tarjeta.id,
        banco_detectado="galicia",
        nombre_archivo="resumen.pdf",
        estado=EstadoImportacion.PENDIENTE_REVISION,
        total_detectadas=1
    )
    db.add(importacion)
    db.flush()

    # 5. Crear CorreccionImportacion
    correccion = CorreccionImportacion(
        id=uuid4(),
        importacion_id=importacion.id,
        banco="galicia",
        capa_parser_usada="galicia_v1",
        tipo_correccion=TipoCorreccion.CATEGORIA_CAMBIADA
    )
    db.add(correccion)
    
    # 6. Crear Transaccion vinculada a la importacion
    transaccion = Transaccion(
        id=uuid4(),
        usuario_id=usuario.id,
        billetera_id=billetera.id,
        monto=Decimal("150.00"),
        moneda=Moneda.ARS,
        descripcion="Compra super",
        fecha=date.today(),
        tipo=TipoTransaccion.EGRESO,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        origen=OrigenTransaccion.IA_PDF,
        metodo_pago=MetodoPago.CREDITO,
        tarjeta_id=tarjeta.id,
        importacion_id=importacion.id
    )
    db.add(transaccion)
    db.commit()

    # Guardar los IDs para evitar acceder a atributos expirados de objetos eliminados
    usuario_id = usuario.id
    importacion_id = importacion.id
    correccion_id = correccion.id
    transaccion_id = transaccion.id
    tarjeta_id = tarjeta.id
    billetera_id = billetera.id

    # Verificar que existan en la DB antes de borrar
    assert db.query(Usuario).filter(Usuario.id == usuario_id).first() is not None
    assert db.query(ImportacionResumen).filter(ImportacionResumen.id == importacion_id).first() is not None
    assert db.query(CorreccionImportacion).filter(CorreccionImportacion.id == correccion_id).first() is not None
    assert db.query(Transaccion).filter(Transaccion.id == transaccion_id).first() is not None

    # Eliminar al usuario
    eliminar_usuario(db, usuario)

    # Verificar que todo haya sido borrado
    assert db.query(Usuario).filter(Usuario.id == usuario_id).first() is None
    assert db.query(ImportacionResumen).filter(ImportacionResumen.id == importacion_id).first() is None
    assert db.query(CorreccionImportacion).filter(CorreccionImportacion.id == correccion_id).first() is None
    assert db.query(Transaccion).filter(Transaccion.id == transaccion_id).first() is None
    assert db.query(TarjetaCredito).filter(TarjetaCredito.id == tarjeta_id).first() is None
    assert db.query(Billetera).filter(Billetera.id == billetera_id).first() is None


def test_eliminar_usuario_admin_reassignment(db):
    """
    Verifica que al eliminar un usuario que figura como admin_id en una importación
    de otro usuario, no se elimine la importación del otro usuario sino que
    se reasigne su admin_id al propio usuario_id (dueño).
    """
    # 1. Crear Usuario Admin (el que se eliminará)
    admin = Usuario(
        id=uuid4(),
        nombre="Admin",
        apellido="User",
        email="admin@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS
    )
    db.add(admin)
    db.flush()

    # 2. Crear Usuario Normal (dueño de la importación)
    normal = Usuario(
        id=uuid4(),
        nombre="Normal",
        apellido="User",
        email="normal@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS
    )
    db.add(normal)
    db.flush()

    # 3. Crear ImportacionResumen del usuario normal, ejecutada por el admin
    importacion = ImportacionResumen(
        id=uuid4(),
        usuario_id=normal.id,
        admin_id=admin.id,
        banco_detectado="galicia",
        nombre_archivo="resumen.pdf",
        estado=EstadoImportacion.PENDIENTE_REVISION,
        total_detectadas=0
    )
    db.add(importacion)
    db.commit()

    # Guardar los IDs para evitar acceder a atributos expirados de objetos eliminados
    admin_id = admin.id
    normal_id = normal.id
    importacion_id = importacion.id

    # Verificar que existan en la DB
    assert db.query(Usuario).filter(Usuario.id == admin_id).first() is not None
    assert db.query(Usuario).filter(Usuario.id == normal_id).first() is not None
    
    imp_db = db.query(ImportacionResumen).filter(ImportacionResumen.id == importacion_id).first()
    assert imp_db is not None
    assert imp_db.admin_id == admin_id

    # Eliminar al usuario admin
    eliminar_usuario(db, admin)

    # Verificar que el admin se borró, el usuario normal sigue existiendo
    assert db.query(Usuario).filter(Usuario.id == admin_id).first() is None
    assert db.query(Usuario).filter(Usuario.id == normal_id).first() is not None

    # Verificar que la importación del usuario normal no se borró, pero su admin_id se actualizó a normal.id
    db.expire_all()
    imp_db = db.query(ImportacionResumen).filter(ImportacionResumen.id == importacion_id).first()
    assert imp_db is not None
    assert imp_db.admin_id == normal_id


def test_actualizar_datos_personales_edad(db):
    """
    Verifica que al actualizar los datos personales, se rechace la fecha
    de nacimiento si corresponde a una persona menor de 18 años, y se acepte si es 18+.
    """
    from datetime import date, timedelta
    from fastapi import HTTPException
    from app.schemas.usuario import EditarDatosPersonales
    from app.models.usuario import Sexo
    from app.services.usuario_service import actualizar_datos_personales

    usuario = Usuario(
        id=uuid4(),
        nombre="Juan",
        apellido="Perez",
        email="juan@example.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS
    )
    db.add(usuario)
    db.commit()

    # 1. Intentar actualizar con una edad menor de 18 años (17 años)
    fecha_menor = date.today() - timedelta(days=17 * 365 + 10)
    datos_menor = EditarDatosPersonales(
        nombre="Juan",
        apellido="Perez",
        fecha_nacimiento=fecha_menor,
        sexo=Sexo.MASCULINO
    )

    with pytest.raises(HTTPException) as exc_info:
        actualizar_datos_personales(db, usuario, datos_menor)
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Tenés que ser mayor de 18 años para crear una cuenta en Argentum"

    # 2. Intentar actualizar con una edad de 18 años o más (19 años)
    fecha_mayor = date.today() - timedelta(days=19 * 365)
    datos_mayor = EditarDatosPersonales(
        nombre="Juan",
        apellido="Perez",
        fecha_nacimiento=fecha_mayor,
        sexo=Sexo.MASCULINO
    )

    actualizar_datos_personales(db, usuario, datos_mayor)
    db.refresh(usuario)
    assert usuario.fecha_nacimiento == fecha_mayor
