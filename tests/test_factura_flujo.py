"""
tests/test_factura_flujo.py
Pruebas unitarias para el flujo de facturas con vencimiento (Fase 4c2b2b).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.routers.whatsapp.factura_wpp import texto_propuesta_factura
from app.routers.whatsapp.pdf_documento import vencimiento_por_texto
from app.utils.fecha import hoy_argentina


def test_texto_propuesta_un_vencimiento_fechas_y_asuncion():
    hoy = hoy_argentina()

    # 1. hoy + 5
    v_fut = hoy + timedelta(days=5)
    f_fut = v_fut.strftime("%d/%m")
    ent_fut = {
        "monto": Decimal("12345.67"),
        "categoria": "Agua",
        "descripcion": "Aguas Santafesinas",
        "billetera": "Galicia",
        "moneda": "ARS",
        "factura": {
            "empresa": "Aguas Santafesinas",
            "origen": "whatsapp_foto",
            "vencimientos": [{"fecha": v_fut.isoformat(), "monto": "12345.67"}],
        },
    }
    # Asumida
    txt_fut_asum = texto_propuesta_factura(ent_fut, billetera_nombre="Galicia", se_asumio_principal=True)
    esp_fut_asum = (
        f"Factura de Aguas Santafesinas por $12.345,67, vence el {f_fut}.\n"
        f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
        f"Si fue con otra, decime cuál.\n"
        f"Si me decís que no, te la anoto en la web para que no se te pase."
    )
    assert txt_fut_asum == esp_fut_asum

    # No asumida
    txt_fut_no_asum = texto_propuesta_factura(ent_fut, billetera_nombre="Galicia", se_asumio_principal=False)
    esp_fut_no_asum = (
        f"Factura de Aguas Santafesinas por $12.345,67, vence el {f_fut}.\n"
        f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
        f"Si me decís que no, te la anoto en la web para que no se te pase."
    )
    assert txt_fut_no_asum == esp_fut_no_asum

    # 2. hoy
    ent_hoy = {
        "monto": Decimal("12345.67"),
        "categoria": "Agua",
        "descripcion": "Aguas Santafesinas",
        "billetera": "Galicia",
        "moneda": "ARS",
        "factura": {
            "empresa": "Aguas Santafesinas",
            "origen": "whatsapp_foto",
            "vencimientos": [{"fecha": hoy.isoformat(), "monto": "12345.67"}],
        },
    }
    txt_hoy_asum = texto_propuesta_factura(ent_hoy, billetera_nombre="Galicia", se_asumio_principal=True)
    esp_hoy_asum = (
        "Factura de Aguas Santafesinas por $12.345,67, vence hoy.\n"
        "¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
        "Si fue con otra, decime cuál.\n"
        "Si me decís que no, te la anoto en la web para que no se te pase."
    )
    assert txt_hoy_asum == esp_hoy_asum

    txt_hoy_no_asum = texto_propuesta_factura(ent_hoy, billetera_nombre="Galicia", se_asumio_principal=False)
    esp_hoy_no_asum = (
        "Factura de Aguas Santafesinas por $12.345,67, vence hoy.\n"
        "¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
        "Si me decís que no, te la anoto en la web para que no se te pase."
    )
    assert txt_hoy_no_asum == esp_hoy_no_asum

    # 3. hoy - 3
    v_pas = hoy - timedelta(days=3)
    f_pas = v_pas.strftime("%d/%m")
    ent_pas = {
        "monto": Decimal("12345.67"),
        "categoria": "Agua",
        "descripcion": "Aguas Santafesinas",
        "billetera": "Galicia",
        "moneda": "ARS",
        "factura": {
            "empresa": "Aguas Santafesinas",
            "origen": "whatsapp_foto",
            "vencimientos": [{"fecha": v_pas.isoformat(), "monto": "12345.67"}],
        },
    }
    txt_pas_asum = texto_propuesta_factura(ent_pas, billetera_nombre="Galicia", se_asumio_principal=True)
    esp_pas_asum = (
        f"Factura de Aguas Santafesinas por $12.345,67, venció el {f_pas}.\n"
        f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
        f"Si fue con otra, decime cuál.\n"
        f"Si me decís que no, te la anoto en la web para que no se te pase."
    )
    assert txt_pas_asum == esp_pas_asum

    txt_pas_no_asum = texto_propuesta_factura(ent_pas, billetera_nombre="Galicia", se_asumio_principal=False)
    esp_pas_no_asum = (
        f"Factura de Aguas Santafesinas por $12.345,67, venció el {f_pas}.\n"
        f"¿Ya la pagaste? Si me decís que sí, la anoto como gasto de hoy en Agua desde Galicia.\n"
        f"Si me decís que no, te la anoto en la web para que no se te pase."
    )
    assert txt_pas_no_asum == esp_pas_no_asum


def test_texto_propuesta_cuotas():
    hoy = hoy_argentina()

    # 1. 2 cuotas iguales
    v1 = hoy + timedelta(days=6)
    v2 = hoy + timedelta(days=36)
    f1 = v1.strftime("%d/%m")
    f2 = v2.strftime("%d/%m")
    ent_2_iguales = {
        "monto": Decimal("77597.44"),
        "categoria": "Luz",
        "descripcion": "EPE",
        "billetera": "Galicia",
        "moneda": "ARS",
        "factura": {
            "empresa": "EPE",
            "origen": "whatsapp_pdf",
            "vencimientos": [
                {"fecha": v1.isoformat(), "monto": "77597.44"},
                {"fecha": v2.isoformat(), "monto": "77597.44"},
            ],
        },
    }
    txt_2_iguales = texto_propuesta_factura(ent_2_iguales, billetera_nombre="Galicia", se_asumio_principal=True)
    esp_2_iguales = (
        f"Factura de EPE en 2 cuotas de $77.597,44: vencen el {f1} y el {f2}.\n"
        f"¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy en Luz desde Galicia y te anoto la segunda en la web.\n"
        f"Si fue con otra, decime cuál.\n"
        f"Si me decís que no, te anoto las dos en la web para que no se te pasen."
    )
    assert txt_2_iguales == esp_2_iguales

    # 2. 2 cuotas distintas
    ent_2_distintas = {
        "monto": Decimal("50000"),
        "categoria": "Luz",
        "descripcion": "EPE",
        "billetera": "Galicia",
        "moneda": "ARS",
        "factura": {
            "empresa": "EPE",
            "origen": "whatsapp_pdf",
            "vencimientos": [
                {"fecha": v1.isoformat(), "monto": "50000"},
                {"fecha": v2.isoformat(), "monto": "60000"},
            ],
        },
    }
    txt_2_distintas = texto_propuesta_factura(ent_2_distintas, billetera_nombre="Galicia", se_asumio_principal=True)
    esp_2_distintas = (
        f"Factura de EPE en 2 cuotas: $50.000 que vence el {f1} y $60.000 que vence el {f2}.\n"
        f"¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy en Luz desde Galicia y te anoto la segunda en la web.\n"
        f"Si fue con otra, decime cuál.\n"
        f"Si me decís que no, te anoto las dos en la web para que no se te pasen."
    )
    assert txt_2_distintas == esp_2_distintas

    # 3. 3 cuotas iguales
    v3 = hoy + timedelta(days=66)
    f3 = v3.strftime("%d/%m")
    ent_3_iguales = {
        "monto": Decimal("25000"),
        "categoria": "Luz",
        "descripcion": "EPE",
        "billetera": "Galicia",
        "moneda": "ARS",
        "factura": {
            "empresa": "EPE",
            "origen": "whatsapp_pdf",
            "vencimientos": [
                {"fecha": v1.isoformat(), "monto": "25000"},
                {"fecha": v2.isoformat(), "monto": "25000"},
                {"fecha": v3.isoformat(), "monto": "25000"},
            ],
        },
    }
    txt_3_iguales = texto_propuesta_factura(ent_3_iguales, billetera_nombre="Galicia", se_asumio_principal=False)
    esp_3_iguales = (
        f"Factura de EPE en 3 cuotas de $25.000: vencen el {f1}, el {f2} y el {f3}.\n"
        f"¿Ya pagaste la primera? Si me decís que sí, la anoto como gasto de hoy en Luz desde Galicia y te anoto las otras 2 en la web.\n"
        f"Si me decís que no, te anoto las 3 en la web para que no se te pasen."
    )
    assert txt_3_iguales == esp_3_iguales


def test_vencimiento_por_texto():
    # 1. TOTAL A PAGAR hasta el 13/10/2026 con CESP y otros descartados
    t1 = (
        "TOTAL A PAGAR hasta el 13/10/2026 "
        "C.E.S.P. Nro.: 37390006516961 F.Vto.: 07/10/2026 "
        "Comprobante Vencimiento Importe\nF 0081-34498428 15/09/2023"
    )
    assert vencimiento_por_texto(t1) == date(2026, 10, 13)

    # 2. VENCIMIENTO:\n05/10/2026 y vence el día 05/10/2026
    t2 = (
        "VENCIMIENTO:\n05/10/2026\n"
        "vence el día 05/10/2026\n"
        "Próximo Vencimiento Estimado: 03/11/2026"
    )
    assert vencimiento_por_texto(t2) == date(2026, 10, 5)

    # 3. Cuota 1 13/10/2026 y Fecha de vencimiento próxima Factura: 14/12/2026 -> None
    t3 = "Cuota 1 13/10/2026\nFecha de vencimiento próxima Factura: 14/12/2026"
    assert vencimiento_por_texto(t3) is None

    # 4. Dos fechas distintas después de vence el -> None
    t4 = "vence el 10/10/2026 y también vence el 20/10/2026"
    assert vencimiento_por_texto(t4) is None


def test_linea_facturas_pagadas(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from uuid import uuid4

    from app.routers.whatsapp.factura_wpp import linea_facturas_pagadas

    db_mock = MagicMock()
    tx1 = uuid4()
    tx2 = uuid4()

    # 1. Sin facturas: ""
    assert linea_facturas_pagadas(db_mock, []) == ""

    monkeypatch.setattr("app.services.factura_service.facturas_pagadas_por", lambda db, ids: [])
    assert linea_facturas_pagadas(db_mock, [tx1]) == ""

    # 2. 1 factura pagada automáticamente: una línea con el texto exacto
    f1 = SimpleNamespace(transaccion_id=tx1, descripcion="Aguas Santafesinas", pagada_automaticamente=True)
    monkeypatch.setattr("app.services.factura_service.facturas_pagadas_por", lambda db, ids: [f1])
    assert linea_facturas_pagadas(db_mock, [tx1]) == "\nMarqué pagada la factura de Aguas Santafesinas."

    # 3. 2 facturas: dos líneas, en orden
    f2 = SimpleNamespace(transaccion_id=tx2, descripcion="EPE", pagada_automaticamente=True)
    # Devueltas desordenadas por la base para verificar que ordena según transaccion_ids
    monkeypatch.setattr("app.services.factura_service.facturas_pagadas_por", lambda db, ids: [f2, f1])

    res_1_2 = linea_facturas_pagadas(db_mock, [tx1, tx2])
    assert res_1_2 == "\nMarqué pagada la factura de Aguas Santafesinas.\nMarqué pagada la factura de EPE."

    res_2_1 = linea_facturas_pagadas(db_mock, [tx2, tx1])
    assert res_2_1 == "\nMarqué pagada la factura de EPE.\nMarqué pagada la factura de Aguas Santafesinas."

    # 4. Una factura pagada con factura_id explícito (pagada_automaticamente false): no aparece
    f_explicita = SimpleNamespace(transaccion_id=tx1, descripcion="Telecom", pagada_automaticamente=False)
    monkeypatch.setattr("app.services.factura_service.facturas_pagadas_por", lambda db, ids: [f_explicita])
    assert linea_facturas_pagadas(db_mock, [tx1]) == ""

