# -*- coding: utf-8 -*-
"""
tests/test_extraccion_documento_parte2.py — Pruebas de extracción de imágenes
separadas de test_extraccion_documento.py para respetar el tope de 1.300 líneas.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app.routers.whatsapp.extraccion_documento import extraer_movimientos_de_imagen


def test_extraer_movimientos_de_imagen_argumentos():
    """C4: extraer_movimientos_de_imagen lleva temperature=0 y enum de categorías."""
    categorias = ["Supermercado", "Farmacia"]
    mock_payload = {
        "legible": True,
        "documento_tipo": "ticket_compra",
        "billetera_texto": None,
        "vencimiento": None,
        "movimientos": [
            {
                "fecha": "2026-10-08",
                "monto": 1500.0,
                "moneda": "ARS",
                "descripcion": "Coto",
                "sentido": "egreso",
                "categoria": "Supermercado",
                "tipo_operacion": None,
                "medio_pago": None,
                "estado": None,
                "contraparte_es_usuario": False,
            }
        ],
        "cuotas": [],
    }

    mock_resp = MagicMock()
    mock_resp.choices = [
        MagicMock(message=MagicMock(content=json.dumps(mock_payload)))
    ]
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50

    with patch(
        "app.routers.whatsapp.extraccion_documento.get_openai_client"
    ) as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.chat.completions.create.return_value = mock_resp

        res, err = extraer_movimientos_de_imagen(
            b"fake_image_bytes", categorias_usuario=categorias
        )
        assert err is None
        assert res is not None

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs.get("temperature") == 0
        resp_fmt = kwargs.get("response_format", {})
        mov_props = (
            resp_fmt["json_schema"]["schema"]["properties"]["movimientos"]
            ["items"]["properties"]
        )
        cat_enum = mov_props["categoria"]["enum"]
        assert cat_enum == ["Supermercado", "Farmacia", None]
