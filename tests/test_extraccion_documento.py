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


def test_esquema_con_rendimiento_en_enum():
    """D1: Verifica que el enum de 'sentido' incluye 'rendimiento' junto a 'egreso' e 'ingreso'."""
    sentido_enum = (
        ESQUEMA_EXTRACCION["json_schema"]["schema"]["properties"]["movimientos"]["items"]["properties"]["sentido"]["enum"]
    )
    assert "rendimiento" in sentido_enum
    assert set(sentido_enum) == {"egreso", "ingreso", "rendimiento"}


def test_a_entidades_con_y_sin_rendimientos():
    """D1: a_entidades maneja rendimientos con y sin movimientos comunes."""
    hoy = hoy_argentina()
    r1 = MovimientoExtraido(
        fecha=hoy,
        monto=Decimal("5650.20"),
        moneda="ARS",
        descripcion="Rendimientos",
        sentido="rendimiento",
        categoria=None,
    )
    m1 = MovimientoExtraido(
        fecha=hoy,
        monto=Decimal("1200.00"),
        moneda="ARS",
        descripcion="Kiosco",
        sentido="egreso",
        categoria="Kiosco",
    )

    # 1. Con comunes y con rendimientos
    res_mixto = ResultadoExtraccion(
        documento_tipo="captura_actividad",
        movimientos=[m1],
        billetera_texto="Mercado Pago",
        vencimiento=None,
        total_vistos=1,
        rendimientos=[r1],
    )
    ent_mixto = a_entidades(res_mixto, billetera_nombre="Mercado Pago")
    assert ent_mixto["monto"] == Decimal("1200.00")
    assert "rendimientos" in ent_mixto
    assert len(ent_mixto["rendimientos"]) == 1
    assert ent_mixto["rendimientos"][0]["monto"] == Decimal("5650.20")
    assert ent_mixto["rendimientos"][0]["fecha"] == hoy.isoformat()

    # 2. Solo rendimientos (sin comunes)
    res_solo_rend = ResultadoExtraccion(
        documento_tipo="captura_actividad",
        movimientos=[],
        billetera_texto="Mercado Pago",
        vencimiento=None,
        total_vistos=0,
        rendimientos=[r1],
    )
    ent_solo = a_entidades(res_solo_rend, billetera_nombre="Mercado Pago")
    assert "monto" not in ent_solo
    assert "rendimientos" in ent_solo
    assert len(ent_solo["rendimientos"]) == 1
    assert ent_solo["rendimientos"][0]["monto"] == Decimal("5650.20")

    # 3. Sin nada
    res_vacio = ResultadoExtraccion(
        documento_tipo="captura_actividad",
        movimientos=[],
        billetera_texto=None,
        vencimiento=None,
        total_vistos=0,
        rendimientos=[],
    )
    assert a_entidades(res_vacio) == {}


def test_tope_de_10_no_cuenta_rendimientos():
    """D1: El tope de 10 y total_vistos cuentan solo los movimientos comunes, los rendimientos quedan íntegros."""
    from app.routers.whatsapp.extraccion_documento import extraer_movimientos_de_imagen
    import json

    movs_json = [
        {
            "fecha": "2026-10-07",
            "monto": i * 1000,
            "moneda": "ARS",
            "descripcion": f"Gasto {i}",
            "sentido": "egreso",
            "categoria": None,
        }
        for i in range(1, 13)
    ]
    rend_json = [
        {
            "fecha": "2026-10-01",
            "monto": 5650.20,
            "moneda": "ARS",
            "descripcion": "Rendimientos",
            "sentido": "rendimiento",
            "categoria": None,
        }
    ]
    doc_json = {
        "legible": True,
        "documento_tipo": "captura_actividad",
        "billetera_texto": "Mercado Pago",
        "vencimiento": None,
        "movimientos": movs_json + rend_json,
    }

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(doc_json)
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50

    with patch("app.routers.whatsapp.extraccion_documento.get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = mock_resp
        res, err = extraer_movimientos_de_imagen(b"fake_bytes")
        assert err is None
        assert res is not None
        assert res.total_vistos == 12
        assert len(res.movimientos) == 10
        assert len(res.rendimientos) == 1
        assert res.rendimientos[0].monto == Decimal("5650.20")


def test_ilegible_solo_si_no_hay_nada():
    """D1: extraer_movimientos_de_imagen devuelve ILEGIBLE solo si no hay comunes NI rendimientos válidos."""
    from app.routers.whatsapp.extraccion_documento import extraer_movimientos_de_imagen
    import json

    # Caso A: Solo rendimientos válidos -> NO es ilegible
    doc_rend = {
        "legible": True,
        "documento_tipo": "captura_actividad",
        "billetera_texto": "Mercado Pago",
        "vencimiento": None,
        "movimientos": [
            {
                "fecha": "2026-10-01",
                "monto": 312.40,
                "moneda": "ARS",
                "descripcion": "Rendimientos",
                "sentido": "rendimiento",
                "categoria": None,
            }
        ],
    }
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(doc_rend)
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage.prompt_tokens = 100
    mock_resp.usage.completion_tokens = 50

    with patch("app.routers.whatsapp.extraccion_documento.get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = mock_resp
        res, err = extraer_movimientos_de_imagen(b"fake_bytes")
        assert err is None
        assert res is not None
        assert len(res.movimientos) == 0
        assert len(res.rendimientos) == 1
        assert res.total_vistos == 0

    # Caso B: Ningún movimiento válido -> ILEGIBLE
    doc_vacio = {
        "legible": True,
        "documento_tipo": "captura_actividad",
        "billetera_texto": None,
        "vencimiento": None,
        "movimientos": [],
    }
    mock_choice.message.content = json.dumps(doc_vacio)
    with patch("app.routers.whatsapp.extraccion_documento.get_openai_client") as mock_client:
        mock_client.return_value.chat.completions.create.return_value = mock_resp
        res, err = extraer_movimientos_de_imagen(b"fake_bytes")
        assert res is None
        assert err == "ILEGIBLE"


def test_textos_exactos_decision_9_camino_b():
    """D1: Verifica los textos exactos de la decisión 9 (camino B)."""
    from app.routers.whatsapp.lote_documento import preparar_rendimientos

    uid = uuid4()
    mock_db = MagicMock()

    b_inv = MagicMock()
    b_inv.id = uuid4()
    b_inv.nombre = "Ahorro con rendimiento"
    b_inv.es_inversion = True
    b_inv.tna = Decimal("34.00")
    b_inv.moneda = "ARS"

    # 1. Propuesta con rendimiento a anotar
    entidades = {
        "monto": Decimal("1000"),
        "rendimientos": [{"fecha": "2026-10-01", "monto": Decimal("5650.20")}],
    }
    # Mocking DB query para billeteras que rinden y duplicados
    mock_db.execute.return_value.scalars.return_value.all.return_value = [b_inv]
    mock_db.execute.return_value.scalar_one_or_none.return_value = None  # No existe duplicado

    r_anotar, avisos = preparar_rendimientos(mock_db, uid, entidades, None, "captura_actividad", camino="B")
    assert len(r_anotar) == 1
    assert len(avisos) == 1
    assert "Además anoto el rendimiento de $5.650,20 en Ahorro con rendimiento (el 1 de octubre); no cuenta como ingreso." in avisos[0]

    # 2. Rendimiento ya existente
    mock_db.execute.return_value.scalar_one_or_none.return_value = uuid4()  # Ya existe
    r_anotar_dup, avisos_dup = preparar_rendimientos(mock_db, uid, entidades, None, "captura_actividad", camino="B")
    assert len(r_anotar_dup) == 0
    assert "Ya tenías el rendimiento del 1 de octubre en Ahorro con rendimiento." in avisos_dup[0]

    # 3. Sin billetera usable (billetera que no rinde)
    mock_db.execute.return_value.scalars.return_value.all.return_value = []
    r_anotar_sin, avisos_sin = preparar_rendimientos(mock_db, uid, entidades, "Galicia", "captura_actividad", camino="B")
    assert len(r_anotar_sin) == 0
    assert "Vi un rendimiento de $5.650,20 que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras." in avisos_sin[0]

    # 4. Solo rendimientos camino B en armar_resultado_ia_documento
    res_solo = armar_resultado_ia_documento(
        entidades={},
        duplicados=[],
        billetera_nombre="Ahorro con rendimiento",
        se_asumio_principal=False,
        solo_rendimientos=True,
        camino="B",
    )
    assert res_solo["intent"] == "duplicado"
    assert res_solo["entidades"] == {}
    assert res_solo["respuesta_usuario"] == "Veo solo rendimientos. Los cargás desde Billeteras o mandame los gastos o ingresos que quieras anotar."


def test_prompt_sistema_contiene_fecha_hoy_y_reglas():
    """D1: Verifica que el prompt de sistema generado contenga la fecha de hoy y reglas de descripción/categoría."""
    import json
    from app.routers.whatsapp.extraccion_documento import extraer_movimientos_de_imagen
    from app.utils.fecha import hoy_argentina

    hoy = hoy_argentina()
    dias_semana = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_semana = dias_semana[hoy.weekday()]

    with patch("app.routers.whatsapp.extraccion_documento.get_openai_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.choices = [
            MagicMock(message=MagicMock(content=json.dumps({
                "legible": True,
                "documento_tipo": "otro",
                "billetera_texto": None,
                "vencimiento": None,
                "movimientos": [],
            })))
        ]
        mock_client.chat.completions.create.return_value = mock_resp

        extraer_movimientos_de_imagen(b"fake_bytes")

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages", [])
        prompt_sys = messages[0]["content"]

        # Fecha de hoy y día de semana
        assert hoy.isoformat() in prompt_sys
        assert dia_semana in prompt_sys

        # Reglas de descripción
        assert "descripcion es SOLO el nombre del comercio o persona" in prompt_sys
        assert "sin 'Pagaste'" in prompt_sys

        # Reglas de categoría
        assert "categoria solo si el nombre es una marca o comercio ampliamente conocido" in prompt_sys
        assert "null" in prompt_sys

        # Verbos de sentido
        assert "Pagaste" in prompt_sys and "Transferiste" in prompt_sys and "Enviaste" in prompt_sys
        assert "Te transfirieron" in prompt_sys and "Recibiste" in prompt_sys


def test_esquema_extraccion_descriptions_nuevas():
    """D1: Verifica que el ESQUEMA_EXTRACCION tenga las descriptions nuevas."""
    from app.routers.whatsapp.extraccion_documento import ESQUEMA_EXTRACCION

    props = ESQUEMA_EXTRACCION["json_schema"]["schema"]["properties"]["movimientos"]["items"]["properties"]
    desc_descripcion = props["descripcion"]["description"]
    desc_categoria = props["categoria"]["description"]

    assert "Solo el nombre del comercio o persona tal como aparece" in desc_descripcion
    assert "sin 'Pagaste'" in desc_descripcion
    assert "Categoría solo si es marca ampliamente conocida" in desc_categoria
    assert "null" in desc_categoria


def test_anotar_rendimientos_confirmados_manejo_errores():
    """D1: Verifica que _anotar_rendimientos_confirmados devuelva mensaje genérico ante Exception y detail ante HTTPException."""
    from app.routers.whatsapp.registro import _anotar_rendimientos_confirmados
    from fastapi import HTTPException

    mock_db = MagicMock()
    mock_user = MagicMock()
    mock_user.id = uuid4()

    entidades = {
        "rendimientos": [
            {
                "billetera_id": str(uuid4()),
                "billetera_nombre": "Mercado Pago",
                "monto": Decimal("500"),
                "fecha": "2026-10-01",
            }
        ]
    }

    # Caso 1: Excepción genérica -> devuelve exactamente "No pude anotar el rendimiento."
    with patch("app.services.rendimiento_billetera_service.confirmar_rendimiento", side_effect=Exception("Database error")):
        res = _anotar_rendimientos_confirmados(mock_db, mock_user, entidades)
        assert res.strip() == "No pude anotar el rendimiento."
        assert "Database error" not in res

    # Caso 2: HTTPException -> muestra su detail
    with patch("app.services.rendimiento_billetera_service.confirmar_rendimiento", side_effect=HTTPException(status_code=400, detail="Saldo insuficiente")):
        res_http = _anotar_rendimientos_confirmados(mock_db, mock_user, entidades)
        assert res_http.strip() == "No pude anotar el rendimiento: Saldo insuficiente."


def test_preparar_rendimientos_fechas_invalidas():
    """D1: Verifica que preparar_rendimientos con fecha de hace 70 días, futura o None avise y no anote."""
    from datetime import datetime, timezone, timedelta
    from app.routers.whatsapp.lote_documento import preparar_rendimientos
    from app.utils.fecha import hoy_argentina

    hoy = hoy_argentina()
    uid = uuid4()
    mock_db = MagicMock()

    b_inv = MagicMock()
    b_inv.id = uuid4()
    b_inv.nombre = "Ahorro Plus"
    b_inv.es_inversion = True
    b_inv.tna = Decimal("30.00")
    b_inv.fecha_ultimo_rendimiento = None
    b_inv.fecha_creacion = datetime(2026, 1, 1, tzinfo=timezone.utc)

    mock_db.execute.return_value.scalars.return_value.all.return_value = [b_inv]
    mock_db.execute.return_value.scalar_one_or_none.return_value = None

    # Caso hace 70 días
    fecha_70 = (hoy - timedelta(days=70)).isoformat()
    ent_70 = {"rendimientos": [{"monto": Decimal("100"), "fecha": fecha_70}]}
    r_70, av_70 = preparar_rendimientos(mock_db, uid, ent_70, None, "captura_actividad", camino="B")
    assert len(r_70) == 0
    assert len(av_70) == 1
    assert "Vi un rendimiento de $100 que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras." in av_70[0]

    # Caso futura (+5 días)
    fecha_fut = (hoy + timedelta(days=5)).isoformat()
    ent_fut = {"rendimientos": [{"monto": Decimal("100"), "fecha": fecha_fut}]}
    r_fut, av_fut = preparar_rendimientos(mock_db, uid, ent_fut, None, "captura_actividad", camino="B")
    assert len(r_fut) == 0
    assert len(av_fut) == 1
    assert "Vi un rendimiento de $100 que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras." in av_fut[0]

    # Caso None
    ent_none = {"rendimientos": [{"monto": Decimal("100"), "fecha": None}]}
    r_none, av_none = preparar_rendimientos(mock_db, uid, ent_none, None, "captura_actividad", camino="B")
    assert len(r_none) == 0
    assert len(av_none) == 1
    assert "Vi un rendimiento de $100 que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras." in av_none[0]


