"""
tests/test_permitirse_whatsapp.py

Pruebas unitarias para la detección, formateo de respuesta y handler determinístico
de '¿Me lo puedo permitir?' por WhatsApp.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.usuario import Usuario
from app.routers.whatsapp.contexto import ContextoMensaje
from app.routers.whatsapp.handlers_permitirse import (
    armar_respuesta_permitirse,
    detectar_consulta_permitirse,
    manejar_consulta_permitirse,
)


class TestDetectorPermitirse:
    """Casos 1 a 8 de la especificación para detectar_consulta_permitirse."""

    def test_01_contado_precio_300000(self):
        # 1. "¿me puedo permitir una tele de 300.000?" da contado y precio 300000
        res = detectar_consulta_permitirse("¿me puedo permitir una tele de 300.000?")
        assert res is not None
        assert res["falta"] is None
        assert res["modo"] == "contado"
        assert res["precio"] == Decimal("300000")
        assert res["cantidad_cuotas"] == 1
        assert res["tiene_interes"] is False
        assert res["tna"] is None
        assert res["nota_interes"] is False

    def test_02_cuotas_6_precio_600000(self):
        # 2. "me alcanza para un celular de 600 mil en 6 cuotas?" da cuotas, 6 cuotas y precio 600000
        res = detectar_consulta_permitirse("me alcanza para un celular de 600 mil en 6 cuotas?")
        assert res is not None
        assert res["falta"] is None
        assert res["modo"] == "cuotas"
        assert res["cantidad_cuotas"] == 6
        assert res["precio"] == Decimal("600000")
        assert res["tiene_interes"] is False
        assert res["tna"] is None
        assert res["nota_interes"] is False

    def test_03_cuotas_12_de_50_lucas(self):
        # 3. "¿puedo comprar una heladera en 12 cuotas de 50 lucas?" da cuotas, 12 cuotas y precio 600000
        res = detectar_consulta_permitirse("¿puedo comprar una heladera en 12 cuotas de 50 lucas?")
        assert res is not None
        assert res["falta"] is None
        assert res["modo"] == "cuotas"
        assert res["cantidad_cuotas"] == 12
        assert res["precio"] == Decimal("600000")
        assert res["tiene_interes"] is False
        assert res["tna"] is None
        assert res["nota_interes"] is False

    def test_04_falta_precio(self):
        # 4. "me lo puedo permitir?" da falta precio
        res = detectar_consulta_permitirse("me lo puedo permitir?")
        assert res == {"falta": "precio"}

    def test_05_falta_cuotas(self):
        # 5. "me puedo permitir algo de 300 mil en cuotas?" da falta cuotas
        res = detectar_consulta_permitirse("me puedo permitir algo de 300 mil en cuotas?")
        assert res == {"falta": "cuotas"}

    def test_06_con_60_porc_interes(self):
        # 6. "me puedo permitir 600 mil en 6 cuotas con 60% de interés?" da tiene_interes True y tna 60
        res = detectar_consulta_permitirse("me puedo permitir 600 mil en 6 cuotas con 60% de interés?")
        assert res is not None
        assert res["falta"] is None
        assert res["modo"] == "cuotas"
        assert res["cantidad_cuotas"] == 6
        assert res["precio"] == Decimal("600000")
        assert res["tiene_interes"] is True
        assert res["tna"] == Decimal("60")
        assert res["nota_interes"] is False

    def test_07_con_interes_sin_porcentaje(self):
        # 7. "me puedo permitir 600 mil en 6 cuotas con interés?" da tiene_interes False y nota_interes True
        res = detectar_consulta_permitirse("me puedo permitir 600 mil en 6 cuotas con interés?")
        assert res is not None
        assert res["falta"] is None
        assert res["modo"] == "cuotas"
        assert res["cantidad_cuotas"] == 6
        assert res["precio"] == Decimal("600000")
        assert res["tiene_interes"] is False
        assert res["tna"] is None
        assert res["nota_interes"] is True

    @pytest.mark.parametrize(
        "texto",
        [
            "¿puedo comprar 100 dólares?",
            "¿puedo pagar la tarjeta?",
            "¿me alcanza para llegar a fin de mes?",
            "me alcanza la plata?",
            "gasté 5000 en el kiosco",
            "¿puedo comprar algo?",
        ],
    )
    def test_08_devuelven_none(self, texto: str):
        # 8. Mensajes que deben devolver None (no interceptados por el detector)
        res = detectar_consulta_permitirse(texto)
        assert res is None, f"Esperado None para '{texto}', obtenido {res}"


class TestArmarRespuestaPermitirse:
    """Casos 9 a 11 con dicts armados a mano y verificación de textos exactos."""

    def test_09_contado_verde_y_negro(self):
        # Contado verde
        res_verde = {
            "modo": "contado",
            "precio_total": 300000.0,
            "saldo_disponible_actual": 5000000.0,
            "saldo_restante_post_compra": 4700000.0,
            "porcentaje_del_saldo": 6.0,
            "semaforo": "verde",
            "mensaje_principal": "Podés comprarlo sin comprometer tu estabilidad.",
        }
        cons_verde = {
            "falta": None,
            "precio": Decimal("300000"),
            "modo": "contado",
            "cantidad_cuotas": 1,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_verde = "Podés comprarlo sin comprometer tu estabilidad. Te quedarían $4.700.000 de los $5.000.000 que tenés disponibles."
        assert armar_respuesta_permitirse(res_verde, cons_verde) == esp_verde

        # Contado negro
        res_negro = {
            "modo": "contado",
            "precio_total": 300000.0,
            "saldo_disponible_actual": 100000.0,
            "saldo_restante_post_compra": -200000.0,
            "porcentaje_del_saldo": 300.0,
            "semaforo": "negro",
            "mensaje_principal": "No tenés suficiente saldo disponible para esta compra.",
        }
        cons_negro = {
            "falta": None,
            "precio": Decimal("300000"),
            "modo": "contado",
            "cantidad_cuotas": 1,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_negro = "No tenés suficiente saldo disponible para esta compra. Tenés $100.000 disponibles y cuesta $300.000."
        assert armar_respuesta_permitirse(res_negro, cons_negro) == esp_negro

    def test_10_cuotas_gris_margen_positivo_y_negativo(self):
        # Cuotas gris
        res_gris = {
            "modo": "cuotas",
            "precio_total": 600000.0,
            "monto_cuota": 100000.0,
            "cantidad_cuotas": 6,
            "semaforo": "gris",
            "mensaje_principal": "Ingresá tu ingreso mensual para ver el análisis completo.",
            "porcentaje_carga_sobre_ingreso": None,
            "margen_libre_post_compra": None,
        }
        cons_gris = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_gris = "La cuota sería de $100.000 por mes. Para decirte si te entra necesito conocer tus cobros: cargá tus ingresos y volvé a preguntarme."
        assert armar_respuesta_permitirse(res_gris, cons_gris) == esp_gris

        # Cuotas con margen positivo
        res_pos = {
            "modo": "cuotas",
            "precio_total": 600000.0,
            "monto_cuota": 100000.0,
            "cantidad_cuotas": 6,
            "carga_mensual_nueva_total": 250000.0,
            "porcentaje_carga_sobre_ingreso": 25.0,
            "margen_libre_post_compra": 450000.0,
            "semaforo": "verde",
            "mensaje_principal": "La cuota entra bien en tu presupuesto mensual.",
            "tiene_interes": False,
            "precio_total_real": 600000.0,
            "interes_total": 0.0,
        }
        cons_pos = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_pos = "La cuota entra bien en tu presupuesto mensual. La cuota sería de $100.000 por mes. Con lo que ya pagás en cuotas y suscripciones llegarías a $250.000 por mes, el 25% de tu ingreso. Después de tus gastos de siempre te quedarían $450.000 por mes."
        assert armar_respuesta_permitirse(res_pos, cons_pos) == esp_pos

        # Cuotas con margen negativo
        res_neg = {
            "modo": "cuotas",
            "precio_total": 600000.0,
            "monto_cuota": 100000.0,
            "cantidad_cuotas": 6,
            "carga_mensual_nueva_total": 600000.0,
            "porcentaje_carga_sobre_ingreso": 85.0,
            "margen_libre_post_compra": -50000.0,
            "semaforo": "rojo",
            "mensaje_principal": "Tus gastos totales y cuotas superarían tus ingresos mensuales, dejándote con saldo negativo.",
            "tiene_interes": False,
            "precio_total_real": 600000.0,
            "interes_total": 0.0,
        }
        cons_neg = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_neg = "Tus gastos totales y cuotas superarían tus ingresos mensuales, dejándote con saldo negativo. La cuota sería de $100.000 por mes. Con lo que ya pagás en cuotas y suscripciones llegarías a $600.000 por mes, el 85% de tu ingreso. Después de tus gastos de siempre te faltarían $50.000 por mes."
        assert armar_respuesta_permitirse(res_neg, cons_neg) == esp_neg

    def test_11_interes_y_nota_interes(self):
        # Con interés
        res_int = {
            "modo": "cuotas",
            "precio_total": 600000.0,
            "monto_cuota": 120000.0,
            "cantidad_cuotas": 6,
            "carga_mensual_nueva_total": 270000.0,
            "porcentaje_carga_sobre_ingreso": 27.0,
            "margen_libre_post_compra": 430000.0,
            "semaforo": "verde",
            "mensaje_principal": "La cuota entra bien en tu presupuesto mensual.",
            "tiene_interes": True,
            "precio_total_real": 720000.0,
            "interes_total": 120000.0,
        }
        cons_int = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": True,
            "tna": Decimal("60"),
            "nota_interes": False,
        }
        esp_int = "La cuota entra bien en tu presupuesto mensual. La cuota sería de $120.000 por mes. Con lo que ya pagás en cuotas y suscripciones llegarías a $270.000 por mes, el 27% de tu ingreso. Después de tus gastos de siempre te quedarían $430.000 por mes. Con interés pagarías $720.000 en total ($120.000 de interés)."
        assert armar_respuesta_permitirse(res_int, cons_int) == esp_int

        # Con nota_interes
        res_nota = {
            "modo": "cuotas",
            "precio_total": 600000.0,
            "monto_cuota": 100000.0,
            "cantidad_cuotas": 6,
            "carga_mensual_nueva_total": 250000.0,
            "porcentaje_carga_sobre_ingreso": 25.0,
            "margen_libre_post_compra": 450000.0,
            "semaforo": "verde",
            "mensaje_principal": "La cuota entra bien en tu presupuesto mensual.",
            "tiene_interes": False,
            "precio_total_real": 600000.0,
            "interes_total": 0.0,
        }
        cons_nota = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": True,
        }
        esp_nota = (
            "La cuota entra bien en tu presupuesto mensual. La cuota sería de $100.000 por mes. "
            "Con lo que ya pagás en cuotas y suscripciones llegarías a $250.000 por mes, el 25% de tu ingreso. "
            "Después de tus gastos de siempre te quedarían $450.000 por mes.\n"
            "Lo calculé sin interés. Si tiene interés, decime la tasa anual (TNA)."
        )
        assert armar_respuesta_permitirse(res_nota, cons_nota) == esp_nota

    def test_12_faltas(self):
        assert (
            armar_respuesta_permitirse({}, {"falta": "precio"})
            == 'Decime cuánto sale. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'
        )
        assert (
            armar_respuesta_permitirse({}, {"falta": "cuotas"})
            == 'Decime en cuántas cuotas. Por ejemplo: "¿me puedo permitir algo de 300.000 en 6 cuotas?"'
        )


class TestHandlerPermitirse:
    """Pruebas del handler manejar_consulta_permitirse (incluyendo manejo de excepciones)."""

    def test_handler_excepcion_calculo(self):
        usuario = MagicMock(spec=Usuario)
        usuario.id = "user-123"
        db = MagicMock()

        ctx = ContextoMensaje(
            datos_mensaje={},
            msg={},
            wamid="wamid-test",
            from_number="+5491100000000",
            msg_type="text",
            t_inicio=0.0,
            db=db,
            usuario=usuario,
            mensaje_texto="¿me puedo permitir una tele de 300.000?",
        )

        with patch("app.services.tools_service.calcular_puede_permitirse", side_effect=RuntimeError("Fallo DB")):
            with patch("app.services.whatsapp_service.enviar_whatsapp") as mock_envio:
                manejado = manejar_consulta_permitirse(ctx)

                assert manejado is True
                db.add.assert_called_once()
                db.commit.assert_called_once()
                mock_envio.assert_called_once_with(
                    "+5491100000000",
                    "No pude calcularlo en este momento. Probá de nuevo en unos minutos.",
                )
