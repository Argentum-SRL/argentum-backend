"""
tests/pg/test_idempotencia_pg.py — TD.9 Pruebas de idempotencia en PostgreSQL.
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.clave_idempotencia import ClaveIdempotencia
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import EstadoMeta, Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import MetodoPago, TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario
from app.schemas.transaccion import TransaccionCreate, TransaccionRead
from app.services import idempotencia_service, transaccion_service
from tests.pg.conftest import (
    crear_categoria,
    crear_fixture_a,
    crear_fixture_b,
    crear_tarjeta_para_billetera,
    fijar_hoy,
)


@pytest.fixture
def client_a(db: Session, usuario_test: Usuario):
    """TestClient configurado para usuario_test."""
    prev_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: usuario_test
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    app.dependency_overrides.update(prev_overrides)


def _obtener_detalle(res) -> str | None:
    data = res.json()
    if isinstance(data, dict):
        return data.get("detail") or data.get("error", {}).get("message")
    return None


def test_td9a_td9b_td9c_td9d_td9e_transacciones(
    db: Session, usuario_test: Usuario, client_a: TestClient, monkeypatch
):
    hoy = date.today()
    fijar_hoy(monkeypatch, hoy)

    b_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera Test TD9")
    cat = crear_categoria(db, "Categoria TD9")

    # TD.9a: POST /transacciones con Idempotency-Key "clave-prueba-0001" dos veces
    payload_a = {
        "monto": "5000.00",
        "tipo": "egreso",
        "moneda": "ARS",
        "descripcion": "Egreso prueba",
        "fecha": str(hoy),
        "billetera_id": str(b_a.id),
        "categoria_id": str(cat.id),
        "metodo_pago": "debito",
    }
    headers_a = {"Idempotency-Key": "clave-prueba-0001"}

    res1 = client_a.post("/transacciones", json=payload_a, headers=headers_a)
    assert res1.status_code == 201

    res2 = client_a.post("/transacciones", json=payload_a, headers=headers_a)
    assert res2.status_code == 201
    assert res1.json() == res2.json()

    # 1 transacción en la base; saldo 265.000,00
    txs = db.execute(select(Transaccion).where(Transaccion.billetera_id == b_a.id)).scalars().all()
    assert len(txs) == 1
    db.refresh(b_a)
    assert b_a.saldo_actual == Decimal("265000.00")

    # TD.9b: Misma clave con monto $6.000 -> 422 con texto de decisión 3
    payload_b = {**payload_a, "monto": "6000.00"}
    res_b = client_a.post("/transacciones", json=payload_b, headers=headers_a)
    assert res_b.status_code == 422
    assert (
        _obtener_detalle(res_b)
        == "Esta operación ya se registró con otros datos. Cerrá y volvé a abrir para cargar una nueva."
    )

    # Sigue 1 transacción y 265.000,00
    txs_b = db.execute(select(Transaccion).where(Transaccion.billetera_id == b_a.id)).scalars().all()
    assert len(txs_b) == 1
    db.refresh(b_a)
    assert b_a.saldo_actual == Decimal("265000.00")

    # TD.9c: Otro usuario con la misma clave y su propia billetera -> 201
    usuario_2 = Usuario(
        id=uuid.uuid4(),
        email=f"otro_{uuid.uuid4().hex[:6]}@example.com",
        nombre="Usuario Dos",
        password_hash="fake",
        rol=RolUsuario.USUARIO,
        is_admin=False,
        estado=EstadoUsuario.ACTIVO,
        moneda_principal=Moneda.ARS,
        auth_provider=AuthProvider.EMAIL,
        onboarding_completado=True,
    )
    db.add(usuario_2)
    db.commit()
    b_u2 = crear_fixture_a(db, usuario_2.id, hoy, "Billetera U2")

    app.dependency_overrides[get_current_user] = lambda: usuario_2
    client_u2 = TestClient(app)

    payload_u2 = {
        **payload_a,
        "billetera_id": str(b_u2.id),
    }
    res_u2 = client_u2.post("/transacciones", json=payload_u2, headers=headers_a)
    assert res_u2.status_code == 201

    # Billetera de U2 tiene 1 transacción y saldo 265.000,00; el primero no cambia
    txs_u2 = db.execute(select(Transaccion).where(Transaccion.billetera_id == b_u2.id)).scalars().all()
    assert len(txs_u2) == 1
    db.refresh(b_a)
    assert b_a.saldo_actual == Decimal("265000.00")

    # Restaurar override a usuario_test
    app.dependency_overrides[get_current_user] = lambda: usuario_test

    # TD.9d: Sin encabezado, el mismo POST dos veces -> 2 transacciones (como hoy)
    payload_sin_clave = {
        "monto": "2000.00",
        "tipo": "egreso",
        "moneda": "ARS",
        "descripcion": "Sin clave",
        "fecha": str(hoy),
        "billetera_id": str(b_a.id),
        "categoria_id": str(cat.id),
        "metodo_pago": "debito",
    }
    r_sc1 = client_a.post("/transacciones", json=payload_sin_clave)
    r_sc2 = client_a.post("/transacciones", json=payload_sin_clave)
    assert r_sc1.status_code == 201
    assert r_sc2.status_code == 201
    assert r_sc1.json()["id"] != r_sc2.json()["id"]

    # TD.9e: Encabezados no válidos -> 400 "La clave de la operación no es válida." y 0 txs
    tx_count_before = db.execute(select(Transaccion)).scalars().all()
    r_inv1 = client_a.post("/transacciones", json=payload_a, headers={"Idempotency-Key": "a b"})
    assert r_inv1.status_code == 400
    assert _obtener_detalle(r_inv1) == "La clave de la operación no es válida."

    r_inv2 = client_a.post("/transacciones", json=payload_a, headers={"Idempotency-Key": "1234567"})
    assert r_inv2.status_code == 400
    assert _obtener_detalle(r_inv2) == "La clave de la operación no es válida."

    tx_count_after = db.execute(select(Transaccion)).scalars().all()
    assert len(tx_count_before) == len(tx_count_after)


def test_td9f_transferencia(db: Session, usuario_test: Usuario, client_a: TestClient, monkeypatch):
    hoy = date.today()
    fijar_hoy(monkeypatch, hoy)

    b_a = crear_fixture_a(db, usuario_test.id, hoy, "Origen A")
    b_b = crear_fixture_b(db, usuario_test.id, hoy, "Destino B")

    clave = "clave-prueba-0002"
    headers = {"Idempotency-Key": clave}

    # Primero de $300.000 (saldo insuficiente en A, tiene 270.000 -> 400, 0 transferencias)
    payload_insuficiente = {
        "billetera_origen_id": str(b_a.id),
        "billetera_destino_id": str(b_b.id),
        "monto": "300000.00",
        "moneda": "ARS",
        "fecha": str(hoy),
    }
    r_err = client_a.post("/transferencias", json=payload_insuficiente, headers=headers)
    assert r_err.status_code == 400
    trs = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs) == 0

    # Después de $1.000 con la misma clave: 201, 1 transferencia, A 269.000,00 y B 1.000,00
    payload_ok = {
        "billetera_origen_id": str(b_a.id),
        "billetera_destino_id": str(b_b.id),
        "monto": "1000.00",
        "moneda": "ARS",
        "fecha": str(hoy),
    }
    r_ok = client_a.post("/transferencias", json=payload_ok, headers=headers)
    assert r_ok.status_code == 201
    trs_ok = db.execute(select(TransferenciaInterna)).scalars().all()
    assert len(trs_ok) == 1

    db.refresh(b_a)
    db.refresh(b_b)
    assert b_a.saldo_actual == Decimal("269000.00")
    assert b_b.saldo_actual == Decimal("1000.00")


def _tomar_foto(db: Session, usuario_id: uuid.UUID, billetera: Billetera) -> dict:
    db.refresh(billetera)
    cant_tx = db.execute(
        select(func.count(Transaccion.id)).where(Transaccion.usuario_id == usuario_id)
    ).scalar_one()
    cant_cuotas_pagadas = db.execute(
        select(func.count(Cuota.id))
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(GrupoCuotas.usuario_id == usuario_id, Cuota.pagada.is_(True))
    ).scalar_one()
    cant_movs_meta = db.execute(
        select(func.count(MovimientoMeta.id))
        .join(Meta, MovimientoMeta.meta_id == Meta.id)
        .where(Meta.usuario_id == usuario_id)
    ).scalar_one()
    cant_rendimientos = db.execute(
        select(func.count(RendimientoBilletera.id))
        .join(Billetera, RendimientoBilletera.billetera_id == Billetera.id)
        .where(Billetera.usuario_id == usuario_id)
    ).scalar_one()
    cant_ajustes = db.execute(
        select(func.count(AjusteSaldo.id))
        .join(Billetera, AjusteSaldo.billetera_id == Billetera.id)
        .where(Billetera.usuario_id == usuario_id)
    ).scalar_one()
    cant_claves = db.execute(
        select(func.count(ClaveIdempotencia.id)).where(ClaveIdempotencia.usuario_id == usuario_id)
    ).scalar_one()
    return {
        "saldo_actual": billetera.saldo_actual,
        "transacciones": cant_tx,
        "cuotas_pagadas": cant_cuotas_pagadas,
        "movimientos_meta": cant_movs_meta,
        "rendimientos_billetera": cant_rendimientos,
        "ajustes_saldo": cant_ajustes,
        "claves_idempotencia": cant_claves,
    }


def test_td9g_cinco_rutas_adicionales(
    db: Session, usuario_test: Usuario, client_a: TestClient, monkeypatch
):
    hoy = date.today()
    fijar_hoy(monkeypatch, hoy)

    b_a = crear_fixture_a(db, usuario_test.id, hoy, "Billetera TD9G")
    cat = crear_categoria(db, "Categoria TD9G")
    # Asegurar existencia de categoría Banco requerida por el servicio de pago de tarjeta
    crear_categoria(db, "Banco")

    # 1. Pagar resumen de tarjeta (compra con crédito de $10.000 de la tarjeta de A)
    tarjeta = crear_tarjeta_para_billetera(db, usuario_test.id, b_a.id, "Visa Pagar")
    payload_compra = {
        "monto": "10000.00",
        "tipo": "egreso",
        "moneda": "ARS",
        "descripcion": "Consumo tarjeta 10k",
        "fecha": str(hoy - timedelta(days=2)),
        "billetera_id": str(b_a.id),
        "tarjeta_id": str(tarjeta.id),
        "categoria_id": str(cat.id),
        "metodo_pago": "credito",
    }
    r_compra = client_a.post("/transacciones", json=payload_compra)
    assert r_compra.status_code == 201

    cuota_existente = db.execute(
        select(Cuota)
        .join(GrupoCuotas, Cuota.grupo_id == GrupoCuotas.id)
        .where(GrupoCuotas.tarjeta_id == tarjeta.id)
    ).scalar_one()
    fecha_resumen_venc = cuota_existente.fecha_vencimiento

    payload_pago = {
        "monto": 10000.0,
        "billetera_id": str(b_a.id),
        "fecha_pago": str(hoy),
        "fecha_resumen": str(fecha_resumen_venc),
        "moneda": "ARS",
    }
    headers_pago = {"Idempotency-Key": "clave-pagar-0001"}
    r_pag1 = client_a.post(f"/tarjetas/{tarjeta.id}/pagar", json=payload_pago, headers=headers_pago)
    assert r_pag1.status_code == 200
    foto1_pag = _tomar_foto(db, usuario_test.id, b_a)

    r_pag2 = client_a.post(f"/tarjetas/{tarjeta.id}/pagar", json=payload_pago, headers=headers_pago)
    assert r_pag2.status_code == 200
    assert r_pag1.json() == r_pag2.json()
    foto2_pag = _tomar_foto(db, usuario_test.id, b_a)
    assert foto1_pag == foto2_pag
    # Saldo A era 270.000 - 10.000 = 260.000
    assert b_a.saldo_actual == Decimal("260000.00")

    # 2. Aporte a meta de $3.000
    meta = Meta(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        nombre="Meta Auto",
        monto_objetivo=Decimal("50000.00"),
        monto_actual=Decimal("0.00"),
        moneda=Moneda.ARS,
        estado=EstadoMeta.ACTIVA,
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(meta)
    db.commit()

    payload_meta = {
        "tipo": "aporte",
        "monto": "3000.00",
        "moneda_movimiento": "ARS",
        "billetera_id": str(b_a.id),
        "fecha": str(hoy),
    }
    headers_meta = {"Idempotency-Key": "clave-meta-0001"}
    r_met1 = client_a.post(f"/goals/{meta.id}/movimientos", json=payload_meta, headers=headers_meta)
    assert r_met1.status_code == 200
    foto1_met = _tomar_foto(db, usuario_test.id, b_a)

    r_met2 = client_a.post(f"/goals/{meta.id}/movimientos", json=payload_meta, headers=headers_meta)
    assert r_met2.status_code == 200
    assert r_met1.json() == r_met2.json()
    foto2_met = _tomar_foto(db, usuario_test.id, b_a)
    assert foto1_met == foto2_met

    # 1 movimiento de meta en la base
    movs = db.execute(select(MovimientoMeta).where(MovimientoMeta.meta_id == meta.id)).scalars().all()
    assert len(movs) == 1
    assert b_a.saldo_actual == Decimal("257000.00")

    # 3. Prepago de un grupo de 3 cuotas de $1.000
    payload_compra_cuotas = {
        "monto": "3000.00",
        "tipo": "egreso",
        "moneda": "ARS",
        "descripcion": "Grupo 3 cuotas",
        "fecha": str(hoy),
        "billetera_id": str(b_a.id),
        "tarjeta_id": str(tarjeta.id),
        "categoria_id": str(cat.id),
        "metodo_pago": "credito",
        "info_cuotas": {
            "cantidad_cuotas": 3,
            "cuota_inicial": 1,
            "monto_total": "3000.00",
            "tiene_interes": False,
        },
    }
    r_cc = client_a.post("/transacciones", json=payload_compra_cuotas)
    assert r_cc.status_code == 201
    grupo_id = r_cc.json()["grupo_cuotas_id"]
    assert grupo_id is not None

    payload_prepago = {
        "billetera_id": str(b_a.id),
    }
    headers_prepago = {"Idempotency-Key": "clave-prepagar-0001"}
    r_pre1 = client_a.post(f"/grupos-cuotas/{grupo_id}/prepagar", json=payload_prepago, headers=headers_prepago)
    assert r_pre1.status_code == 200
    foto1_pre = _tomar_foto(db, usuario_test.id, b_a)

    r_pre2 = client_a.post(f"/grupos-cuotas/{grupo_id}/prepagar", json=payload_prepago, headers=headers_prepago)
    assert r_pre2.status_code == 200
    assert r_pre1.json() == r_pre2.json()
    foto2_pre = _tomar_foto(db, usuario_test.id, b_a)
    assert foto1_pre == foto2_pre

    # Cuotas saldadas una sola vez; saldo debitado 3.000
    assert b_a.saldo_actual == Decimal("254000.00")

    # 4. Rendimiento de $500 con fecha hoy
    payload_rend = {
        "monto": "500.00",
        "fecha": str(hoy),
    }
    headers_rend = {"Idempotency-Key": "clave-rend-0001"}
    r_rnd1 = client_a.post(f"/billeteras/{b_a.id}/rendimiento", json=payload_rend, headers=headers_rend)
    assert r_rnd1.status_code == 200
    foto1_rnd = _tomar_foto(db, usuario_test.id, b_a)

    r_rnd2 = client_a.post(f"/billeteras/{b_a.id}/rendimiento", json=payload_rend, headers=headers_rend)
    assert r_rnd2.status_code == 200
    assert r_rnd1.json() == r_rnd2.json()
    foto2_rnd = _tomar_foto(db, usuario_test.id, b_a)
    assert foto1_rnd == foto2_rnd

    rend_rows = db.execute(select(RendimientoBilletera).where(RendimientoBilletera.billetera_id == b_a.id)).scalars().all()
    assert len(rend_rows) == 1
    assert b_a.saldo_actual == Decimal("254500.00")

    # 5. Ajuste de saldo a $250.000
    payload_ajuste = {
        "saldo_declarado": "250000.00",
        "rendimiento": None,
    }
    headers_ajuste = {"Idempotency-Key": "clave-ajuste-0001"}
    r_aj1 = client_a.post(f"/billeteras/{b_a.id}/ajustes", json=payload_ajuste, headers=headers_ajuste)
    assert r_aj1.status_code == 200
    foto1_aj = _tomar_foto(db, usuario_test.id, b_a)

    r_aj2 = client_a.post(f"/billeteras/{b_a.id}/ajustes", json=payload_ajuste, headers=headers_ajuste)
    assert r_aj2.status_code == 200
    assert r_aj1.json() == r_aj2.json()
    foto2_aj = _tomar_foto(db, usuario_test.id, b_a)
    assert foto1_aj == foto2_aj

    ajustes = db.execute(select(AjusteSaldo).where(AjusteSaldo.billetera_id == b_a.id)).scalars().all()
    assert len(ajustes) == 1
    assert b_a.saldo_actual == Decimal("250000.00")


def test_td9h_concurrencia(SessionTesting, usuario_test):
    """
    TD.9h Concurrencia: dos hilos con su propia sesión llaman a ejecutar
    con la misma clave y el mismo cuerpo para crear un egreso de $1.000
    en una billetera sin foto con $10.000: 1 transacción, saldo 9.000,00 y mismo JSON.
    """
    db_setup = SessionTesting()
    b = Billetera(
        id=uuid.uuid4(),
        usuario_id=usuario_test.id,
        nombre="Billetera Concurrente",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("10000.00"),
        saldo_inicial=Decimal("10000.00"),
        fecha_saldo=None,
        tiene_punto_partida=False,
        estado=EstadoBilletera.ACTIVA,
    )
    cat = crear_categoria(db_setup, "Cat Concurrencia")
    db_setup.add(b)
    db_setup.commit()
    b_id = b.id
    cat_id = cat.id
    u_id = usuario_test.id
    db_setup.close()

    clave = "clave-concurrente-0001"
    ruta = "/transacciones"
    cuerpo = {
        "monto": "1000.00",
        "tipo": "egreso",
        "moneda": "ARS",
        "descripcion": "Concurrencia",
        "fecha": str(date.today()),
        "billetera_id": str(b_id),
        "categoria_id": str(cat_id),
        "metodo_pago": "debito",
    }

    class MockURL:
        path = ruta

    class MockRequest:
        url = MockURL()
        headers = {"Idempotency-Key": clave}
        method = "POST"

    resultados = []
    errores = []
    barrera_inicio = threading.Barrier(2)

    def trabajador():
        barrera_inicio.wait(timeout=5)
        db_hilo = SessionTesting()
        try:
            tx_data = TransaccionCreate(
                monto=Decimal("1000.00"),
                tipo=TipoTransaccion.EGRESO,
                moneda=Moneda.ARS,
                descripcion="Concurrencia",
                fecha=date.today(),
                billetera_id=b_id,
                categoria_id=cat_id,
                metodo_pago=MetodoPago.DEBITO,
            )
            req = MockRequest()
            resp = idempotencia_service.ejecutar(
                db=db_hilo,
                request=req,
                usuario_id=u_id,
                cuerpo=cuerpo,
                operar=lambda: transaccion_service.crear_transaccion(db_hilo, u_id, tx_data, commit=False),
                serializar=lambda obj: TransaccionRead.model_validate(obj).model_dump(mode="json"),
                codigo=201,
            )
            resultados.append(resp)
        except Exception as e:
            errores.append(e)
        finally:
            db_hilo.close()

    t1 = threading.Thread(target=trabajador)
    t2 = threading.Thread(target=trabajador)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(errores) == 0, f"Errores en concurrencia: {errores}"
    assert len(resultados) == 2
    r1, r2 = resultados
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert json.loads(r1.body) == json.loads(r2.body)

    db_check = SessionTesting()
    txs = db_check.execute(select(Transaccion).where(Transaccion.billetera_id == b_id)).scalars().all()
    assert len(txs) == 1
    b_final = db_check.get(Billetera, b_id)
    assert b_final.saldo_actual == Decimal("9000.00")
    db_check.close()


def test_td9i_purgar_claves(db: Session, usuario_test: Usuario):
    """
    TD.9i purgar_claves_idempotencia borra una fila de hace 25 horas
    y deja una de hace 23.
    """
    ahora = datetime.now(timezone.utc)
    hace_25 = ahora - timedelta(hours=25)
    hace_23 = ahora - timedelta(hours=23)

    c1_id = uuid.uuid4()
    c2_id = uuid.uuid4()

    c1 = ClaveIdempotencia(
        id=c1_id,
        usuario_id=usuario_test.id,
        clave="clave-vieja-25h",
        ruta="/test",
        hash_cuerpo="0" * 64,
        codigo_http=200,
        respuesta={"status": "old"},
        creada_en=hace_25,
    )
    c2 = ClaveIdempotencia(
        id=c2_id,
        usuario_id=usuario_test.id,
        clave="clave-reciente-23h",
        ruta="/test",
        hash_cuerpo="0" * 64,
        codigo_http=200,
        respuesta={"status": "new"},
        creada_en=hace_23,
    )
    db.add(c1)
    db.add(c2)
    db.commit()

    borradas = idempotencia_service.purgar_claves_idempotencia(db)
    assert borradas == 1

    r1 = db.get(ClaveIdempotencia, c1_id)
    r2 = db.get(ClaveIdempotencia, c2_id)
    assert r1 is None
    assert r2 is not None
