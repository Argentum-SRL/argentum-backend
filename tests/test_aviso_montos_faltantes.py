"""
tests/test_aviso_montos_faltantes.py — Tests con IA simulada (mock) para reintento y aviso de montos faltantes (Decisión C).
"""
import pytest
from unittest.mock import patch, MagicMock
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.usuario import Usuario, Moneda, RolUsuario, EstadoUsuario, AuthProvider
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.routers.whatsapp_ia import _procesar_mensaje_whatsapp_background


@pytest.fixture(name="db_session", scope="function")
def db_session_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    with patch("app.routers.whatsapp_ia.SessionLocal", side_effect=TestingSession):
        try:
            yield session
        finally:
            session.close()


@pytest.fixture
def mock_contexto_db(db_session):
    u = Usuario(
        id=uuid4(),
        email="test_aviso_c@argentum.com",
        nombre="Test",
        apellido="Aviso",
        telefono="+5491100001111",
        telefono_normalizado="1100001111",
        moneda_principal=Moneda.ARS,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        telefono_verificado=True,
    )
    b = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Galicia",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("1000000"),
        saldo_inicial=Decimal("1000000"),
        es_principal=True,
        estado=EstadoBilletera.ACTIVA,
    )
    cat1 = Categoria(id=uuid4(), nombre="Transporte", tipo=TipoCategoria.EGRESO)
    sub1 = Subcategoria(id=uuid4(), categoria_id=cat1.id, nombre="Taxi / Apps")
    cat2 = Categoria(id=uuid4(), nombre="Supermercado", tipo=TipoCategoria.EGRESO)
    sub2 = Subcategoria(id=uuid4(), categoria_id=cat2.id, nombre="Supermercado")
    cat3 = Categoria(id=uuid4(), nombre="Otros", tipo=TipoCategoria.EGRESO)
    sub3 = Subcategoria(id=uuid4(), categoria_id=cat3.id, nombre="Otros")

    db_session.add_all([u, b, cat1, sub1, cat2, sub2, cat3, sub3])
    db_session.commit()
    return u, b


def test_reintento_exitoso_completa_todos_los_montos(db_session, mock_contexto_db):
    """Caso 1: la IA primero devuelve 2 ítems, el reintento devuelve los 3 y NO hay aviso de faltantes."""
    u, b = mock_contexto_db
    msg_texto = "gasté 2.900 pesos en Uber y 16.900 pesos de los chinos. Mi mamá me transfirió 300.000 pesos"

    resp_1 = {
        "intent": "registrar_transaccion",
        "confianza": 0.95,
        "slot_filling": False,
        "entidades": {
            "monto": 2900.0,
            "tipo": "egreso",
            "categoria": "Taxi / Apps",
            "billetera": "Galicia",
            "billetera_origen": "Galicia",
            "transacciones_adicionales": [
                {"monto": 16900.0, "tipo": "egreso", "categoria": "Supermercado", "billetera": "Galicia"}
            ],
        },
        "respuesta_usuario": "Listo. $2.900 en Uber y $16.900 en Supermercado desde Galicia — registrado.",
    }

    resp_2 = {
        "intent": "registrar_transaccion",
        "confianza": 0.95,
        "slot_filling": False,
        "entidades": {
            "monto": 2900.0,
            "tipo": "egreso",
            "categoria": "Taxi / Apps",
            "billetera": "Galicia",
            "billetera_origen": "Galicia",
            "transacciones_adicionales": [
                {"monto": 16900.0, "tipo": "egreso", "categoria": "Supermercado", "billetera": "Galicia"},
                {"monto": 300000.0, "tipo": "ingreso", "categoria": "Otros", "billetera": "Galicia"},
            ],
        },
        "respuesta_usuario": "Listo, 3 movimientos desde Galicia: ...",
    }

    llamadas_ai = [resp_1, resp_2]

    def mock_procesar(*args, **kwargs):
        return llamadas_ai.pop(0)

    mensajes_enviados = []
    with patch("app.services.ai_service.procesar_mensaje", side_effect=mock_procesar) as p_mock, \
         patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=lambda to, text: mensajes_enviados.append(text)):

        datos_msg = {
            "from_number": u.telefono_normalizado,
            "msg_type": "text",
            "msg": {"text": {"body": msg_texto}},
            "wamid": "wamid.test_reintento_ok",
            "t_inicio": 0.0,
        }
        _procesar_mensaje_whatsapp_background(datos_msg)

        assert p_mock.call_count == 2
        segundo_prompt_arg = p_mock.call_args_list[1].kwargs.get("mensaje") or p_mock.call_args_list[1].args[0]
        assert "Atención: el mensaje tiene 3 montos" in segundo_prompt_arg
        assert len(mensajes_enviados) == 1
        assert "Ojo: en tu mensaje también vi" not in mensajes_enviados[0]


def test_reintento_falla_y_termina_en_aviso_faltantes(db_session, mock_contexto_db):
    """Caso 2: la IA en ambas llamadas omite el tercer monto y la respuesta final termina con el aviso de C."""
    u, b = mock_contexto_db
    msg_texto = "gasté 2.900 pesos en Uber y 16.900 pesos de los chinos. Mi mamá me transfirió 300.000 pesos"

    resp_incompleta = {
        "intent": "registrar_transaccion",
        "confianza": 0.95,
        "slot_filling": False,
        "entidades": {
            "monto": 2900.0,
            "tipo": "egreso",
            "categoria": "Taxi / Apps",
            "billetera": "Galicia",
            "billetera_origen": "Galicia",
            "transacciones_adicionales": [
                {"monto": 16900.0, "tipo": "egreso", "categoria": "Supermercado", "billetera": "Galicia"}
            ],
        },
        "respuesta_usuario": "Listo. $2.900 en Uber y $16.900 en Supermercado desde Galicia — registrado.",
    }

    llamadas_ai = [dict(resp_incompleta), dict(resp_incompleta)]

    def mock_procesar(*args, **kwargs):
        return llamadas_ai.pop(0)

    mensajes_enviados = []
    with patch("app.services.ai_service.procesar_mensaje", side_effect=mock_procesar) as p_mock, \
         patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=lambda to, text: mensajes_enviados.append(text)):

        datos_msg = {
            "from_number": u.telefono_normalizado,
            "msg_type": "text",
            "msg": {"text": {"body": msg_texto}},
            "wamid": "wamid.test_aviso_final",
            "t_inicio": 0.0,
        }
        _procesar_mensaje_whatsapp_background(datos_msg)

        assert p_mock.call_count == 2
        assert len(mensajes_enviados) == 1
        resp_final = mensajes_enviados[0]
        aviso_esperado = "Ojo: en tu mensaje también vi 300.000 pesos y no lo registré. Mandámelo en un mensaje aparte así lo cargo bien."
        assert aviso_esperado in resp_final
