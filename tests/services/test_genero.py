"""
tests/services/test_genero.py — Tests para helpers de concordancia de género y templates de email.
"""
import pytest
from app.models.usuario import Sexo
from app.utils.genero import (
    normalizar_sexo,
    flexionar_saludo,
    flexionar_palabra,
    get_asunto_bienvenida,
)
from app.services.email_templates import template_bienvenida


def test_normalizar_sexo():
    assert normalizar_sexo(Sexo.FEMENINO) == "femenino"
    assert normalizar_sexo(Sexo.MASCULINO) == "masculino"
    assert normalizar_sexo(Sexo.NO_BINARIO) == "no_binario"
    assert normalizar_sexo("FEMENINO") == "femenino"
    assert normalizar_sexo("Masculino") == "masculino"
    assert normalizar_sexo(None) is None


def test_flexionar_saludo():
    assert flexionar_saludo(Sexo.FEMENINO, "Bienvenido") == "Bienvenida"
    assert flexionar_saludo(Sexo.MASCULINO, "Bienvenido") == "Bienvenido"
    assert flexionar_saludo(Sexo.NO_BINARIO, "Bienvenido") == "Te damos la bienvenida"
    assert flexionar_saludo(None, "bienvenido") == "te damos la bienvenida"
    assert flexionar_saludo("femenino", "bienvenido") == "bienvenida"


def test_flexionar_palabra():
    assert flexionar_palabra(Sexo.FEMENINO, "listo", "lista") == "lista"
    assert flexionar_palabra(Sexo.MASCULINO, "listo", "lista") == "listo"
    assert flexionar_palabra(Sexo.NO_BINARIO, "listo", "lista", "listo/a") == "listo/a"
    assert flexionar_palabra(None, "estimado", "estimada", "estimade") == "estimade"


def test_get_asunto_bienvenida():
    assert get_asunto_bienvenida(Sexo.FEMENINO) == "¡Bienvenida a Argentum!"
    assert get_asunto_bienvenida(Sexo.MASCULINO) == "¡Bienvenido a Argentum!"
    assert get_asunto_bienvenida(Sexo.NO_BINARIO) == "¡Te damos la bienvenida a Argentum!"
    assert get_asunto_bienvenida(None) == "¡Te damos la bienvenida a Argentum!"


def test_template_bienvenida():
    html_fem = template_bienvenida("Lucía", Sexo.FEMENINO)
    assert "Lucía" in html_fem
    assert "Tu cuenta ya está lista, Lucía" in html_fem

    html_masc = template_bienvenida("Martín", Sexo.MASCULINO)
    assert "Martín" in html_masc
    assert "Tu cuenta ya está lista, Martín" in html_masc

    html_nb = template_bienvenida("Alex", Sexo.NO_BINARIO)
    assert "Alex" in html_nb
    assert "Tu cuenta ya está lista, Alex" in html_nb
