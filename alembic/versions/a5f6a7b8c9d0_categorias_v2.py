# -*- coding: utf-8 -*-
"""categorias_v2

Revision ID: a5f6a7b8c9d0
Revises: a4e5f6a7b8c9
Create Date: 2026-10-26

"""
from __future__ import annotations

from typing import Sequence, Union
from uuid import UUID, uuid4

from alembic import op
import sqlalchemy as sa


revision: str = "a5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "a4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLAS_CON_CATEGORIA = (
    "transacciones",
    "suscripciones",
    "presupuestos_categorias",
    "memoria_comercios",
    "facturas",
    "categorias_excluidas",
)

# Foto inmutable del árbol aprobado de la Decisión 1
# (nombre_cat, tipo, orden, icono, color, subcategorias: [(nombre_sub, orden, es_sistema, ipc)])
FOTO_ARBOL_DECISION_1 = (
    # EGRESO
    (
        "Alimentación", "egreso", 1, "alimentacion", "#F97316",
        (
            ("Supermercado", 0, False, "01"),
            ("Verdulería", 1, False, "01"),
            ("Carnicería", 2, False, "01"),
            ("Panadería", 3, False, "01"),
            ("Kiosco", 4, False, "01"),
        )
    ),
    (
        "Gastronomía", "egreso", 2, "hamburguesa", "#F59E0B",
        (
            ("Restaurantes", 0, False, "11"),
            ("Delivery", 1, False, "11"),
            ("Cafetería", 2, False, "11"),
        )
    ),
    (
        "Transporte", "egreso", 3, "transporte", "#0284C7",
        (
            ("Transporte público", 0, False, "07"),
            ("Taxi y apps", 1, False, "07"),
            ("Combustible", 2, False, "07"),
            ("Estacionamiento", 3, False, "07"),
            ("Peajes", 4, False, "07"),
            ("Auto", 5, False, "07"),
        )
    ),
    (
        "Vivienda", "egreso", 4, "luz", "#EAB308",
        (
            ("Alquiler", 0, False, "04"),
            ("Expensas", 1, False, "04"),
            ("Luz", 2, False, "04"),
            ("Gas", 3, False, "04"),
            ("Agua", 4, False, "04"),
        )
    ),
    (
        "Hogar", "egreso", 5, "casa", "#8B5CF6",
        (
            ("Limpieza", 0, False, "05"),
            ("Muebles y electro", 1, False, "05"),
            ("Reparaciones", 2, False, "05"),
            ("Servicio doméstico", 3, False, "05"),
        )
    ),
    (
        "Salud", "egreso", 6, "medicina", "#10B981",
        (
            ("Prepaga y obra social", 0, False, "06"),
            ("Médico", 1, False, "06"),
            ("Farmacia", 2, False, "06"),
            ("Dentista", 3, False, "06"),
            ("Terapias", 4, False, "06"),
        )
    ),
    (
        "Cuidado personal", "egreso", 7, "spa", "#C026D3",
        (
            ("Peluquería y belleza", 0, False, "12"),
            ("Perfumería", 1, False, "12"),
        )
    ),
    (
        "Ocio", "egreso", 8, "entretenimiento", "#EC4899",
        (
            ("Salidas", 0, False, "09"),
            ("Streaming", 1, False, "09"),
            ("Hobbies y juegos", 2, False, "09"),
            ("Deportes y gimnasio", 3, False, "09"),
            ("Viajes", 4, False, "09"),
        )
    ),
    (
        "Educación", "egreso", 9, "libros", "#DC2626",
        (
            ("Colegio y facultad", 0, False, "10"),
            ("Cursos", 1, False, "10"),
            ("Libros y útiles", 2, False, "10"),
        )
    ),
    (
        "Indumentaria", "egreso", 10, "remera", "#7C3AED",
        (
            ("Ropa", 0, False, "03"),
            ("Calzado", 1, False, "03"),
            ("Accesorios", 2, False, "03"),
        )
    ),
    (
        "Tecnología", "egreso", 11, "equipos", "#6366F1",
        (
            ("Celular", 0, False, "08"),
            ("Internet", 1, False, "08"),
            ("Apps y programas", 2, False, "09"),
            ("Equipos", 3, False, "09"),
        )
    ),
    (
        "Impuestos", "egreso", 12, "impuestos", "#78716C",
        (
            ("ABL e inmobiliario", 0, False, "fuera"),
            ("Patente", 1, False, "fuera"),
            ("Monotributo", 2, False, "fuera"),
        )
    ),
    (
        "Banco", "egreso", 13, "banco", "#64748B",
        (
            ("Comisiones", 0, False, "12"),
            ("Préstamos", 1, False, "fuera"),
            ("Intereses", 2, False, "fuera"),
            ("Seguros", 3, False, "12"),
            ("Tarjeta de crédito", 4, True, "fuera"),
            ("Impuestos", 5, True, "fuera"),
        )
    ),
    (
        "Otros", "egreso", 14, "herramienta", "#6B7280",
        (
            ("Regalos", 0, False, "general"),
            ("Mascotas", 1, False, "09"),
            ("Ayuda familiar", 2, False, "fuera"),
            ("Pagos a personas", 3, False, "general"),
        )
    ),
    (
        "Ahorro", "egreso", 90, "ahorro", "#2563EB",
        ()
    ),
    # INGRESO
    (
        "Trabajo", "ingreso", 1, "salario", "#16A34A",
        (
            ("Sueldo", 0, False, None),
            ("Aguinaldo", 1, False, None),
            ("Bonos y extras", 2, False, None),
            ("Freelance y changas", 3, False, None),
            ("Ventas", 4, False, None),
        )
    ),
    (
        "Jubilación y AUH", "ingreso", 2, "jubilacion", "#65A30D",
        (
            ("Jubilación o pensión", 0, False, None),
            ("AUH y asignaciones", 1, False, None),
        )
    ),
    (
        "Inversiones", "ingreso", 3, "dineroenmano", "#2563EB",
        (
            ("Intereses", 0, False, None),
            ("Alquileres", 1, False, None),
        )
    ),
    (
        "Otros", "ingreso", 4, "dineroenmano", "#9CA3AF",
        (
            ("Regalos", 0, False, None),
            ("Ayuda familiar", 1, False, None),
            ("Cobros de personas", 2, False, None),
        )
    ),
    (
        "Ahorro", "ingreso", 90, "ahorro", "#2563EB",
        ()
    ),
)


def _tablas_disponibles(conn) -> list[str]:
    is_pg = conn.dialect.name == "postgresql"
    disponibles = []
    for t in TABLAS_CON_CATEGORIA:
        if is_pg:
            res = conn.execute(sa.text(
                "SELECT 1 FROM information_schema.tables WHERE table_name = :t AND table_schema = current_schema()"
            ), {"t": t}).scalar()
        else:
            res = conn.execute(sa.text(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = :t"
            ), {"t": t}).scalar()
        if res:
            disponibles.append(t)
    return disponibles


def _guardar_insert(conn, tabla: str, fila_id: str):
    conn.execute(
        sa.text("INSERT INTO respaldo_categorias_a5f6 (tabla, fila_id, accion) VALUES (:t, :f, 'insert')"),
        {"t": tabla, "f": str(fila_id)}
    )


def _guardar_update(conn, tabla: str, fila_id: str, columna: str, valor_anterior: object):
    val_str = str(valor_anterior) if valor_anterior is not None else None
    conn.execute(
        sa.text(
            "INSERT INTO respaldo_categorias_a5f6 (tabla, fila_id, columna, valor_anterior, accion) "
            "VALUES (:t, :f, :c, :v, 'update')"
        ),
        {"t": tabla, "f": str(fila_id), "c": columna, "v": val_str}
    )


def _migrar_datos(conn) -> None:
    # Guarda de idempotencia / CI vacía: si no existe la estructura vieja, no hacer nada
    cuenta_viejas = conn.execute(sa.text("""
        SELECT count(*) FROM categorias 
        WHERE tipo = 'egreso' AND estado = 'activa' 
          AND nombre IN ('Recreativo', 'Comunicación', 'Equipamiento del hogar')
    """)).scalar()
    if not cuenta_viejas:
        return

    is_pg = conn.dialect.name == "postgresql"

    # Crear tabla de respaldo
    if is_pg:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS respaldo_categorias_a5f6 (
                id SERIAL PRIMARY KEY,
                tabla VARCHAR(100) NOT NULL,
                fila_id VARCHAR(100) NOT NULL,
                columna VARCHAR(100),
                valor_anterior TEXT,
                accion VARCHAR(20) NOT NULL
            )
        """))
    else:
        conn.execute(sa.text("""
            CREATE TABLE IF NOT EXISTS respaldo_categorias_a5f6 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tabla VARCHAR(100) NOT NULL,
                fila_id VARCHAR(100) NOT NULL,
                columna VARCHAR(100),
                valor_anterior TEXT,
                accion VARCHAR(20) NOT NULL
            )
        """))

    tablas = _tablas_disponibles(conn)

    # 1. Crea las 3 categorías nuevas
    nuevas_cats = [
        ("Cuidado personal", "egreso", 7, "spa", "#C026D3"),
        ("Impuestos", "egreso", 12, "impuestos", "#78716C"),
        ("Jubilación y AUH", "ingreso", 2, "jubilacion", "#65A30D"),
    ]
    for nom, tipo, orden, icono, color in nuevas_cats:
        row = conn.execute(sa.text(
            "SELECT id FROM categorias WHERE nombre = :nom AND tipo = :tipo"
        ), {"nom": nom, "tipo": tipo}).first()
        if not row:
            new_id = uuid4()
            conn.execute(sa.text("""
                INSERT INTO categorias (id, nombre, tipo, orden, icono, color, estado)
                VALUES (:id, :nom, :tipo, :orden, :icono, :color, 'activa')
            """), {"id": new_id, "nom": nom, "tipo": tipo, "orden": orden, "icono": icono, "color": color})
            _guardar_insert(conn, "categorias", str(new_id))

    # 2. Renombra (decisión 2)
    renombres_cats = [
        ("Recreativo", "Ocio", "egreso"),
        ("Comunicación", "Tecnología", "egreso"),
        ("Equipamiento del hogar", "Hogar", "egreso"),
        ("Empleo", "Trabajo", "ingreso"),
        ("Inversiones y rentas", "Inversiones", "ingreso"),
    ]
    for viejo, nuevo, tipo in renombres_cats:
        row = conn.execute(sa.text(
            "SELECT id, nombre FROM categorias WHERE nombre = :viejo AND tipo = :tipo"
        ), {"viejo": viejo, "tipo": tipo}).first()
        if row:
            _guardar_update(conn, "categorias", str(row[0]), "nombre", row[1])
            conn.execute(sa.text(
                "UPDATE categorias SET nombre = :nuevo WHERE id = :id"
            ), {"nuevo": nuevo, "id": row[0]})

    # 3. Mueve (decisión 3) cambiando la categoría de cada fila de las 6 tablas que usa esa subcategoría
    # (cat_origen, subcat_origen, tipo_cat, cat_destino, subcat_destino_nombre)
    movimientos_subcats = [
        ("Otros", "Cuidado personal", "egreso", "Cuidado personal", "Peluquería y belleza"),
        ("Vivienda", "Impuestos", "egreso", "Impuestos", "ABL e inmobiliario"),
        ("Vivienda", "Seguros", "egreso", "Banco", "Seguros"),
        ("Salud", "Deportes y gimnasio", "egreso", "Ocio", "Deportes y gimnasio"),
        ("Trabajo independiente", "Honorarios", "ingreso", "Trabajo", "Freelance y changas"),
        ("Trabajo independiente", "Venta de productos/servicios", "ingreso", "Trabajo", "Ventas"),
    ]
    for cat_orig, sub_orig, tipo_c, cat_dest, sub_dest in movimientos_subcats:
        cat_orig_row = conn.execute(sa.text(
            "SELECT id FROM categorias WHERE nombre = :nom AND tipo = :tipo"
        ), {"nom": cat_orig, "tipo": tipo_c}).first()
        cat_dest_row = conn.execute(sa.text(
            "SELECT id FROM categorias WHERE nombre = :nom AND tipo = :tipo"
        ), {"nom": cat_dest, "tipo": tipo_c}).first()

        if not cat_orig_row or not cat_dest_row:
            continue

        orig_cid = cat_orig_row[0]
        dest_cid = cat_dest_row[0]

        sub_row = conn.execute(sa.text("""
            SELECT id, nombre, categoria_id FROM subcategorias 
            WHERE categoria_id = :cid AND nombre = :snom
        """), {"cid": orig_cid, "snom": sub_orig}).first()

        if sub_row:
            sub_id = sub_row[0]
            _guardar_update(conn, "subcategorias", str(sub_id), "categoria_id", str(orig_cid))
            if sub_orig != sub_dest:
                _guardar_update(conn, "subcategorias", str(sub_id), "nombre", sub_orig)

            conn.execute(sa.text("""
                UPDATE subcategorias SET categoria_id = :dcid, nombre = :sdest WHERE id = :sid
            """), {"dcid": dest_cid, "sdest": sub_dest, "sid": sub_id})

            # Actualizar filas en las 6 tablas que usan esa subcategoría
            for t in tablas:
                filas = conn.execute(sa.text(f"""
                    SELECT id, categoria_id FROM {t} WHERE subcategoria_id = :sid
                """), {"sid": sub_id}).fetchall()
                for f in filas:
                    _guardar_update(conn, t, str(f[0]), "categoria_id", str(f[1]) if f[1] is not None else None)
                conn.execute(sa.text(f"""
                    UPDATE {t} SET categoria_id = :dcid WHERE subcategoria_id = :sid
                """), {"dcid": dest_cid, "sid": sub_id})

    # 4. Renombra (decisión 4)
    renombres_subcats = [
        ("Transporte", "egreso", "Taxi / Apps", "Taxi y apps"),
        ("Transporte", "egreso", "Mantenimiento y seguro del auto", "Auto"),
        ("Hogar", "egreso", "Muebles y electrodomésticos", "Muebles y electro"),
        ("Salud", "egreso", "Obra social / Prepaga", "Prepaga y obra social"),
        ("Salud", "egreso", "Médico / Consulta", "Médico"),
        ("Salud", "egreso", "Odontología", "Dentista"),
        ("Educación", "egreso", "Cuotas", "Colegio y facultad"),
        ("Educación", "egreso", "Idiomas", "Cursos"),
        ("Educación", "egreso", "Materiales y libros", "Libros y útiles"),
        ("Tecnología", "egreso", "Internet y cable", "Internet"),
        ("Banco", "egreso", "Comisiones y gastos bancarios", "Comisiones"),
        ("Banco", "egreso", "Intereses pagados", "Intereses"),
        ("Trabajo", "ingreso", "Bonos y horas extras", "Bonos y extras"),
        ("Inversiones", "ingreso", "Dividendos e intereses", "Intereses"),
        ("Inversiones", "ingreso", "Alquileres cobrados", "Alquileres"),
    ]
    for c_nom, c_tipo, sub_viejo, sub_nuevo in renombres_subcats:
        c_row = conn.execute(sa.text(
            "SELECT id FROM categorias WHERE nombre = :cnom AND tipo = :tipo"
        ), {"cnom": c_nom, "tipo": c_tipo}).first()
        if c_row:
            s_row = conn.execute(sa.text("""
                SELECT id, nombre FROM subcategorias WHERE categoria_id = :cid AND nombre = :snom
            """), {"cid": c_row[0], "snom": sub_viejo}).first()
            if s_row:
                _guardar_update(conn, "subcategorias", str(s_row[0]), "nombre", s_row[1])
                conn.execute(sa.text("""
                    UPDATE subcategorias SET nombre = :snuevo WHERE id = :sid
                """), {"snuevo": sub_nuevo, "sid": s_row[0]})

    # 5. En las 6 tablas: Estudios y análisis -> Salud > Médico; Impuesto al cheque -> Banco > Comisiones; Trabajo independiente vacía -> Trabajo
    salud_row = conn.execute(sa.text("SELECT id FROM categorias WHERE nombre = 'Salud' AND tipo = 'egreso'")).first()
    if salud_row:
        estudios = conn.execute(sa.text(
            "SELECT id FROM subcategorias WHERE categoria_id = :cid AND nombre = 'Estudios y análisis'"
        ), {"cid": salud_row[0]}).first()
        medico = conn.execute(sa.text(
            "SELECT id FROM subcategorias WHERE categoria_id = :cid AND nombre = 'Médico'"
        ), {"cid": salud_row[0]}).first()
        if estudios and medico:
            for t in tablas:
                filas = conn.execute(sa.text(f"""
                    SELECT id, categoria_id, subcategoria_id FROM {t} WHERE subcategoria_id = :eid
                """), {"eid": estudios[0]}).fetchall()
                for f in filas:
                    _guardar_update(conn, t, str(f[0]), "subcategoria_id", str(f[2]))
                    if f[1] != salud_row[0]:
                        _guardar_update(conn, t, str(f[0]), "categoria_id", str(f[1]) if f[1] is not None else None)
                conn.execute(sa.text(f"""
                    UPDATE {t} SET subcategoria_id = :mid, categoria_id = :cid WHERE subcategoria_id = :eid
                """), {"mid": medico[0], "cid": salud_row[0], "eid": estudios[0]})

    banco_row = conn.execute(sa.text("SELECT id FROM categorias WHERE nombre = 'Banco' AND tipo = 'egreso'")).first()
    if banco_row:
        cheque = conn.execute(sa.text(
            "SELECT id FROM subcategorias WHERE categoria_id = :cid AND nombre = 'Impuesto al cheque / movimientos'"
        ), {"cid": banco_row[0]}).first()
        comisiones = conn.execute(sa.text(
            "SELECT id FROM subcategorias WHERE categoria_id = :cid AND nombre = 'Comisiones'"
        ), {"cid": banco_row[0]}).first()
        if cheque and comisiones:
            for t in tablas:
                filas = conn.execute(sa.text(f"""
                    SELECT id, categoria_id, subcategoria_id FROM {t} WHERE subcategoria_id = :chid
                """), {"chid": cheque[0]}).fetchall()
                for f in filas:
                    _guardar_update(conn, t, str(f[0]), "subcategoria_id", str(f[2]))
                    if f[1] != banco_row[0]:
                        _guardar_update(conn, t, str(f[0]), "categoria_id", str(f[1]) if f[1] is not None else None)
                conn.execute(sa.text(f"""
                    UPDATE {t} SET subcategoria_id = :cmid, categoria_id = :cid WHERE subcategoria_id = :chid
                """), {"cmid": comisiones[0], "cid": banco_row[0], "chid": cheque[0]})

    trabajo_indep_cat = conn.execute(sa.text(
        "SELECT id FROM categorias WHERE nombre = 'Trabajo independiente' AND tipo = 'ingreso'"
    )).first()
    trabajo_cat = conn.execute(sa.text(
        "SELECT id FROM categorias WHERE nombre = 'Trabajo' AND tipo = 'ingreso'"
    )).first()
    if trabajo_indep_cat and trabajo_cat:
        for t in tablas:
            filas = conn.execute(sa.text(f"""
                SELECT id FROM {t} WHERE categoria_id = :tid AND subcategoria_id IS NULL
            """), {"tid": trabajo_indep_cat[0]}).fetchall()
            for f in filas:
                _guardar_update(conn, t, str(f[0]), "categoria_id", str(trabajo_indep_cat[0]))
            conn.execute(sa.text(f"""
                UPDATE {t} SET categoria_id = :wcid WHERE categoria_id = :tid AND subcategoria_id IS NULL
            """), {"wcid": trabajo_cat[0], "tid": trabajo_indep_cat[0]})

    # 6. Archiva (decisión 5)
    subcats_archivar = [
        ("Salud", "Estudios y análisis", "egreso"),
        ("Banco", "Impuesto al cheque / movimientos", "egreso"),
        ("Otros", "Reintegros", "ingreso"),
    ]
    for c_nom, s_nom, c_tipo in subcats_archivar:
        s_row = conn.execute(sa.text("""
            SELECT s.id, s.estado FROM subcategorias s
            JOIN categorias c ON s.categoria_id = c.id
            WHERE c.nombre = :c AND s.nombre = :s AND c.tipo = :t
        """), {"c": c_nom, "s": s_nom, "t": c_tipo}).first()
        if s_row and s_row[1] != "archivada":
            _guardar_update(conn, "subcategorias", str(s_row[0]), "estado", s_row[1])
            conn.execute(sa.text(
                "UPDATE subcategorias SET estado = 'archivada' WHERE id = :id"
            ), {"id": s_row[0]})

    if trabajo_indep_cat:
        row_ti = conn.execute(sa.text(
            "SELECT estado FROM categorias WHERE id = :id"
        ), {"id": trabajo_indep_cat[0]}).first()
        if row_ti and row_ti[0] != "archivada":
            _guardar_update(conn, "categorias", str(trabajo_indep_cat[0]), "estado", row_ti[0])
            conn.execute(sa.text(
                "UPDATE categorias SET estado = 'archivada' WHERE id = :id"
            ), {"id": trabajo_indep_cat[0]})

    # 7. Marca es_sistema en Banco > Tarjeta de crédito y Banco > Impuestos
    for s_nom in ("Tarjeta de crédito", "Impuestos"):
        row = conn.execute(sa.text("""
            SELECT s.id, s.es_sistema FROM subcategorias s
            JOIN categorias c ON s.categoria_id = c.id
            WHERE c.nombre = 'Banco' AND s.nombre = :s AND c.tipo = 'egreso'
        """), {"s": s_nom}).first()
        if row and not row[1]:
            _guardar_update(conn, "subcategorias", str(row[0]), "es_sistema", str(row[1]))
            conn.execute(sa.text(
                "UPDATE subcategorias SET es_sistema = true WHERE id = :id"
            ), {"id": row[0]})

    # 8. Crea las subcategorías nuevas
    subcats_nuevas = [
        ("Alimentación", "egreso", "Panadería", 3, False),
        ("Hogar", "egreso", "Servicio doméstico", 3, False),
        ("Cuidado personal", "egreso", "Perfumería", 1, False),
        ("Ocio", "egreso", "Streaming", 1, False),
        ("Tecnología", "egreso", "Apps y programas", 2, False),
        ("Tecnología", "egreso", "Equipos", 3, False),
        ("Impuestos", "egreso", "Patente", 1, False),
        ("Impuestos", "egreso", "Monotributo", 2, False),
        ("Otros", "egreso", "Ayuda familiar", 2, False),
        ("Otros", "egreso", "Pagos a personas", 3, False),
        ("Jubilación y AUH", "ingreso", "Jubilación o pensión", 0, False),
        ("Jubilación y AUH", "ingreso", "AUH y asignaciones", 1, False),
        ("Otros", "ingreso", "Ayuda familiar", 1, False),
        ("Otros", "ingreso", "Cobros de personas", 2, False),
    ]
    for c_nom, c_tipo, s_nom, ord_s, es_sis in subcats_nuevas:
        c_row = conn.execute(sa.text(
            "SELECT id FROM categorias WHERE nombre = :c AND tipo = :t"
        ), {"c": c_nom, "t": c_tipo}).first()
        if c_row:
            s_exists = conn.execute(sa.text("""
                SELECT id FROM subcategorias WHERE categoria_id = :cid AND nombre = :s
            """), {"cid": c_row[0], "s": s_nom}).first()
            if not s_exists:
                new_sid = uuid4()
                conn.execute(sa.text("""
                    INSERT INTO subcategorias (id, categoria_id, nombre, orden, es_sistema, estado)
                    VALUES (:id, :cid, :nombre, :orden, :es_sistema, 'activa')
                """), {"id": new_sid, "cid": c_row[0], "nombre": s_nom, "orden": ord_s, "es_sistema": es_sis})
                _guardar_insert(conn, "subcategorias", str(new_sid))

    # 9. Fija orden de categorías y subcategorías según la decisión 1
    for cat_data in FOTO_ARBOL_DECISION_1:
        c_nom = cat_data[0]
        c_tipo = cat_data[1]
        c_orden = cat_data[2]
        c_icono = cat_data[3]
        c_color = cat_data[4]
        c_subcats = cat_data[5]

        c_row = conn.execute(sa.text("""
            SELECT id, orden, icono, color FROM categorias WHERE nombre = :nom AND tipo = :tipo
        """), {"nom": c_nom, "tipo": c_tipo}).first()
        if c_row:
            cid, old_ord, old_ico, old_col = c_row
            if old_ord != c_orden:
                _guardar_update(conn, "categorias", str(cid), "orden", str(old_ord))
            if old_ico != c_icono:
                _guardar_update(conn, "categorias", str(cid), "icono", old_ico)
            if old_col != c_color:
                _guardar_update(conn, "categorias", str(cid), "color", old_col)

            conn.execute(sa.text("""
                UPDATE categorias SET orden = :orden, icono = :icono, color = :color WHERE id = :cid
            """), {"orden": c_orden, "icono": c_icono, "color": c_color, "cid": cid})

            for s_nom, s_ord, s_sis, _ in c_subcats:
                s_row = conn.execute(sa.text("""
                    SELECT id, orden, es_sistema FROM subcategorias WHERE categoria_id = :cid AND nombre = :snom
                """), {"cid": cid, "snom": s_nom}).first()
                if s_row:
                    sid, old_sord, old_ssis = s_row
                    if old_sord != s_ord:
                        _guardar_update(conn, "subcategorias", str(sid), "orden", str(old_sord))
                    if old_ssis != s_sis:
                        _guardar_update(conn, "subcategorias", str(sid), "es_sistema", str(old_ssis))
                    conn.execute(sa.text("""
                        UPDATE subcategorias SET orden = :ord, es_sistema = :sis WHERE id = :sid
                    """), {"ord": s_ord, "sis": s_sis, "sid": sid})

    # 10. Recategoriza las suscripciones del catálogo (decisión 9)
    from app.core.catalogo_suscripciones import sugerir_categoria_suscripcion

    subs = conn.execute(sa.text("""
        SELECT s.id, s.nombre, s.categoria_id, c.nombre as cat_nombre
        FROM suscripciones s
        JOIN categorias c ON s.categoria_id = c.id
        WHERE s.subcategoria_id IS NULL
    """)).fetchall()

    for sub_id, sub_nombre, sub_cat_id, cur_cat_nombre in subs:
        cat_sug, sub_sug = sugerir_categoria_suscripcion(sub_nombre)
        if not sub_sug:
            continue
        if cur_cat_nombre in (cat_sug, "Ocio", "Otros"):
            target_cat = conn.execute(sa.text(
                "SELECT id FROM categorias WHERE nombre = :nom AND tipo = 'egreso'"
            ), {"nom": cat_sug}).first()
            if not target_cat:
                continue
            target_sub = conn.execute(sa.text("""
                SELECT id FROM subcategorias 
                WHERE categoria_id = :cat_id AND nombre = :sub AND estado = 'activa'
            """), {"cat_id": target_cat[0], "sub": sub_sug}).first()
            if not target_sub:
                continue

            new_cid = target_cat[0]
            new_sid = target_sub[0]

            _guardar_update(conn, "suscripciones", str(sub_id), "categoria_id", str(sub_cat_id))
            _guardar_update(conn, "suscripciones", str(sub_id), "subcategoria_id", None)
            conn.execute(sa.text("""
                UPDATE suscripciones SET categoria_id = :cid, subcategoria_id = :sid WHERE id = :id
            """), {"cid": new_cid, "sid": new_sid, "id": sub_id})

            # Sus movimientos (suscripcion_id) que tenían la misma categoría y subcategoría que ella pasan igual
            movs = conn.execute(sa.text("""
                SELECT id FROM transacciones 
                WHERE suscripcion_id = :sub_id AND categoria_id = :old_cid AND subcategoria_id IS NULL
            """), {"sub_id": sub_id, "old_cid": sub_cat_id}).fetchall()
            for m in movs:
                _guardar_update(conn, "transacciones", str(m[0]), "categoria_id", str(sub_cat_id))
                _guardar_update(conn, "transacciones", str(m[0]), "subcategoria_id", None)
                conn.execute(sa.text("""
                    UPDATE transacciones SET categoria_id = :cid, subcategoria_id = :sid WHERE id = :id
                """), {"cid": new_cid, "sid": new_sid, "id": m[0]})

    # 11. En las 6 tablas, toda fila con subcategoría pasa a la categoría de su subcategoría
    for t in tablas:
        desalineadas = conn.execute(sa.text(f"""
            SELECT t.id, t.categoria_id, s.categoria_id as target_cat_id
            FROM {t} t
            JOIN subcategorias s ON t.subcategoria_id = s.id
            WHERE t.categoria_id != s.categoria_id
        """)).fetchall()
        for r_id, old_cid, target_cid in desalineadas:
            _guardar_update(conn, t, str(r_id), "categoria_id", str(old_cid) if old_cid is not None else None)
            conn.execute(sa.text(f"""
                UPDATE {t} SET categoria_id = :target_cat_id WHERE id = :id
            """), {"target_cat_id": target_cid, "id": r_id})


def _revertir_datos(conn) -> None:
    is_pg = conn.dialect.name == "postgresql"

    # Si la tabla de respaldo no existe, no hay nada que revertir
    if is_pg:
        respaldo_existe = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'respaldo_categorias_a5f6' AND table_schema = current_schema()"
        )).scalar()
    else:
        respaldo_existe = conn.execute(sa.text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = 'respaldo_categorias_a5f6'"
        )).scalar()

    if not respaldo_existe:
        return

    tablas = _tablas_disponibles(conn)

    # (1) Las filas creadas después de la migración que usan una subcategoría o categoría creada por la migración
    # pasan a la categoría vieja equivalente con subcategoría vacía:
    # Cuidado personal → Otros egreso; Impuestos → Vivienda; Jubilación y AUH → Otros ingreso;
    # una subcategoría nueva dentro de una categoría que ya existía → esa categoría, subcategoría vacía.
    otros_egreso_row = conn.execute(sa.text("SELECT id FROM categorias WHERE nombre = 'Otros' AND tipo = 'egreso'")).first()
    vivienda_row = conn.execute(sa.text("SELECT id FROM categorias WHERE nombre = 'Vivienda' AND tipo = 'egreso'")).first()
    otros_ingreso_row = conn.execute(sa.text("SELECT id FROM categorias WHERE nombre = 'Otros' AND tipo = 'ingreso'")).first()

    otros_egreso_id = otros_egreso_row[0] if otros_egreso_row else None
    vivienda_id = vivienda_row[0] if vivienda_row else None
    otros_ingreso_id = otros_ingreso_row[0] if otros_ingreso_row else None

    # Categorías y subcategorías creadas por la migración
    cats_insertadas = {
        r[0]: r[1]
        for r in conn.execute(sa.text("""
            SELECT c.id, c.nombre 
            FROM categorias c
            JOIN respaldo_categorias_a5f6 r ON CAST(c.id AS VARCHAR) = r.fila_id
            WHERE r.tabla = 'categorias' AND r.accion = 'insert'
        """)).fetchall()
    }
    subcats_insertadas = {
        r[0]: r[1]
        for r in conn.execute(sa.text("""
            SELECT s.id, s.categoria_id 
            FROM subcategorias s
            JOIN respaldo_categorias_a5f6 r ON CAST(s.id AS VARCHAR) = r.fila_id
            WHERE r.tabla = 'subcategorias' AND r.accion = 'insert'
        """)).fetchall()
    }

    for t in tablas:
        # Identificar filas que están en el respaldo para esta tabla (fueron tocadas por la migración)
        filas_en_respaldo = set(r[0] for r in conn.execute(sa.text(
            "SELECT fila_id FROM respaldo_categorias_a5f6 WHERE tabla = :t"
        ), {"t": t}).fetchall())

        todas_filas = conn.execute(sa.text(f"SELECT id, categoria_id, subcategoria_id FROM {t}")).fetchall()
        for f_id, f_cid, f_sid in todas_filas:
            if str(f_id) in filas_en_respaldo:
                continue

            # Esta fila fue creada DESPUÉS de la migración
            # Si usa una categoría creada por la migración:
            if f_cid in cats_insertadas:
                cat_nom = cats_insertadas[f_cid]
                if cat_nom == "Cuidado personal" and otros_egreso_id:
                    conn.execute(sa.text(f"UPDATE {t} SET categoria_id = :cid, subcategoria_id = NULL WHERE id = :id"),
                                 {"cid": otros_egreso_id, "id": f_id})
                elif cat_nom == "Impuestos" and vivienda_id:
                    conn.execute(sa.text(f"UPDATE {t} SET categoria_id = :cid, subcategoria_id = NULL WHERE id = :id"),
                                 {"cid": vivienda_id, "id": f_id})
                elif cat_nom == "Jubilación y AUH" and otros_ingreso_id:
                    conn.execute(sa.text(f"UPDATE {t} SET categoria_id = :cid, subcategoria_id = NULL WHERE id = :id"),
                                 {"cid": otros_ingreso_id, "id": f_id})
            # Si usa una subcategoría creada por la migración pero en categoría existente:
            elif f_sid in subcats_insertadas:
                # Se mantiene su categoría (o se asigna la categoría de la subcategoría si no tenía), subcategoría vacía
                conn.execute(sa.text(f"UPDATE {t} SET subcategoria_id = NULL WHERE id = :id"), {"id": f_id})

    # (2) Restaura cada valor del respaldo en orden inverso
    updates = conn.execute(sa.text("""
        SELECT tabla, fila_id, columna, valor_anterior 
        FROM respaldo_categorias_a5f6 
        WHERE accion = 'update' 
        ORDER BY id DESC
    """)).fetchall()

    for tabla, fila_id, columna, valor_anterior in updates:
        if columna in ("categoria_id", "subcategoria_id"):
            if valor_anterior is None or valor_anterior == "" or valor_anterior == "None":
                conn.execute(sa.text(f"""
                    UPDATE {tabla} SET {columna} = NULL WHERE id = :id
                """), {"id": UUID(fila_id) if is_pg else fila_id})
            else:
                conn.execute(sa.text(f"""
                    UPDATE {tabla} SET {columna} = :val WHERE id = :id
                """), {
                    "val": UUID(valor_anterior) if is_pg else valor_anterior,
                    "id": UUID(fila_id) if is_pg else fila_id
                })
        elif columna == "es_sistema":
            val_bool = valor_anterior in (True, "True", "true", "1", 1)
            conn.execute(sa.text(f"""
                UPDATE {tabla} SET {columna} = :val WHERE id = :id
            """), {
                "val": val_bool,
                "id": UUID(fila_id) if is_pg else fila_id
            })
        elif columna == "orden":
            val_int = int(valor_anterior) if valor_anterior is not None else 0
            conn.execute(sa.text(f"""
                UPDATE {tabla} SET {columna} = :val WHERE id = :id
            """), {
                "val": val_int,
                "id": UUID(fila_id) if is_pg else fila_id
            })
        else:
            conn.execute(sa.text(f"""
                UPDATE {tabla} SET {columna} = :val WHERE id = :id
            """), {
                "val": valor_anterior,
                "id": UUID(fila_id) if is_pg else fila_id
            })

    # (3) Las filas que no están en el respaldo y quedaron con una categoría distinta de la de su subcategoría
    # pasan a la de su subcategoría
    for t in tablas:
        desalineadas = conn.execute(sa.text(f"""
            SELECT t.id, s.categoria_id 
            FROM {t} t
            JOIN subcategorias s ON t.subcategoria_id = s.id
            WHERE t.categoria_id != s.categoria_id
              AND CAST(t.id AS VARCHAR) NOT IN (
                  SELECT fila_id FROM respaldo_categorias_a5f6 WHERE tabla = :t
              )
        """), {"t": t}).fetchall()
        for r_id, target_cid in desalineadas:
            conn.execute(sa.text(f"""
                UPDATE {t} SET categoria_id = :target_cid WHERE id = :id
            """), {"target_cid": target_cid, "id": r_id})

    # (4) Borra las filas creadas (primero subcategorías, después categorías)
    subcats_insert = conn.execute(sa.text("""
        SELECT fila_id FROM respaldo_categorias_a5f6 WHERE tabla = 'subcategorias' AND accion = 'insert'
    """)).fetchall()
    for s in subcats_insert:
        conn.execute(sa.text("DELETE FROM subcategorias WHERE id = :id"), {
            "id": UUID(s[0]) if is_pg else s[0]
        })

    cats_insert = conn.execute(sa.text("""
        SELECT fila_id FROM respaldo_categorias_a5f6 WHERE tabla = 'categorias' AND accion = 'insert'
    """)).fetchall()
    for c in cats_insert:
        conn.execute(sa.text("DELETE FROM categorias WHERE id = :id"), {
            "id": UUID(c[0]) if is_pg else c[0]
        })

    # (5) Borra respaldo_categorias_a5f6
    conn.execute(sa.text("DROP TABLE IF EXISTS respaldo_categorias_a5f6"))


def upgrade() -> None:
    op.add_column("categorias", sa.Column("orden", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("subcategorias", sa.Column("es_sistema", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    conn = op.get_bind()
    _migrar_datos(conn)


def downgrade() -> None:
    conn = op.get_bind()
    _revertir_datos(conn)
    op.drop_column("subcategorias", "es_sistema")
    op.drop_column("categorias", "orden")
