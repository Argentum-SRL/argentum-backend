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
    """Casos 9 a 11 con dicts armados a mano y verificación de textos exactos (nuevo formato B-G)."""

    def test_09_contado_verde_y_negro(self):
        # contado bien del caso de integración → "Te entra bien.\nDespués de pagarlo llegarías al 30/11 con entre $845.000 y $1.045.000."
        res_bien = {
            "titulo": "Te entra bien",
            "linea_principal": "Despu\u00e9s de pagarlo llegar\u00edas al 30/11 con entre $845.000 y $1.045.000.",
        }
        cons_bien = {
            "falta": None,
            "precio": Decimal("300000"),
            "modo": "contado",
            "cantidad_cuotas": 1,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_bien = "Te entra bien.\nDespu\u00e9s de pagarlo llegar\u00edas al 30/11 con entre $845.000 y $1.045.000."
        assert armar_respuesta_permitirse(res_bien, cons_bien) == esp_bien

        # contado no_alcanza falta 155.000 sin ahorros → "No te alcanza.\nTe faltan $155.000."
        res_no = {
            "titulo": "No te alcanza",
            "linea_principal": "Te faltan $155.000.",
        }
        cons_no = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "contado",
            "cantidad_cuotas": 1,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_no = "No te alcanza.\nTe faltan $155.000."
        assert armar_respuesta_permitirse(res_no, cons_no) == esp_no

    def test_10_cuotas_gris_margen_positivo_y_negativo(self):
        # cuotas justo 6 × 100.000 r_min 15.000 colchon 20.000
        # → "Te entra justo.\nSon 6 cuotas de $100.000.\nTe quedarían solo $15.000 por mes después de tus gastos de siempre."
        res_justo = {
            "titulo": "Te entra justo",
            "lineas_plan": ["Son 6 cuotas de $100.000."],
            "linea_principal": "Te quedar\u00edan solo $15.000 por mes despu\u00e9s de tus gastos de siempre.",
        }
        cons_justo = {
            "falta": None,
            "precio": Decimal("600000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_justo = "Te entra justo.\nSon 6 cuotas de $100.000.\nTe quedar\u00edan solo $15.000 por mes despu\u00e9s de tus gastos de siempre."
        assert armar_respuesta_permitirse(res_justo, cons_justo) == esp_justo

        # mensual ahorros (TH.4)
        # → "Solo tocando tus ahorros.\nTe faltarían $30.000 por mes: tus ahorros alcanzan para 6 meses.\nTus ahorros suman $200.000."
        res_ahorros = {
            "titulo": "Solo tocando tus ahorros",
            "linea_principal": "Te faltar\u00edan $30.000 por mes: tus ahorros alcanzan para 6 meses.",
            "linea_ahorros": "Tus ahorros suman $200.000.",
        }
        cons_ahorros = {
            "falta": None,
            "precio": Decimal("80000"),
            "modo": "mensual",
            "cantidad_cuotas": 1,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": False,
        }
        esp_ahorros = "Solo tocando tus ahorros.\nTe faltar\u00edan $30.000 por mes: tus ahorros alcanzan para 6 meses.\nTus ahorros suman $200.000."
        assert armar_respuesta_permitirse(res_ahorros, cons_ahorros) == esp_ahorros

    def test_11_interes_y_nota_interes(self):
        # cuotas con interés 6 × tna 60 de 300.000, resultado bien r_min 50.000
        # → "Te entra bien.\nSon 6 cuotas de $59.105 ($354.631 en total).\nCada mes te quedarían al menos $50.000 después de tus gastos de siempre."
        res_int = {
            "titulo": "Te entra bien",
            "lineas_plan": [
                "Son 6 cuotas de $59.105 ($354.631 en total).",
                "Pag\u00e1s $54.631 de inter\u00e9s.",
            ],
            "linea_principal": "Cada mes te quedar\u00edan al menos $50.000 despu\u00e9s de tus gastos de siempre.",
        }
        cons_int = {
            "falta": None,
            "precio": Decimal("300000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": True,
            "tna": Decimal("60"),
            "nota_interes": False,
        }
        esp_int = (
            "Te entra bien.\n"
            "Son 6 cuotas de $59.105 ($354.631 en total).\n"
            "Cada mes te quedar\u00edan al menos $50.000 despu\u00e9s de tus gastos de siempre."
        )
        assert armar_respuesta_permitirse(res_int, cons_int) == esp_int

        # el mismo con nota_interes True y sin interés (6 × 50.000)
        # → termina en "\nLo calculé sin interés. Si tiene interés, decime la tasa anual (TNA)."
        res_sin_int = {
            "titulo": "Te entra bien",
            "lineas_plan": ["Son 6 cuotas de $50.000."],
            "linea_principal": "Cada mes te quedar\u00edan al menos $50.000 despu\u00e9s de tus gastos de siempre.",
        }
        cons_sin_int = {
            "falta": None,
            "precio": Decimal("300000"),
            "modo": "cuotas",
            "cantidad_cuotas": 6,
            "tiene_interes": False,
            "tna": None,
            "nota_interes": True,
        }
        esp_sin_int = (
            "Te entra bien.\n"
            "Son 6 cuotas de $50.000.\n"
            "Cada mes te quedar\u00edan al menos $50.000 despu\u00e9s de tus gastos de siempre.\n"
            "Lo calcul\u00e9 sin inter\u00e9s. Si tiene inter\u00e9s, decime la tasa anual (TNA)."
        )
        assert armar_respuesta_permitirse(res_sin_int, cons_sin_int) == esp_sin_int

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
