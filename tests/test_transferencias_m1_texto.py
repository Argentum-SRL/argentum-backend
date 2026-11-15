# -*- coding: utf-8 -*-
"""
Tests unitarios estáticos y de texto puro para el Bloque M1 (transferencias).
Prueba TT1 (extraer_transferencias), TT2 (monto_en_palabras),
TT3 (extraer_fecha), TT4 (entidad_de_texto) y TT5 (estático).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
import pytest

from app.core.entidades import entidad_de_texto
from app.routers.whatsapp.montos_palabras import monto_en_palabras
from app.routers.whatsapp.transferencias_texto import (
    extraer_fecha,
    extraer_transferencias,
)

FECHA_FIJA_HOY = date(2026, 11, 14)


# ==============================================================================
# TT1: extraer_transferencias
# ==============================================================================
@pytest.mark.parametrize(
    "texto,esperado",
    [
        (
            "pase 20k de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "Pasé 20k de Galicia a Macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pasé 20 lucas de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "transferí 20000 del galicia al macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20000 desde galicia hacia macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "mandé 20k de mi cuenta de galicia a la de macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "20k de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "de galicia a macro 20k",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20.000 de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase $20000 de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "me pase 20k de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "transferi 20k a macro desde galicia",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "transferencia de 20k galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "saque 20k de galicia y los meti en macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "ingrese 20k a macro desde galicia",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pasé plata de galicia a macro, 20 mil",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase veinte mil de galicia a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20k al macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": None,
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20k de gali a macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "gali",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "cargue 5000 en mercado pago desde galicia",
            [{"monto": Decimal("5000"), "moneda": None, "origen": "galicia",
              "destino": "mercado pago", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase los 20.000 de mercado pago a la cuenta de banco macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "mercado pago",
              "destino": "banco macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 1 palo de galicia a macro",
            [{"monto": Decimal("1000000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase un palo de galicia a macro",
            [{"monto": Decimal("1000000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 1,5 palos de galicia a macro",
            [{"monto": Decimal("1500000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase un palo y medio de galicia a macro",
            [{"monto": Decimal("1500000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase medio palo de galicia a macro",
            [{"monto": Decimal("500000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase dos lucas de galicia a macro",
            [{"monto": Decimal("2000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 200 mil de galicia a macro",
            [{"monto": Decimal("200000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase treinta y cinco mil de galicia a macro",
            [{"monto": Decimal("35000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 1.500.000,50 de galicia a macro",
            [{"monto": Decimal("1500000.50"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20 mangos de galicia a macro",
            [{"monto": Decimal("20"), "moneda": "ARS", "origen": "galicia",
              "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 100 dolares a galicia",
            [{"monto": Decimal("100"), "moneda": "USD", "origen": None,
              "destino": "galicia", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pasé 50000 de Galicia a Efectivo USD",
            [{"monto": Decimal("50000"), "moneda": None, "origen": "galicia",
              "destino": "efectivo usd", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pasé 50 de Efectivo USD a Galicia",
            [{"monto": Decimal("50"), "moneda": None, "origen": "efectivo usd",
              "destino": "galicia", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pasé 10 mil de Galicia a Galicia",
            [{"monto": Decimal("10000"), "moneda": None, "origen": "galicia",
              "destino": "galicia", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase un palo y medio de bna a bapro",
            [{"monto": Decimal("1500000"), "moneda": None, "origen": "bna",
              "destino": "bapro", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 5 lucas del banco nacion a la cuenta dni",
            [{"monto": Decimal("5000"), "moneda": None, "origen": "banco nacion",
              "destino": "cuenta dni", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20k de galicia a macro ayer",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia",
              "fecha": date(2026, 11, 13), "comision": None}],
        ),
        (
            "pasé 20k de galicia a macro el lunes",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia",
              "fecha": date(2026, 11, 9), "comision": None}],
        ),
        (
            "pase 20k de galicia a macro el 10/11",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia",
              "fecha": date(2026, 11, 10), "comision": None}],
        ),
        (
            "pase 20k de galicia a macro el 3 de noviembre",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia",
              "fecha": date(2026, 11, 3), "comision": None}],
        ),
        (
            "transferí 20k a macro desde galicia anteayer",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia",
              "fecha": date(2026, 11, 12), "comision": None}],
        ),
        (
            "pasé 20k de galicia a macro con 300 de comision",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": "macro", "tipo": "transferencia",
              "fecha": None, "comision": Decimal("300")}],
        ),
        (
            "saque 10k del cajero",
            [{"monto": Decimal("10000"), "moneda": None, "origen": None,
              "destino": None, "tipo": "extraccion", "fecha": None, "comision": None}],
        ),
        (
            "saque 10 lucas del galicia",
            [{"monto": Decimal("10000"), "moneda": None, "origen": "galicia",
              "destino": None, "tipo": "extraccion", "fecha": None, "comision": None}],
        ),
        (
            "fui al cajero y saque 20k de macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "macro",
              "destino": None, "tipo": "extraccion", "fecha": None, "comision": None}],
        ),
        (
            "saque 10k del cajero con 500 de comision",
            [{"monto": Decimal("10000"), "moneda": None, "origen": None,
              "destino": None, "tipo": "extraccion", "fecha": None, "comision": Decimal("500")}],
        ),
        (
            "deposite 20k en macro",
            [{"monto": Decimal("20000"), "moneda": None, "origen": None,
              "destino": "macro", "tipo": "deposito", "fecha": None, "comision": None}],
        ),
        (
            "pase 20 lucas a mp",
            [{"monto": Decimal("20000"), "moneda": None, "origen": None,
              "destino": "mp", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "me transferi 15k a mp",
            [{"monto": Decimal("15000"), "moneda": None, "origen": None,
              "destino": "mp", "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20k de galicia",
            [{"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
              "destino": None, "tipo": "transferencia", "fecha": None, "comision": None}],
        ),
        (
            "pase 20k de galicia a macro y 10k de mp a uala",
            [
                {"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
                 "destino": "macro", "tipo": "transferencia", "fecha": None, "comision": None},
                {"monto": Decimal("10000"), "moneda": None, "origen": "mp",
                 "destino": "uala", "tipo": "transferencia", "fecha": None, "comision": None},
            ],
        ),
        (
            "pasé 20k de galicia a santander, 5k de santander a efectivo",
            [
                {"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
                 "destino": "santander", "tipo": "transferencia", "fecha": None, "comision": None},
                {"monto": Decimal("5000"), "moneda": None, "origen": "santander",
                 "destino": "efectivo", "tipo": "transferencia", "fecha": None, "comision": None},
            ],
        ),
        (
            "pasé 20k de galicia a santander y 5k a mercado pago",
            [
                {"monto": Decimal("20000"), "moneda": None, "origen": "galicia",
                 "destino": "santander", "tipo": "transferencia",
                 "fecha": None, "comision": None},
                {"monto": Decimal("5000"), "moneda": None, "origen": None,
                 "destino": "mercado pago", "tipo": "transferencia",
                 "fecha": None, "comision": None},
            ],
        ),
        # Casos None
        ("le pase 20k a macro", None),
        ("gaste 5000 en el kiosco y pase 20000 de galicia a macro", None),
        ("pase 20k de galicia a macro y gaste 3000 en el kiosco", None),
        ("pase al kiosco y me compre un chocolate por 2.790", None),
        ("cargue 2000 en la sube", None),
        ("cargue 30000 de nafta", None),
        ("pase 20k", None),
        ("le transferí 5000 a mi hermano y también gasté 3000 en el kiosco", None),
        ("le pasé 5000 a Juan y transferí 20000 de Galicia a Santander", None),
        ("compré 100 dólares a 1500", None),
        ("ingrese 20k a macro", None),
    ],
)
def test_tt1_extraer_transferencias(texto, esperado):
    res = extraer_transferencias(texto, FECHA_FIJA_HOY)
    assert res == esperado


# ==============================================================================
# TT2: monto_en_palabras
# ==============================================================================
@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("un palo", Decimal("1000000")),
        ("medio palo", Decimal("500000")),
        ("un palo y medio", Decimal("1500000")),
        ("palo y medio", Decimal("1500000")),
        ("dos palos", Decimal("2000000")),
        ("una luca", Decimal("1000")),
        ("dos lucas", Decimal("2000")),
        ("luca y media", Decimal("1500")),
        ("diez lucas", Decimal("10000")),
        ("veinte lucas", Decimal("20000")),
        ("veinte mil", Decimal("20000")),
        ("veinticinco mil", Decimal("25000")),
        ("treinta y cinco mil", Decimal("35000")),
        ("cien mil", Decimal("100000")),
        ("ciento cincuenta mil", Decimal("150000")),
        ("doscientos mil", Decimal("200000")),
        ("quinientos", Decimal("500")),
        ("mil quinientos", Decimal("1500")),
        ("mil", Decimal("1000")),
        ("un millon", Decimal("1000000")),
        ("millon y medio", Decimal("1500000")),
        ("dos millones", Decimal("2000000")),
        ("una gamba", None),
        ("dos gambas", None),
        ("kiosco", None),
        ("galicia", None),
    ],
)
def test_tt2_monto_en_palabras(texto, esperado):
    assert monto_en_palabras(texto) == esperado


# ==============================================================================
# TT3: extraer_fecha
# ==============================================================================
@pytest.mark.parametrize(
    "texto,esperado_fecha",
    [
        ("ayer", date(2026, 11, 13)),
        ("anteayer", date(2026, 11, 12)),
        ("antes de ayer", date(2026, 11, 12)),
        ("hoy", date(2026, 11, 14)),
        ("el lunes", date(2026, 11, 9)),
        ("el viernes", date(2026, 11, 13)),
        ("el sabado", date(2026, 11, 7)),
        ("el domingo pasado", date(2026, 11, 8)),
        ("el 10/11", date(2026, 11, 10)),
        ("el 10/11/2025", date(2025, 11, 10)),
        ("el 3 de noviembre", date(2026, 11, 3)),
        ("el 20/11", date(2026, 11, 20)),
        ("el 30/12", date(2025, 12, 30)),
        ("pase 20.000", None),
        ("1,5 palos", None),
        ("1.5", None),
        ("sin fecha", None),
    ],
)
def test_tt3_extraer_fecha(texto, esperado_fecha):
    f_res, txt_sin = extraer_fecha(texto, FECHA_FIJA_HOY)
    assert f_res == esperado_fecha
    if esperado_fecha is not None:
        assert txt_sin == ""


# ==============================================================================
# TT4: entidad_de_texto
# ==============================================================================
@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("macro", "macro"),
        ("banco macro", "macro"),
        ("bna", "nacion"),
        ("banco nacion", "nacion"),
        ("nacion", "nacion"),
        ("banco de la nacion", "nacion"),
        ("Banco Nación", "nacion"),
        ("bapro", "provincia"),
        ("banco provincia", "provincia"),
        ("cuenta dni", "cuentadni"),
        ("dni", "cuentadni"),
        ("mp", "mercadopago"),
        ("Mercado Pago", "mercadopago"),
        ("naranja x", "naranjax"),
        ("bru", "brubank"),
        ("gali", "galicia"),
        ("rio", "santander"),
        ("frances", "bbva"),
        ("uala", "uala"),
        ("invertir online", "iol"),
        ("santa fe", "bancosantafe"),
        ("juan", None),
        ("kiosco", None),
        ("efectivo", None),
        ("ahorro", None),
    ],
)
def test_tt4_entidad_de_texto(texto, esperado):
    assert entidad_de_texto(texto) == esperado


# ==============================================================================
# TT5: Controles estáticos
# ==============================================================================
def test_tt5_estatico():
    repo_root = Path(__file__).resolve().parents[1]

    # Archivos nuevos y sus topes de líneas
    topes = {
        "app/routers/whatsapp/montos_palabras.py": 220,
        "app/routers/whatsapp/transferencias_texto.py": 500,
        "app/routers/whatsapp/transferencias_cuentas.py": 320,
    }

    for rel_path, tope in topes.items():
        file_path = repo_root / rel_path
        if not file_path.exists():
            continue
        lines = file_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) <= tope, (
            f"{rel_path} supera el tope de {tope} líneas: tiene {len(lines)}"
        )
        for i, line in enumerate(lines, 1):
            assert len(line) <= 100, (
                f"{rel_path}:{i} supera 100 caracteres ({len(line)}): {line}"
            )
