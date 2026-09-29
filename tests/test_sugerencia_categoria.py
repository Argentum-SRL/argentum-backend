from app.routers.whatsapp.parsers import _extraer_nombre_servicio
import pytest
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.core.database import Base
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.core.catalogo_suscripciones import (
    sugerir_categoria_suscripcion,
    resolver_categoria_sugerida,
    identificar_servicio_en_texto,
)

CASOS_PRUEBA = [
    ("Movistar Fibra", ("Comunicación", "Internet y cable")),
    ("Claro Hogar", ("Comunicación", "Internet y cable")),
    ("Claro", ("Comunicación", "Celular")),
    ("Personal", ("Comunicación", "Celular")),
    ("Entrenador personal", ("Salud", "Deportes y gimnasio")),
    ("Megatlon", ("Salud", "Deportes y gimnasio")),
    ("Seguro del auto La Caja", ("Transporte", "Mantenimiento y seguro del auto")),
    ("Seguro hogar Mapfre", ("Vivienda", "Seguros")),
    ("Sancor Seguros", ("Vivienda", "Seguros")),
    ("OSDE 210", ("Salud", "Obra social / Prepaga")),
    ("Sancor Salud", ("Salud", "Obra social / Prepaga")),
    ("Hospital Italiano", ("Salud", "Obra social / Prepaga")),
    ("Clases de inglés", ("Educación", "Idiomas")),
    ("Clases de natación", ("Salud", "Deportes y gimnasio")),
    ("Club Atlético", ("Salud", "Deportes y gimnasio")),
    ("Clases de piano", ("Recreativo", "Hobbies y juegos")),
    ("Colegio San José", ("Educación", "Cuotas")),
    ("Jardín maternal", ("Educación", "Cuotas")),
    ("Netflix Premium", ("Recreativo", None)),
    ("Cosa rara que no existe", ("Otros", None)),
    ("", ("Otros", None)),
]

@pytest.mark.parametrize("texto,esperado", CASOS_PRUEBA)
def test_sugerir_categoria_suscripcion(texto, esperado):
    assert sugerir_categoria_suscripcion(texto) == esperado


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

    categorias_data = [
        ("Comunicación", ["Internet y cable", "Celular"]),
        ("Salud", ["Deportes y gimnasio", "Obra social / Prepaga"]),
        ("Transporte", ["Mantenimiento y seguro del auto"]),
        ("Vivienda", ["Seguros"]),
        ("Educación", ["Idiomas", "Cuotas"]),
        ("Recreativo", ["Hobbies y juegos"]),
        ("Otros", []),
    ]

    for cat_nombre, subcats in categorias_data:
        cat = Categoria(id=uuid4(), nombre=cat_nombre, tipo=TipoCategoria.EGRESO)
        session.add(cat)
        session.flush()
        for sub_nom in subcats:
            sub = Subcategoria(id=uuid4(), categoria_id=cat.id, nombre=sub_nom)
            session.add(sub)
    session.commit()

    try:
        yield session
    finally:
        session.close()


def test_resolver_categoria_sugerida_con_subcategoria(db_session):
    cat_id, sub_id = resolver_categoria_sugerida(db_session, "Movistar Fibra")
    assert cat_id is not None
    assert sub_id is not None
    cat = db_session.get(Categoria, cat_id)
    sub = db_session.get(Subcategoria, sub_id)
    assert cat.nombre == "Comunicación"
    assert sub.nombre == "Internet y cable"


def test_resolver_categoria_sugerida_sin_subcategoria(db_session):
    cat_id, sub_id = resolver_categoria_sugerida(db_session, "Netflix Premium")
    assert cat_id is not None
    assert sub_id is None
    cat = db_session.get(Categoria, cat_id)
    assert cat.nombre == "Recreativo"


def test_resolver_categoria_sugerida_no_coincidente(db_session):
    cat_id, sub_id = resolver_categoria_sugerida(db_session, "Cosa rara inventada")
    assert cat_id is not None
    assert sub_id is None
    cat = db_session.get(Categoria, cat_id)
    assert cat.nombre == "Otros"


@pytest.mark.parametrize("texto", [
    "compré un cable usb 3000",
    "cargué 5000 al celular",
    "pagué el colegio 80000",
    "pagué el seguro del auto",
    "clases de guitarra 10000",
    "fui al club",
    "pagué el gimnasio",
])
def test_identificar_servicio_en_texto_no_genericos(texto):
    assert identificar_servicio_en_texto(texto) is None


def test_identificar_servicio_en_texto_servicios_reales():
    spotify = identificar_servicio_en_texto("pagué el Spotify")
    assert spotify is not None
    assert spotify["nombre"] == "Spotify"

    netflix = identificar_servicio_en_texto("me suscribí a Netflix")
    assert netflix is not None
    assert netflix["nombre"] == "Netflix"

@pytest.mark.parametrize("texto", [
    "pago el alquiler 300000 por mes",
    "pago 300 mil por mes de expensas",
    "Pago el gimnasio 45000 por mes",
])
def test_extraer_nombre_servicio_no_suscribe_gastos_comunes(texto):
    assert _extraer_nombre_servicio(texto) is None


def test_extraer_nombre_servicio_empece_a_pagar():
    assert _extraer_nombre_servicio("empecé a pagar 15000 del Gimnasio del barrio por mes") == "Gimnasio del barrio"
    assert _extraer_nombre_servicio("empecé a pagar 45000 del gimnasio por mes") == "gimnasio"

