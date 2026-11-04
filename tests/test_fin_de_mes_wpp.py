"""
tests/test_fin_de_mes_wpp.py — Pruebas de integración de WhatsApp para consultar_proyeccion.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.services.contrato_numero_service import ContratoNumero
from app.services import fin_de_mes_service
from app.services.fin_de_mes_service import ResultadoFinDeMes, ResultadoRango
from app.services.fin_de_mes_textos import mensaje_wpp
from app.services.frescura_service import Frescura


@pytest.fixture
def usuario_mock():
    return Usuario(
        id=uuid4(),
        email="wpp_test@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
    )


def _caso_completo_resultado():
    rango = ResultadoRango(
        estado="completo",
        rango_bajo=Decimal("1145000"),
        rango_alto=Decimal("1345000"),
        ciclos=3,
        veces_no_alcanza=0,
        factor_cobertura=Decimal("1"),
    )
    frescura = Frescura(
        ultima_carga=date(2026, 11, 19),
        dias_sin_cargar=1,
        umbral_dias=7,
        intervalos=5,
        datos_viejos=False,
    )
    contrato = ContratoNumero(
        valor=Decimal("445000"),
        moneda="ARS",
        base_desde=date(2026, 11, 1),
        base_hasta=date(2026, 11, 30),
        fecha_corte=date(2026, 11, 20),
        cobertura=None,
        frescura=frescura,
        supuestos=(),
    )
    return ResultadoFinDeMes(
        hoy=date(2026, 11, 20),
        inicio_ciclo=date(2026, 11, 1),
        fin_ciclo=date(2026, 11, 30),
        limite=date(2026, 11, 24),
        proximo_ingreso=date(2026, 11, 25),
        hasta_ingreso=True,
        cobra_en_ciclo=True,
        estado="completo",
        libre_ars=Decimal("445000"),
        libre_hasta_cobro=Decimal("485000"),
        ingreso_esperado=Decimal("1000000"),
        libre_usd=Decimal("0"),
        compromisos_ars=[{"fecha_cobro": "2026-11-22", "nombre": "Gimnasio", "monto": Decimal("15000")}],
        compromisos_usd=[],
        rango=rango,
        cobertura=None,
        saldo_inicio=Decimal("550000"),
        movimiento=Decimal("-50000"),
        saldo_hoy=Decimal("500000"),
        frescura=frescura,
        contrato=contrato,
    )


def test_enriquecer_respuesta_consultar_proyeccion_ok(usuario_mock, monkeypatch):
    resultado_mock = _caso_completo_resultado()
    monkeypatch.setattr(fin_de_mes_service, "calcular_fin_de_mes", lambda db, u: resultado_mock)

    resultado_ia = {}
    db_mock = MagicMock()

    res_intent = enriquecer_respuesta_por_intent(
        intent_detectado="consultar_proyeccion",
        resultado_ia=resultado_ia,
        mensaje_texto="¿cómo llego a fin de mes?",
        usuario=usuario_mock,
        db=db_mock,
    )

    assert res_intent == "consultar_proyeccion"
    assert resultado_ia["respuesta_usuario"] == mensaje_wpp(resultado_mock)


def test_enriquecer_respuesta_consultar_proyeccion_error(usuario_mock, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("Fallo inesperado")

    monkeypatch.setattr(fin_de_mes_service, "calcular_fin_de_mes", boom)

    resultado_ia = {}
    db_mock = MagicMock()

    res_intent = enriquecer_respuesta_por_intent(
        intent_detectado="consultar_proyeccion",
        resultado_ia=resultado_ia,
        mensaje_texto="proyección",
        usuario=usuario_mock,
        db=db_mock,
    )

    assert res_intent == "consultar_proyeccion"
    assert resultado_ia["respuesta_usuario"] == "No pude calcular tu proyección en este momento. Probá de nuevo en unos minutos."

