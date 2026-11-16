# -*- coding: utf-8 -*-
"""
tests/test_permitirse_bateria_t1.py — Batería de 30 personas al azar e invariantes I1 a I6.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import random
import re
import sqlite3
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
import sqlalchemy.types as types

from app.core.database import Base
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.cotizacion_dolar import CotizacionDolar
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.meta import EstadoMeta, Meta
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.suscripcion import EstadoSuscripcion, FrecuenciaSuscripcion, Suscripcion
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    OrigenTransaccion,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import AuthProvider, CicloTipo, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.services.permitirse_evaluacion import ORDEN_RESULTADOS
from app.services.permitirse_service import recolectar, resolver_consulta
from app.services.permitirse_textos import armar_respuesta_permitirse
from app.utils.fecha import TZ_ARGENTINA

HOY_FIJO = date(2026, 11, 17)
FECHA_SALDO_FIJA = date(2026, 3, 1)
CICLOS = [
    date(2026, 3, 1),
    date(2026, 4, 1),
    date(2026, 5, 1),
    date(2026, 6, 1),
    date(2026, 7, 1),
    date(2026, 8, 1),
    date(2026, 9, 1),
    date(2026, 10, 1),
]


@pytest.fixture(autouse=True)
def _compat_y_fecha(monkeypatch):
    had_sqlite = (
        hasattr(JSONB, "_compiler_dispatcher")
        and "sqlite" in getattr(JSONB._compiler_dispatcher, "specs", {})
    )
    if not had_sqlite:
        compiles(JSONB, "sqlite")(lambda type_, compiler, **kw: "TEXT")

    orig_uuid_processor = types.Uuid.bind_processor

    def _safe_uuid_processor(self, dialect):
        proc = orig_uuid_processor(self, dialect)
        if proc is None:
            return None

        def process(value):
            if isinstance(value, str):
                try:
                    value = uuid.UUID(value)
                except Exception:
                    pass
            return proc(value)

        return process

    monkeypatch.setattr(types.Uuid, "bind_processor", _safe_uuid_processor)
    sqlite3.register_adapter(uuid.UUID, lambda u: u.hex)

    for mod in (
        "app.utils.fecha",
        "app.services.permitirse_service",
        "app.services.fin_de_mes_service",
        "app.services.frescura_service",
        "app.services.compromisos_service",
        "app.services.ingreso_habitual_service",
        "app.services.definiciones_service",
        "app.services.pagos_proximos_service",
        "app.services.dolar_service",
        "app.services.tools_service",
    ):
        monkeypatch.setattr(f"{mod}.hoy_argentina", lambda: HOY_FIJO, raising=False)

    yield

    if not had_sqlite:
        if hasattr(JSONB, "_compiler_dispatcher") and hasattr(JSONB._compiler_dispatcher, "specs"):
            JSONB._compiler_dispatcher.specs.pop("sqlite", None)


@pytest.fixture(name="db")
def db_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import app.models
    _ = app.models
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def _seed_catalogo(db: Session) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    for fecha_cot in (date(2026, 1, 1), HOY_FIJO):
        db.add(CotizacionDolar(
            fecha=fecha_cot,
            tipo="oficial",
            compra=Decimal("1400.00"),
            venta=Decimal("1450.00"),
            promedio=Decimal("1425.00"),
            fecha_registro=datetime(2026, 11, 17, 10, 0, tzinfo=TZ_ARGENTINA),
        ))

    c_ing = Categoria(
        id=uuid4(), nombre="Trabajo", tipo=TipoCategoria.INGRESO, estado=EstadoCategoria.ACTIVA
    )
    c_ali = Categoria(
        id=uuid4(), nombre="Alimentación", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA
    )
    c_gas = Categoria(
        id=uuid4(), nombre="Gastronomía", tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA
    )
    db.add_all([c_ing, c_ali, c_gas])
    db.flush()

    s_sue = Subcategoria(
        id=uuid4(), categoria_id=c_ing.id, nombre="Sueldo", estado=EstadoSubcategoria.ACTIVA
    )
    s_sup = Subcategoria(
        id=uuid4(), categoria_id=c_ali.id, nombre="Supermercado", estado=EstadoSubcategoria.ACTIVA
    )
    s_del = Subcategoria(
        id=uuid4(), categoria_id=c_gas.id, nombre="Delivery", estado=EstadoSubcategoria.ACTIVA
    )
    db.add_all([s_sue, s_sup, s_del])
    db.commit()
    return {
        "Sueldo": (c_ing.id, s_sue.id),
        "Supermercado": (c_ali.id, s_sup.id),
        "Delivery": (c_gas.id, s_del.id),
    }


def _armar_persona(
    db: Session,
    rng: random.Random,
    idx: int,
    cats: dict[str, tuple[uuid.UUID, uuid.UUID]],
) -> Usuario:
    perfil_moneda = rng.choice(["pesos", "dolares", "mixta"])
    moneda_p = Moneda.USD if perfil_moneda == "dolares" else Moneda.ARS
    u = Usuario(
        id=uuid4(),
        email=f"bat_{idx}@argentum.com",
        auth_provider=AuthProvider.EMAIL,
        rol=RolUsuario.USUARIO,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=moneda_p,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        fecha_registro=datetime(2026, 2, 1, 10, 0, tzinfo=TZ_ARGENTINA),
    )
    u.is_admin = False
    db.add(u)
    db.flush()

    saldo_ars = Decimal(str(rng.choice([0, 80000, 350000, 1500000, 5000000])))
    b_ars = Billetera(
        id=uuid4(),
        usuario_id=u.id,
        nombre="Banco",
        moneda=Moneda.ARS,
        estado=EstadoBilletera.ACTIVA,
        es_inversion=False,
        saldo_actual=saldo_ars,
        saldo_inicial=saldo_ars,
        fecha_saldo=FECHA_SALDO_FIJA,
        tiene_punto_partida=True,
    )
    db.add(b_ars)

    b_usd = None
    if perfil_moneda in ("dolares", "mixta") or rng.random() < 0.5:
        saldo_usd = Decimal(str(rng.choice([200, 1500, 5000])))
        b_usd = Billetera(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Caja USD",
            moneda=Moneda.USD,
            estado=EstadoBilletera.ACTIVA,
            es_inversion=False,
            saldo_actual=saldo_usd,
            saldo_inicial=saldo_usd,
            fecha_saldo=FECHA_SALDO_FIJA,
            tiene_punto_partida=True,
        )
        db.add(b_usd)

    if rng.random() < 0.5:
        db.add(Billetera(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Fondo",
            moneda=Moneda.ARS,
            estado=EstadoBilletera.ACTIVA,
            es_inversion=True,
            saldo_actual=Decimal(str(rng.choice([100000, 800000]))),
            saldo_inicial=Decimal("0"),
            tiene_punto_partida=False,
        ))

    if rng.random() < 0.4:
        db.add(Meta(
            id=uuid4(),
            usuario_id=u.id,
            nombre="Viaje",
            monto_objetivo=Decimal("1000000"),
            monto_actual=Decimal(str(rng.choice([50000, 300000]))),
            moneda=Moneda.ARS,
            estado=EstadoMeta.ACTIVA,
        ))

    tipo_ingreso = rng.choice(["regular", "variable", "sin_ingreso"])
    base_ing = Decimal(str(rng.choice([700000, 1200000, 2000000])))
    cant_ciclos = rng.choice([2, 5, 8])
    ciclos_u = CICLOS[-cant_ciclos:]

    for ini in ciclos_u:
        if tipo_ingreso != "sin_ingreso":
            factor = Decimal("1")
            if tipo_ingreso == "variable":
                factor = Decimal(str(rng.choice([0.6, 0.9, 1.1, 1.5])))
            if perfil_moneda == "dolares" and b_usd is not None:
                db.add(Transaccion(
                    id=uuid4(),
                    usuario_id=u.id,
                    billetera_id=b_usd.id,
                    categoria_id=cats["Sueldo"][0],
                    subcategoria_id=cats["Sueldo"][1],
                    tipo=TipoTransaccion.INGRESO,
                    moneda=Moneda.USD,
                    monto=(base_ing / Decimal("1400") * factor).quantize(Decimal("0.01")),
                    fecha=ini,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    fecha_creacion=datetime(ini.year, ini.month, 1, 11, 0, tzinfo=TZ_ARGENTINA),
                ))
            else:
                db.add(Transaccion(
                    id=uuid4(),
                    usuario_id=u.id,
                    billetera_id=b_ars.id,
                    categoria_id=cats["Sueldo"][0],
                    subcategoria_id=cats["Sueldo"][1],
                    tipo=TipoTransaccion.INGRESO,
                    moneda=Moneda.ARS,
                    monto=(base_ing * factor).quantize(Decimal("0.01")),
                    fecha=ini,
                    origen=OrigenTransaccion.MANUAL,
                    estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                    fecha_creacion=datetime(ini.year, ini.month, 1, 11, 0, tzinfo=TZ_ARGENTINA),
                ))

        gasto_ciclo = Decimal(str(rng.choice([300000, 650000, 1100000])))
        db.add(Transaccion(
            id=uuid4(),
            usuario_id=u.id,
            billetera_id=b_ars.id,
            categoria_id=cats["Supermercado"][0],
            subcategoria_id=cats["Supermercado"][1],
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.DEBITO,
            moneda=Moneda.ARS,
            monto=gasto_ciclo,
            fecha=ini.replace(day=12),
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime(ini.year, ini.month, 12, 13, 0, tzinfo=TZ_ARGENTINA),
        ))
        if rng.random() < 0.6:
            db.add(Transaccion(
                id=uuid4(),
                usuario_id=u.id,
                billetera_id=b_ars.id,
                categoria_id=cats["Delivery"][0],
                subcategoria_id=cats["Delivery"][1],
                tipo=TipoTransaccion.EGRESO,
                metodo_pago=MetodoPago.DEBITO,
                moneda=Moneda.ARS,
                monto=Decimal(str(rng.choice([40000, 90000, 150000]))),
                fecha=ini.replace(day=16),
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                fecha_creacion=datetime(ini.year, ini.month, 16, 14, 0, tzinfo=TZ_ARGENTINA),
            ))

        if b_usd is not None and perfil_moneda == "mixta" and rng.random() < 0.6:
            db.add(Transaccion(
                id=uuid4(),
                usuario_id=u.id,
                billetera_id=b_usd.id,
                categoria_id=cats["Delivery"][0],
                subcategoria_id=cats["Delivery"][1],
                tipo=TipoTransaccion.EGRESO,
                metodo_pago=MetodoPago.DEBITO,
                moneda=Moneda.USD,
                monto=Decimal("50"),
                fecha=ini.replace(day=18),
                origen=OrigenTransaccion.MANUAL,
                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                fecha_creacion=datetime(ini.year, ini.month, 18, 15, 0, tzinfo=TZ_ARGENTINA),
            ))

    # Carga reciente en el ciclo actual
    db.add(Transaccion(
        id=uuid4(),
        usuario_id=u.id,
        billetera_id=b_ars.id,
        categoria_id=cats["Supermercado"][0],
        subcategoria_id=cats["Supermercado"][1],
        tipo=TipoTransaccion.EGRESO,
        metodo_pago=MetodoPago.DEBITO,
        moneda=Moneda.ARS,
        monto=Decimal("45000"),
        fecha=date(2026, 11, 15),
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
        fecha_creacion=datetime(2026, 11, 15, 16, 0, tzinfo=TZ_ARGENTINA),
    ))

    if rng.random() < 0.5:
        sub = Suscripcion(
            id=uuid4(),
            usuario_id=u.id,
            billetera_id=b_ars.id,
            nombre="Streaming",
            frecuencia=FrecuenciaSuscripcion.MENSUAL,
            proximo_cobro=date(2026, 11, 25),
            estado=EstadoSuscripcion.ACTIVA,
        )
        db.add(sub)
        db.flush()
        db.add(HistorialSuscripcion(
            id=uuid4(),
            suscripcion_id=sub.id,
            monto=Decimal("25000"),
            moneda=Moneda.ARS,
            vigente_desde=date(2026, 1, 1),
        ))

    if rng.random() < 0.4:
        tx_cuota = Transaccion(
            id=uuid4(),
            usuario_id=u.id,
            billetera_id=b_ars.id,
            categoria_id=cats["Supermercado"][0],
            subcategoria_id=cats["Supermercado"][1],
            tipo=TipoTransaccion.EGRESO,
            metodo_pago=MetodoPago.CREDITO,
            moneda=Moneda.ARS,
            monto=Decimal("240000"),
            fecha=date(2026, 10, 15),
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime(2026, 10, 15, 16, 0, tzinfo=TZ_ARGENTINA),
        )
        db.add(tx_cuota)
        db.flush()
        g = GrupoCuotas(
            id=uuid4(),
            usuario_id=u.id,
            transaccion_padre_id=tx_cuota.id,
            descripcion="Compra cuotas",
            monto_total=Decimal("240000"),
            total_financiado=Decimal("240000"),
            moneda=Moneda.ARS,
            cantidad_cuotas=4,
            tasa_interes=Decimal("0"),
            tiene_interes=False,
        )
        db.add(g)
        db.flush()
        for k in range(1, 5):
            db.add(Cuota(
                id=uuid4(),
                grupo_id=g.id,
                transaccion_id=tx_cuota.id,
                numero_cuota=k,
                monto_proyectado=Decimal("60000"),
                fecha_vencimiento=HOY_FIJO + timedelta(days=30 * k),
                pagada=False,
            ))

    db.commit()
    return u


def _chequear_textos_i6(res: dict, cons: dict) -> None:
    wpp = armar_respuesta_permitirse(res, cons)
    for linea in wpp.split("\n"):
        assert len(linea) <= 90, f"Línea WPP > 90 ({len(linea)}): {linea}"
    textos = [
        res.get("titulo") or "",
        res.get("linea_principal") or "",
        *(res.get("lineas_plan") or []),
        res.get("linea_ahorros") or "",
        res.get("linea_liberacion") or "",
        res.get("linea_compensar") or "",
        res.get("linea_frescura") or "",
        res.get("linea_espera") or "",
        res.get("linea_cotizacion") or "",
        res.get("linea_cuentas") or "",
        res.get("linea_reintegro") or "",
        res.get("linea_juntar") or "",
        wpp,
    ]
    for t in textos:
        for mal in ("REM", "ARS", "USD", "None"):
            assert mal not in t, f"Aparece '{mal}' en: {t}"
        assert not re.search(r"\bnan\b", t, re.IGNORECASE), f"Aparece nan en: {t}"


def test_bateria_30_personas_invariantes_i1_a_i6(db: Session):
    cats = _seed_catalogo(db)
    rng = random.Random(2026)

    conteos = {"I1": 0, "I2": 0, "I3": 0, "I4": 0, "I5": 0, "I6": 0}
    total_consultas = 0

    for idx in range(30):
        u = _armar_persona(db, rng, idx, cats)
        datos = recolectar(db, u, hoy=HOY_FIJO)

        for _ in range(20):
            total_consultas += 1
            moneda_q = rng.choice(["ARS", "USD"]) if datos.usa_dolares else "ARS"
            if moneda_q == "USD":
                precio = Decimal(str(rng.choice([20, 80, 250, 800, 2500])))
            else:
                precio = Decimal(str(rng.choice([15000, 80000, 250000, 900000, 3500000])))
            modo = rng.choice(["contado", "cuotas", "mensual"])
            cuotas = rng.randint(1, 24) if modo == "cuotas" else 1
            tiene_int = (modo == "cuotas") and (rng.random() < 0.4)
            tna = Decimal(str(rng.choice([40, 80, 120]))) if tiene_int else None

            res = resolver_consulta(
                db, u, datos, precio, modo, cuotas, tiene_int, tna, HOY_FIJO, moneda_q
            )

            # I6: largo <= 90 y sin tecnicismos ni basura
            cons_wpp = {
                "modo": modo,
                "cantidad_cuotas": cuotas,
                "nota_interes": False,
            }
            _chequear_textos_i6(res, cons_wpp)
            conteos["I6"] += 1

            # I1: subir el precio nunca mejora
            res_caro = resolver_consulta(
                db, u, datos, precio * Decimal("2"), modo,
                cuotas, tiene_int, tna, HOY_FIJO, moneda_q,
            )
            assert ORDEN_RESULTADOS[res_caro["resultado"]] <= ORDEN_RESULTADOS[res["resultado"]]
            conteos["I1"] += 1

            # I2: sin interés, si contado da bien o justo, cuotas nunca da ahorros ni no_alcanza
            res_cont = resolver_consulta(
                db, u, datos, precio, "contado", 1, False, None, HOY_FIJO, moneda_q
            )
            cuotas_sin = cuotas if modo == "cuotas" else rng.randint(2, 12)
            res_cuo_sin = resolver_consulta(
                db, u, datos, precio, "cuotas", cuotas_sin, False, None, HOY_FIJO, moneda_q
            )
            if res_cont["resultado"] in ("bien", "justo"):
                assert res_cuo_sin["resultado"] not in ("ahorros", "no_alcanza")
            conteos["I2"] += 1

            # I3: con interés nunca da mejor que sin interés
            res_cuo_con = resolver_consulta(
                db, u, datos, precio, "cuotas", cuotas_sin, True, Decimal("60"), HOY_FIJO, moneda_q
            )
            assert (
                ORDEN_RESULTADOS[res_cuo_con["resultado"]]
                <= ORDEN_RESULTADOS[res_cuo_sin["resultado"]]
            )
            conteos["I3"] += 1

            # I4: datos viejos nunca da bien
            datos_viejos = replace(datos, estado_fdm="datos_viejos", dias_sin_cargar=40)
            res_v = resolver_consulta(
                db, u, datos_viejos, precio, modo, cuotas, tiene_int, tna, HOY_FIJO, moneda_q
            )
            assert res_v["resultado"] != "bien"
            conteos["I4"] += 1

            # I5: más plata en las cuentas o más ahorros nunca empeora
            datos_mas = replace(
                datos,
                libre=datos.libre + Decimal("500000"),
                finales=tuple(f + Decimal("500000") for f in datos.finales),
                ahorros_total=datos.ahorros_total + Decimal("500000"),
            )
            res_m = resolver_consulta(
                db, u, datos_mas, precio, modo, cuotas, tiene_int, tna, HOY_FIJO, moneda_q
            )
            assert ORDEN_RESULTADOS[res_m["resultado"]] >= ORDEN_RESULTADOS[res["resultado"]]
            conteos["I5"] += 1

    assert total_consultas >= 600
    for inv, cant in conteos.items():
        assert cant >= 600, f"{inv} probó menos de 600 casos: {cant}"
        print(f"Invariante {inv}: {cant} casos probados, 0 fallos")
