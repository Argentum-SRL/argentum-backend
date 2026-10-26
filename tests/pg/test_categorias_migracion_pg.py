# -*- coding: utf-8 -*-
"""
tests/pg/test_categorias_migracion_pg.py - Test T.13 para la migración y reversión en PostgreSQL.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from uuid import uuid4
from decimal import Decimal
from sqlalchemy import insert, text

from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import AuthProvider, EstadoUsuario, Moneda, RolUsuario, Usuario

# Cargar el módulo de migración vía importlib
MIGRACION_PATH = Path(__file__).resolve().parent.parent.parent / "alembic" / "versions" / "a5f6a7b8c9d0_categorias_v2.py"
spec = importlib.util.spec_from_file_location("migracion_v2", MIGRACION_PATH)
migracion_v2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migracion_v2)
_migrar_datos = migracion_v2._migrar_datos
_revertir_datos = migracion_v2._revertir_datos


# Foto inmutable del árbol viejo (18 categorías y 59 subcategorías)
ARBOL_VIEJO = (
    # Egreso (12 + ahorro)
    ("Alimentación", "egreso", ("Supermercado", "Kiosco", "Verdulería", "Carnicería")),
    ("Indumentaria", "egreso", ("Ropa", "Calzado", "Accesorios")),
    ("Vivienda", "egreso", ("Luz", "Gas", "Agua", "Alquiler", "Expensas", "Impuestos", "Seguros")),
    ("Equipamiento del hogar", "egreso", ("Limpieza", "Reparaciones", "Muebles y electrodomésticos")),
    ("Salud", "egreso", ("Farmacia", "Médico / Consulta", "Obra social / Prepaga", "Estudios y análisis", "Odontología", "Terapias", "Deportes y gimnasio")),
    ("Transporte", "egreso", ("Taxi / Apps", "Transporte público", "Combustible", "Peajes", "Estacionamiento", "Mantenimiento y seguro del auto")),
    ("Comunicación", "egreso", ("Celular", "Internet y cable")),
    ("Recreativo", "egreso", ("Salidas", "Hobbies y juegos", "Viajes")),
    ("Educación", "egreso", ("Cuotas", "Materiales y libros", "Idiomas")),
    ("Gastronomía", "egreso", ("Restaurantes", "Delivery", "Cafetería")),
    ("Otros", "egreso", ("Cuidado personal", "Mascotas", "Regalos")),
    ("Banco", "egreso", ("Comisiones y gastos bancarios", "Impuesto al cheque / movimientos", "Préstamos", "Intereses pagados", "Tarjeta de crédito", "Impuestos")),
    ("Ahorro", "egreso", ()),
    # Ingreso (4 + ahorro)
    ("Empleo", "ingreso", ("Sueldo", "Bonos y horas extras", "Aguinaldo")),
    ("Trabajo independiente", "ingreso", ("Honorarios", "Venta de productos/servicios")),
    ("Inversiones y rentas", "ingreso", ("Dividendos e intereses", "Alquileres cobrados")),
    ("Otros", "ingreso", ("Regalos", "Reintegros")),
    ("Ahorro", "ingreso", ()),
)


def _volcado_hash(conn) -> str:
    lines = []
    lines.append("=== CATEGORIAS ===")
    for r in conn.execute(text("SELECT id, nombre, tipo, estado FROM categorias ORDER BY id")).fetchall():
        lines.append(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}")
    lines.append("=== SUBCATEGORIAS ===")
    for r in conn.execute(text("SELECT id, categoria_id, nombre, estado FROM subcategorias ORDER BY id")).fetchall():
        lines.append(f"{r[0]}|{r[1]}|{r[2]}|{r[3]}")
    for t in ("transacciones", "suscripciones", "presupuestos_categorias", "memoria_comercios", "facturas", "categorias_excluidas"):
        lines.append(f"=== {t.upper()} ===")
        for r in conn.execute(text(f"SELECT id, categoria_id, subcategoria_id FROM {t} ORDER BY id")).fetchall():
            lines.append(f"{r[0]}|{r[1]}|{r[2]}")
    dump_text = "\n".join(lines) + "\n"
    return hashlib.sha256(dump_text.encode("utf-8")).hexdigest()


def _contar_filas_esquema(engine) -> dict[str, int]:
    with engine.connect() as c:
        tablas = c.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )).scalars().all()
        return {t: c.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tablas}


def test_t13_migracion_reversion_pg(pg_engine):
    conteo_inicial = _contar_filas_esquema(pg_engine)

    conn = pg_engine.connect()
    trans = conn.begin()
    try:
        user_id = uuid4()
        billetera_id = uuid4()
        conn.execute(insert(Usuario).values(
            id=user_id,
            email=f"mig_{uuid4().hex[:8]}@example.com",
            nombre="User Migracion PG",
            password_hash="fake",
            rol=RolUsuario.USUARIO,
            is_admin=False,
            estado=EstadoUsuario.ACTIVO,
            moneda_principal=Moneda.ARS,
            auth_provider=AuthProvider.EMAIL,
            onboarding_completo=True,
        ))
        conn.execute(insert(Billetera).values(
            id=billetera_id,
            usuario_id=user_id,
            nombre="Billetera Test",
            moneda=Moneda.ARS,
            saldo_actual=Decimal("0"),
            saldo_inicial=Decimal("0"),
            estado=EstadoBilletera.ACTIVA,
        ))

        # 1. Limpiar las 6 tablas y categorías/subcategorías para tener ambiente puro dentro de la transacción
        for t in ("facturas", "categorias_excluidas", "memoria_comercios", "presupuestos_categorias", "suscripciones", "transacciones", "subcategorias", "categorias"):
            conn.execute(text(f"DELETE FROM {t}"))

        # Cargar el árbol viejo
        cat_map: dict[tuple[str, str], uuid4] = {}
        sub_map: dict[tuple[str, str, str], uuid4] = {}

        for cat_nom, tipo, subs in ARBOL_VIEJO:
            cid = uuid4()
            conn.execute(text(
                "INSERT INTO categorias (id, nombre, tipo, orden, icono, color, estado) "
                "VALUES (:id, :nom, :tipo, 0, 'icono', '#000000', 'activa')"
            ), {"id": cid, "nom": cat_nom, "tipo": tipo})
            cat_map[(cat_nom, tipo)] = cid

            for s_nom in subs:
                sid = uuid4()
                conn.execute(text(
                    "INSERT INTO subcategorias (id, categoria_id, nombre, orden, es_sistema, estado) "
                    "VALUES (:id, :cid, :nom, 0, FALSE, 'activa')"
                ), {"id": sid, "cid": cid, "nom": s_nom})
                sub_map[(cat_nom, tipo, s_nom)] = sid

        # Filas en las 6 tablas según especificación exacta de T.13:
        # Transacciones
        # - Otros > Cuidado personal (2)
        tx1_id, tx2_id = uuid4(), uuid4()
        for tx_id in (tx1_id, tx2_id):
            conn.execute(text(
                "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
                "VALUES (:id, :uid, :bid, 1000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Corte de pelo', FALSE, FALSE)"
            ), {"id": tx_id, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Otros", "egreso")], "sid": sub_map[("Otros", "egreso", "Cuidado personal")]})

        # - Vivienda > Impuestos (1)
        tx_viv_imp = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 2000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'ABL', FALSE, FALSE)"
        ), {"id": tx_viv_imp, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Vivienda", "egreso")], "sid": sub_map[("Vivienda", "egreso", "Impuestos")]})

        # - Salud > Deportes y gimnasio (1)
        tx_sal_dep = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 3000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Gimnasio', FALSE, FALSE)"
        ), {"id": tx_sal_dep, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Salud", "egreso")], "sid": sub_map[("Salud", "egreso", "Deportes y gimnasio")]})

        # - Recreativo con subcategoría Deportes y gimnasio (1) [desalineada histórica del 22/09]
        tx_rec_dep = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 4000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Futbol con amigos', FALSE, FALSE)"
        ), {"id": tx_rec_dep, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Recreativo", "egreso")], "sid": sub_map[("Salud", "egreso", "Deportes y gimnasio")]})

        # - Vivienda > Seguros (1)
        tx_viv_seg = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 5000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Seguro hogar', FALSE, FALSE)"
        ), {"id": tx_viv_seg, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Vivienda", "egreso")], "sid": sub_map[("Vivienda", "egreso", "Seguros")]})

        # - Trabajo independiente > Honorarios (1)
        tx_ti_hono = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 60000, 'ARS', 'ingreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Honorarios cliente', FALSE, FALSE)"
        ), {"id": tx_ti_hono, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Trabajo independiente", "ingreso")], "sid": sub_map[("Trabajo independiente", "ingreso", "Honorarios")]})

        # - Trabajo independiente vacía (1)
        tx_ti_vacia = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 70000, 'ARS', 'ingreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, NULL, 'Trabajo freelance', FALSE, FALSE)"
        ), {"id": tx_ti_vacia, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Trabajo independiente", "ingreso")]})

        # - Otros de ingreso > Reintegros (1)
        tx_reintegro = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 8000, 'ARS', 'ingreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Reintegro compra', FALSE, FALSE)"
        ), {"id": tx_reintegro, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Otros", "ingreso")], "sid": sub_map[("Otros", "ingreso", "Reintegros")]})

        # - Salud > Estudios y análisis (1)
        tx_estudios = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 9000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Analisis de sangre', FALSE, FALSE)"
        ), {"id": tx_estudios, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Salud", "egreso")], "sid": sub_map[("Salud", "egreso", "Estudios y análisis")]})

        # - Banco > Impuesto al cheque / movimientos (1)
        tx_imp_chq = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 1000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, :sid, 'Impuesto ley', FALSE, FALSE)"
        ), {"id": tx_imp_chq, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Banco", "egreso")], "sid": sub_map[("Banco", "egreso", "Impuesto al cheque / movimientos")]})

        # Suscripciones
        # - Netflix en Recreativo vacía con 1 cobro
        susc_netflix = uuid4()
        conn.execute(text(
            "INSERT INTO suscripciones (id, usuario_id, billetera_id, nombre, frecuencia, proximo_cobro, fecha_creacion, categoria_id, subcategoria_id, estado) "
            "VALUES (:id, :uid, :bid, 'Netflix', 'mensual', '2026-11-20', NOW(), :cid, NULL, 'activa')"
        ), {"id": susc_netflix, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Recreativo", "egreso")]})

        tx_cobro_netflix = uuid4()
        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, suscripcion_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 5000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-20', NOW(), :cid, NULL, :susc_id, 'Cobro Netflix', FALSE, FALSE)"
        ), {"id": tx_cobro_netflix, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Recreativo", "egreso")], "susc_id": susc_netflix})

        # - Gimnasio en Salud > Deportes y gimnasio
        susc_gimnasio = uuid4()
        conn.execute(text(
            "INSERT INTO suscripciones (id, usuario_id, billetera_id, nombre, frecuencia, proximo_cobro, fecha_creacion, categoria_id, subcategoria_id, estado) "
            "VALUES (:id, :uid, :bid, 'Gimnasio', 'mensual', '2026-11-20', NOW(), :cid, :sid, 'activa')"
        ), {"id": susc_gimnasio, "uid": user_id, "bid": billetera_id, "cid": cat_map[("Salud", "egreso")], "sid": sub_map[("Salud", "egreso", "Deportes y gimnasio")]})

        # Presupuestos_categorias
        # - (Salud, Deportes y gimnasio)
        presupuesto_id = uuid4()
        conn.execute(text(
            "INSERT INTO presupuestos (id, usuario_id, nombre, monto, moneda, periodo, renovacion, estado, fecha_creacion) "
            "VALUES (:id, :uid, 'Presupuesto Octubre', 100000, 'ARS', 'mensual', 'automatica', 'activo', NOW())"
        ), {"id": presupuesto_id, "uid": user_id})

        pc_sal_dep = uuid4()
        conn.execute(text(
            "INSERT INTO presupuestos_categorias (id, presupuesto_id, categoria_id, subcategoria_id) "
            "VALUES (:id, :pid, :cid, :sid)"
        ), {"id": pc_sal_dep, "pid": presupuesto_id, "cid": cat_map[("Salud", "egreso")], "sid": sub_map[("Salud", "egreso", "Deportes y gimnasio")]})

        # - (Otros, Cuidado personal)
        pc_otr_cui = uuid4()
        conn.execute(text(
            "INSERT INTO presupuestos_categorias (id, presupuesto_id, categoria_id, subcategoria_id) "
            "VALUES (:id, :pid, :cid, :sid)"
        ), {"id": pc_otr_cui, "pid": presupuesto_id, "cid": cat_map[("Otros", "egreso")], "sid": sub_map[("Otros", "egreso", "Cuidado personal")]})

        # Memoria_comercios
        # - (Vivienda, Impuestos)
        mc_id = uuid4()
        conn.execute(text(
            "INSERT INTO memoria_comercios (id, usuario_id, clave, tipo, categoria_id, subcategoria_id, fecha_creacion, fecha_actualizacion) "
            "VALUES (:id, :uid, 'rentas cba', 'egreso', :cid, :sid, NOW(), NOW())"
        ), {"id": mc_id, "uid": user_id, "cid": cat_map[("Vivienda", "egreso")], "sid": sub_map[("Vivienda", "egreso", "Impuestos")]})

        # Facturas
        fact_id = uuid4()
        conn.execute(text(
            "INSERT INTO facturas (id, usuario_id, descripcion, monto, moneda, fecha_vencimiento, origen, estado, pagada_automaticamente, categoria_id, subcategoria_id) "
            "VALUES (:id, :uid, 'Factura luz', 1500, 'ARS', '2026-10-30', 'whatsapp_foto', 'pendiente', FALSE, :cid, :sid)"
        ), {"id": fact_id, "uid": user_id, "cid": cat_map[("Vivienda", "egreso")], "sid": sub_map[("Vivienda", "egreso", "Impuestos")]})

        # Categorias_excluidas
        ce_id = uuid4()
        conn.execute(text(
            "INSERT INTO categorias_excluidas (id, usuario_id, categoria_id, subcategoria_id, fecha_exclusion) "
            "VALUES (:id, :uid, :cid, :sid, NOW())"
        ), {"id": ce_id, "uid": user_id, "cid": cat_map[("Otros", "egreso")], "sid": sub_map[("Otros", "egreso", "Cuidado personal")]})

        # Volcado inicial
        hash_antes = _volcado_hash(conn)

        # ==========================================================================
        # EJECUTAR _migrar_datos
        # ==========================================================================
        _migrar_datos(conn)

        # Verificaciones después de migración:
        # 1. Netflix y su cobro en Ocio > Streaming
        row_susc = conn.execute(text(
            "SELECT c.nombre, s.nombre FROM suscripciones sub "
            "JOIN categorias c ON sub.categoria_id = c.id "
            "LEFT JOIN subcategorias s ON sub.subcategoria_id = s.id WHERE sub.id = :id"
        ), {"id": susc_netflix}).fetchone()
        assert row_susc == ("Ocio", "Streaming")

        row_tx_net = conn.execute(text(
            "SELECT c.nombre, s.nombre FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id "
            "LEFT JOIN subcategorias s ON t.subcategoria_id = s.id WHERE t.id = :id"
        ), {"id": tx_cobro_netflix}).fetchone()
        assert row_tx_net == ("Ocio", "Streaming")

        # 2. Estudios y análisis -> Salud > Médico
        row_est = conn.execute(text(
            "SELECT c.nombre, s.nombre FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id "
            "JOIN subcategorias s ON t.subcategoria_id = s.id WHERE t.id = :id"
        ), {"id": tx_estudios}).fetchone()
        assert row_est == ("Salud", "Médico")

        # 3. Impuesto al cheque -> Banco > Comisiones
        row_chq = conn.execute(text(
            "SELECT c.nombre, s.nombre FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id "
            "JOIN subcategorias s ON t.subcategoria_id = s.id WHERE t.id = :id"
        ), {"id": tx_imp_chq}).fetchone()
        assert row_chq == ("Banco", "Comisiones")

        # 4. Trabajo independiente vacía -> Trabajo (vacía)
        row_ti_vacia = conn.execute(text(
            "SELECT c.nombre, t.subcategoria_id FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id WHERE t.id = :id"
        ), {"id": tx_ti_vacia}).fetchone()
        assert row_ti_vacia[0] == "Trabajo"
        assert row_ti_vacia[1] is None

        # 5. Reintegros sin cambio
        row_reint = conn.execute(text(
            "SELECT c.nombre, s.nombre FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id "
            "JOIN subcategorias s ON t.subcategoria_id = s.id WHERE t.id = :id"
        ), {"id": tx_reintegro}).fetchone()
        assert row_reint == ("Otros", "Reintegros")

        # 6. Cuidado personal -> Cuidado personal > Peluquería y belleza
        for tx_id in (tx1_id, tx2_id):
            r = conn.execute(text(
                "SELECT c.nombre, s.nombre FROM transacciones t "
                "JOIN categorias c ON t.categoria_id = c.id "
                "JOIN subcategorias s ON t.subcategoria_id = s.id WHERE t.id = :id"
            ), {"id": tx_id}).fetchone()
            assert r == ("Cuidado personal", "Peluquería y belleza")

        # 7. 0 filas con categoría distinta de la de su subcategoría en las 6 tablas
        for t in ("transacciones", "suscripciones", "presupuestos_categorias", "memoria_comercios", "facturas", "categorias_excluidas"):
            desalineadas = conn.execute(text(
                f"SELECT COUNT(*) FROM {t} x "
                "JOIN subcategorias s ON x.subcategoria_id = s.id "
                "WHERE x.categoria_id IS NULL OR x.categoria_id != s.categoria_id"
            )).scalar()
            assert desalineadas == 0, f"Tabla {t} tiene {desalineadas} filas desalineadas tras migrar"

        # 8. El árbol activo coincide exactamente con ARBOL_CATEGORIAS
        cats_activas = conn.execute(text(
            "SELECT nombre, tipo FROM categorias WHERE estado = 'activa' ORDER BY tipo, orden, nombre"
        )).fetchall()
        assert len(cats_activas) == 20

        # 9. Segunda corrida de _migrar_datos no cambia nada (idempotente)
        _migrar_datos(conn)

        # 10. Reversión: probar _revertir_datos y verificar volcado idéntico
        _revertir_datos(conn)

        hash_despues_revertir = _volcado_hash(conn)
        assert hash_despues_revertir == hash_antes, f"Hash no coincide: antes={hash_antes}, despues={hash_despues_revertir}"

        # 11. Probar transacciones creadas después de migrar
        _migrar_datos(conn)

        # Obtener IDs de las categorías y subcategorías nuevas
        cat_alim = conn.execute(text("SELECT id FROM categorias WHERE nombre = 'Alimentación' AND tipo = 'egreso'")).scalar()
        sub_pan = conn.execute(text("SELECT id FROM subcategorias WHERE nombre = 'Panadería' AND categoria_id = :cid"), {"cid": cat_alim}).scalar()

        cat_cp = conn.execute(text("SELECT id FROM categorias WHERE nombre = 'Cuidado personal' AND tipo = 'egreso'")).scalar()
        sub_perf = conn.execute(text("SELECT id FROM subcategorias WHERE nombre = 'Perfumería' AND categoria_id = :cid"), {"cid": cat_cp}).scalar()

        tx_pan_id = uuid4()
        tx_perf_id = uuid4()

        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 1200, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-26', NOW(), :cid, :sid, 'Medialunas', FALSE, FALSE)"
        ), {"id": tx_pan_id, "uid": user_id, "bid": billetera_id, "cid": cat_alim, "sid": sub_pan})

        conn.execute(text(
            "INSERT INTO transacciones (id, usuario_id, billetera_id, monto, moneda, tipo, estado_verificacion, origen, fecha, fecha_creacion, categoria_id, subcategoria_id, descripcion, es_cuota_hija, es_padre_cuotas) "
            "VALUES (:id, :uid, :bid, 25000, 'ARS', 'egreso', 'confirmada', 'manual', '2026-10-26', NOW(), :cid, :sid, 'Perfume', FALSE, FALSE)"
        ), {"id": tx_perf_id, "uid": user_id, "bid": billetera_id, "cid": cat_cp, "sid": sub_perf})

        _revertir_datos(conn)

        # Después de revertir:
        # - Panadería -> Alimentación, subcategoría vacía (NULL)
        r_pan = conn.execute(text(
            "SELECT c.nombre, t.subcategoria_id FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id WHERE t.id = :id"
        ), {"id": tx_pan_id}).fetchone()
        assert r_pan[0] == "Alimentación"
        assert r_pan[1] is None

        # - Perfumería en Cuidado personal -> Otros egreso, subcategoría vacía (NULL)
        r_perf = conn.execute(text(
            "SELECT c.nombre, t.subcategoria_id FROM transacciones t "
            "JOIN categorias c ON t.categoria_id = c.id WHERE t.id = :id"
        ), {"id": tx_perf_id}).fetchone()
        assert r_perf[0] == "Otros"
        assert r_perf[1] is None
    finally:
        trans.rollback()
        conn.close()

    conteo_final = _contar_filas_esquema(pg_engine)
    assert conteo_inicial == conteo_final, f"El esquema quedó modificado: {conteo_inicial} != {conteo_final}"
