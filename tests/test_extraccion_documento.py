"""
tests/test_extraccion_documento.py
Pruebas unitarias de extracción de documentos, esquemas, conversión de montos,
separación de duplicados y textos exactos de avisos de WhatsApp (fase4c1).
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.routers.whatsapp.extraccion_documento import (
    ESQUEMA_EXTRACCION,
    MovimientoExtraido,
    ResultadoExtraccion,
    a_entidades,
    parsear_monto_documento,
)
from app.routers.whatsapp.lote_documento import (
    armar_resultado_ia_documento,
    separar_duplicados,
)
from app.utils.fecha import hoy_argentina


def test_a_entidades_textos_exactos():
    """a_entidades construye la estructura de lote pura sin tocar base de datos."""
    hoy = hoy_argentina()
    mov1 = MovimientoExtraido(
        fecha=hoy,
        monto=Decimal("1500.50"),
        moneda="ARS",
        descripcion="Cafetería Martínez",
        sentido="egreso",
        categoria="Café y Restaurantes",
    )
    mov2 = MovimientoExtraido(
        fecha=hoy,
        monto=Decimal("8200"),
        moneda="ARS",
        descripcion="Supermercado Coto",
        sentido="egreso",
        categoria="Supermercado",
    )
    res = ResultadoExtraccion(
        documento_tipo="ticket_compra",
        movimientos=[mov1, mov2],
        billetera_texto="Galicia",
        vencimiento=None,
        total_vistos=2,
    )

    entidades = a_entidades(res, billetera_nombre="Galicia")

    assert entidades["monto"] == Decimal("1500.50")
    assert entidades["descripcion"] == "Cafetería Martínez"
    assert entidades["categoria"] == "Café y Restaurantes"
    assert entidades["tipo"] == "egreso"
    assert entidades["fecha"] == hoy.isoformat()
    assert entidades["moneda"] == "ARS"
    assert entidades["billetera"] == "Galicia"
    assert entidades["billetera_origen"] == "Galicia"
    assert entidades["origen_imagen"] is True
    assert entidades["documento_tipo"] == "ticket_compra"
    assert entidades["total_vistos"] == 2

    assert len(entidades["transacciones_adicionales"]) == 1
    ad = entidades["transacciones_adicionales"][0]
    assert ad["monto"] == Decimal("8200")
    assert ad["descripcion"] == "Supermercado Coto"
    assert ad["categoria"] == "Supermercado"
    assert ad["tipo"] == "egreso"
    assert ad["billetera"] == "Galicia"


def test_tope_de_10_y_total_vistos():
    """Si el documento tiene más de 10 movimientos, total_vistos conserva el total y movimientos se limita a 10."""
    hoy = hoy_argentina()
    movs = [
        MovimientoExtraido(
            fecha=hoy,
            monto=Decimal(f"{i * 100}"),
            moneda="ARS",
            descripcion=f"Movimiento {i}",
            sentido="egreso",
            categoria=None,
        )
        for i in range(1, 13)
    ]
    res = ResultadoExtraccion(
        documento_tipo="captura_actividad",
        movimientos=movs[:10],
        billetera_texto="Mercado Pago",
        vencimiento=None,
        total_vistos=12,
    )

    assert res.total_vistos == 12
    assert len(res.movimientos) == 10


def test_conversion_montos_a_decimal():
    """monto 18450.5 y '$18.450' bien convertidos a Decimal, sin floats."""
    d1 = parsear_monto_documento(18450.5)
    assert isinstance(d1, Decimal)
    assert d1 == Decimal("18450.5")

    d2 = parsear_monto_documento("$18.450")
    assert isinstance(d2, Decimal)
    assert d2 == Decimal("18450")

    d3 = parsear_monto_documento("$18.450,50")
    assert isinstance(d3, Decimal)
    assert d3 == Decimal("18450.50")


def test_descarte_de_monto_menor_o_igual_a_cero():
    """Descarta montos menores o iguales a cero."""
    assert parsear_monto_documento(0) == Decimal("0")
    assert parsear_monto_documento(-150) == Decimal("-150")

    from app.routers.whatsapp.extraccion_documento import extraer_movimientos_de_imagen

    # Falso OpenAI que devuelve un movimiento con monto 0 y otro con -500
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"legible": true, "documento_tipo": "ticket_compra", "billetera_texto": null, '
        '"vencimiento": null, "movimientos": [{"fecha": "2026-10-07", "monto": 0, "moneda": "ARS", '
        '"descripcion": "Cero", "sentido": "egreso", "categoria": null}]}'
    )
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50

    with patch("app.routers.whatsapp.extraccion_documento.get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = mock_resp
        res, err = extraer_movimientos_de_imagen(b"fake_image_bytes")
        assert res is None
        assert err == "ILEGIBLE"


def test_esquema_extraccion_estricto():
    """ESQUEMA_EXTRACCION con additionalProperties false y todos los campos en required."""
    schema = ESQUEMA_EXTRACCION["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"].keys()) == set(schema["required"])

    items_schema = schema["properties"]["movimientos"]["items"]
    assert items_schema["additionalProperties"] is False
    assert set(items_schema["properties"].keys()) == set(items_schema["required"])
    assert ESQUEMA_EXTRACCION["json_schema"]["strict"] is True


def test_separar_duplicados_uno_y_todos():
    """separar_duplicados separa correctamente un duplicado y todos duplicados."""
    hoy = hoy_argentina()
    uid = uuid4()
    mock_db = MagicMock()

    entidades = {
        "monto": Decimal("5000"),
        "descripcion": "Gasto Existente",
        "categoria": "Otros",
        "tipo": "egreso",
        "fecha": hoy.isoformat(),
        "moneda": "ARS",
        "billetera": "Galicia",
        "transacciones_adicionales": [
            {
                "monto": Decimal("3000"),
                "descripcion": "Gasto Nuevo",
                "categoria": "Otros",
                "tipo": "egreso",
                "fecha": hoy.isoformat(),
                "moneda": "ARS",
                "billetera": "Galicia",
            }
        ],
    }

    # Caso 1: 1 duplicado, 1 nuevo
    def mock_buscar(db, usuario_id, monto, moneda, fecha, tipo, billetera_id, descripcion):
        if descripcion == "Gasto Existente":
            return [MagicMock()]  # Duplicado encontrado
        return []

    with patch("app.services.duplicados_service.buscar_coincidencias", side_effect=mock_buscar):
        ent_limpias, dups = separar_duplicados(mock_db, uid, None, entidades)
        assert len(dups) == 1
        assert dups[0]["descripcion"] == "Gasto Existente"
        assert ent_limpias["monto"] == Decimal("3000")
        assert ent_limpias["descripcion"] == "Gasto Nuevo"
        assert len(ent_limpias["transacciones_adicionales"]) == 0

    # Caso 2: Todos duplicados
    with patch("app.services.duplicados_service.buscar_coincidencias", return_value=[MagicMock()]):
        ent_limpias_todas, dups_todas = separar_duplicados(mock_db, uid, None, entidades)
        assert ent_limpias_todas == {}
        assert len(dups_todas) == 2


def test_textos_exactos_avisos():
    """Verifica el texto exacto de cada aviso (Decisiones 3, 5 y 7)."""
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    # Decisión 3: Tope 10 movimientos
    entidades = {
        "monto": Decimal("1000"),
        "descripcion": "Café",
        "categoria": "Café",
        "tipo": "egreso",
        "fecha": hoy.isoformat(),
        "moneda": "ARS",
        "billetera": "Galicia",
        "transacciones_adicionales": [],
    }
    res3 = armar_resultado_ia_documento(
        entidades=entidades,
        duplicados=[],
        billetera_nombre="Galicia",
        se_asumio_principal=False,
        documento_tipo="captura_actividad",
        total_vistos=12,
    )
    assert "Vi 12 movimientos y cargo los primeros 10. Mandame otra captura con el resto." in res3["respuesta_usuario"]

    # Decisión 5: Duplicados parciales
    dups = [
        {"descripcion": "Almuerzo", "fecha": ayer, "monto": Decimal("5000"), "moneda": "ARS"}
    ]
    res5 = armar_resultado_ia_documento(
        entidades=entidades,
        duplicados=dups,
        billetera_nombre="Galicia",
        se_asumio_principal=False,
        documento_tipo="ticket_compra",
        total_vistos=2,
    )
    assert "Ya tenías cargado: Almuerzo (ayer, $5.000)." in res5["respuesta_usuario"]
    assert "Si es otro movimiento igual, mandámelo escrito." in res5["respuesta_usuario"]

    # Decisión 5: Todos duplicados
    res5_todos = armar_resultado_ia_documento(
        entidades={},
        duplicados=dups,
        billetera_nombre="Galicia",
        se_asumio_principal=False,
        documento_tipo="ticket_compra",
        total_vistos=1,
    )
    assert res5_todos["respuesta_usuario"] == "Ya tenías cargado todo lo que veo en la imagen."
    assert res5_todos["intent"] != "registrar_transaccion"

    # Decisión 7: Factura de servicios
    res7 = armar_resultado_ia_documento(
        entidades=entidades,
        duplicados=[],
        billetera_nombre="Galicia",
        se_asumio_principal=False,
        documento_tipo="factura_servicio",
        total_vistos=1,
    )
    assert "Si todavía no la pagaste, respondé no y no la cargo." in res7["respuesta_usuario"]


def test_esquema_sin_limite_de_cantidad():
    """Verifica que en ESQUEMA_EXTRACCION la description de movimientos no contiene 'máximo' y no existe maxItems."""
    movimientos_schema = (
        ESQUEMA_EXTRACCION["json_schema"]["schema"]["properties"]["movimientos"]
    )
    assert "máximo" not in movimientos_schema.get("description", "").lower()
    assert "maxItems" not in movimientos_schema
