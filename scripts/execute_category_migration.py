"""
scripts/execute_category_migration.py
Migración de datos de categorías y subcategorías de Argentum.
Ejecuta INSERT, UPDATE y DELETE dentro de una transacción atómica.
"""
import os
import sys
from uuid import uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import SessionLocal

NUEVAS_CATEGORIAS_DATA = [
    # EGRESOS (12 categorías, 51 subcategorías)
    {
        "nombre": "Alimentación",
        "tipo": "egreso",
        "icono": "alimentacion",
        "color": "#F97316",
        "subcategorias": ["Supermercado", "Kiosco", "Verdulería", "Carnicería"]
    },
    {
        "nombre": "Indumentaria",
        "tipo": "egreso",
        "icono": "remera",
        "color": "#7C3AED",
        "subcategorias": ["Ropa", "Calzado", "Accesorios"]
    },
    {
        "nombre": "Servicios",
        "tipo": "egreso",
        "icono": "luz",
        "color": "#EAB308",
        "subcategorias": ["Luz", "Gas", "Agua", "Alquiler", "Expensas", "Impuestos", "Seguros"]
    },
    {
        "nombre": "Hogar",
        "tipo": "egreso",
        "icono": "casa",
        "color": "#8B5CF6",
        "subcategorias": ["Limpieza", "Reparaciones", "Muebles y electrodomésticos"]
    },
    {
        "nombre": "Salud",
        "tipo": "egreso",
        "icono": "medicina",
        "color": "#10B981",
        "subcategorias": ["Farmacia", "Médico / Consulta", "Obra social / Prepaga", "Estudios y análisis", "Odontología", "Terapias"]
    },
    {
        "nombre": "Transporte",
        "tipo": "egreso",
        "icono": "transporte",
        "color": "#0284C7",
        "subcategorias": ["Taxi / Apps", "Transporte público", "Combustible", "Peajes", "Estacionamiento", "Mantenimiento y seguro del auto"]
    },
    {
        "nombre": "Comunicación",
        "tipo": "egreso",
        "icono": "internet",
        "color": "#6366F1",
        "subcategorias": ["Celular", "Internet y cable"]
    },
    {
        "nombre": "Recreativo",
        "tipo": "egreso",
        "icono": "entretenimiento",
        "color": "#EC4899",
        "subcategorias": ["Salidas", "Deportes y gimnasio", "Hobbies y juegos", "Viajes"]
    },
    {
        "nombre": "Educación",
        "tipo": "egreso",
        "icono": "libros",
        "color": "#DC2626",
        "subcategorias": ["Cuotas", "Materiales y libros", "Idiomas"]
    },
    {
        "nombre": "Restaurantes y delivery",
        "tipo": "egreso",
        "icono": "hamburguesa",
        "color": "#F59E0B",
        "subcategorias": ["Restaurantes", "Delivery", "Cafetería"]
    },
    {
        "nombre": "Otros",
        "tipo": "egreso",
        "icono": "herramienta",
        "color": "#6B7280",
        "subcategorias": ["Cuidado personal", "Mascotas", "Regalos"]
    },
    {
        "nombre": "Banco",
        "tipo": "egreso",
        "icono": "banco",
        "color": "#64748B",
        "subcategorias": ["Comisiones y gastos bancarios", "Impuesto al cheque / movimientos", "Préstamos", "Intereses pagados"]
    },
    # INGRESOS (4 categorías, 10 subcategorías)
    {
        "nombre": "Empleo",
        "tipo": "ingreso",
        "icono": "salario",
        "color": "#16A34A",
        "subcategorias": ["Sueldo", "Bonos y horas extras", "Aguinaldo"]
    },
    {
        "nombre": "Trabajo independiente",
        "tipo": "ingreso",
        "icono": "trato",
        "color": "#0D9488",
        "subcategorias": ["Honorarios", "Venta de productos/servicios"]
    },
    {
        "nombre": "Inversiones y rentas",
        "tipo": "ingreso",
        "icono": "dineroenmano",
        "color": "#2563EB",
        "subcategorias": ["Dividendos e intereses", "Alquileres cobrados"]
    },
    {
        "nombre": "Otros",
        "tipo": "ingreso",
        "icono": "dineroenmano",
        "color": "#9CA3AF",
        "subcategorias": ["Reintegros", "Regalos"]
    }
]

MAPPING_RULES = {
    # Alimentación
    ("Alimentación", "Bar"): ("Restaurantes y delivery", "Restaurantes", "egreso"),
    ("Alimentación", "Cafetería"): ("Restaurantes y delivery", "Cafetería", "egreso"),
    ("Alimentación", "Carnicería"): ("Alimentación", "Carnicería", "egreso"),
    ("Alimentación", "Delivery"): ("Restaurantes y delivery", "Delivery", "egreso"),
    ("Alimentación", "Dietética"): ("Alimentación", "Otros", "egreso"),
    ("Alimentación", "Heladería"): ("Restaurantes y delivery", "Cafetería", "egreso"),
    ("Alimentación", "Panadería"): ("Alimentación", "Supermercado", "egreso"),
    ("Alimentación", "Pescadería"): ("Alimentación", "Carnicería", "egreso"),
    ("Alimentación", "Pollería"): ("Alimentación", "Carnicería", "egreso"),
    ("Alimentación", "Restaurante"): ("Restaurantes y delivery", "Restaurantes", "egreso"),
    ("Alimentación", "Supermercado"): ("Alimentación", "Supermercado", "egreso"),
    ("Alimentación", "Verdulería"): ("Alimentación", "Verdulería", "egreso"),

    # Banco
    ("Banco", "Ahorros"): ("Ahorro", None, "egreso"),
    ("Banco", "Comisiones bancarias"): ("Banco", "Comisiones y gastos bancarios", "egreso"),
    ("Banco", "Gastos bancarios y comisiones"): ("Banco", "Comisiones y gastos bancarios", "egreso"),
    ("Banco", "Inversiones"): ("Banco", "Comisiones y gastos bancarios", "egreso"),
    ("Banco", "Préstamos"): ("Banco", "Préstamos", "egreso"),
    ("Banco", "Tarjeta de crédito"): ("Banco", "Préstamos", "egreso"),

    # Compras
    ("Compras", "Cuidado Personal y Cosmética"): ("Otros", "Cuidado personal", "egreso"),
    ("Compras", "Hogar y Bazar"): ("Hogar", "Muebles y electrodomésticos", "egreso"),
    ("Compras", "Libros, Juegos y Hobbies"): ("Recreativo", "Hobbies y juegos", "egreso"),
    ("Compras", "Tecnología y Electrónica"): ("Hogar", "Muebles y electrodomésticos", "egreso"),

    # Educación
    ("Educación", "Cuotas escolares / universitarias"): ("Educación", "Cuotas", "egreso"),
    ("Educación", "Cursos y capacitaciones"): ("Educación", "Cuotas", "egreso"),
    ("Educación", "Guardería / Jardín"): ("Educación", "Cuotas", "egreso"),
    ("Educación", "Idiomas"): ("Educación", "Idiomas", "egreso"),
    ("Educación", "Libros y materiales"): ("Educación", "Materiales y libros", "egreso"),

    # Entretenimiento y salidas
    ("Entretenimiento y salidas", "Cine / Teatro / Recitales"): ("Recreativo", "Salidas y entretenimiento", "egreso"),
    ("Entretenimiento y salidas", "Deportes"): ("Recreativo", "Deportes y gimnasio", "egreso"),
    ("Entretenimiento y salidas", "Gimnasio"): ("Recreativo", "Deportes y gimnasio", "egreso"),
    ("Entretenimiento y salidas", "Hobbies"): ("Recreativo", "Hobbies y juegos", "egreso"),
    ("Entretenimiento y salidas", "Juegos y videojuegos"): ("Recreativo", "Hobbies y juegos", "egreso"),
    ("Entretenimiento y salidas", "Salidas con amigos"): ("Recreativo", "Salidas y entretenimiento", "egreso"),
    ("Entretenimiento y salidas", "Streaming"): ("Recreativo", "Suscripciones", "egreso"),
    ("Entretenimiento y salidas", "Vacaciones y viajes"): ("Recreativo", "Viajes", "egreso"),

    # Mascotas
    ("Mascotas", "Alimento"): ("Otros", "Mascotas", "egreso"),
    ("Mascotas", "Mascotas"): ("Otros", "Mascotas", "egreso"),
    ("Mascotas", "Veterinario"): ("Otros", "Mascotas", "egreso"),

    # Otros gastos
    ("Otros gastos", "Gastos bancarios y comisiones"): ("Banco", "Comisiones y gastos bancarios", "egreso"),
    ("Otros gastos", "Impuestos y tasas"): ("Servicios", "Impuestos", "egreso"),
    ("Otros gastos", "Mascotas"): ("Otros", "Mascotas", "egreso"),
    ("Otros gastos", "Otros"): ("Otros", "Otros", "egreso"),
    ("Otros gastos", "Regalos y donaciones"): ("Otros", "Regalos", "egreso"),
    ("Otros gastos", "Seguro de vida / hogar"): ("Servicios", "Seguros", "egreso"),

    # Otros ingresos
    ("Otros ingresos", "Alquiler cobrado"): ("Inversiones y rentas", "Alquileres cobrados", "ingreso"),
    ("Otros ingresos", "Dividendos / inversiones"): ("Inversiones y rentas", "Dividendos e intereses", "ingreso"),
    ("Otros ingresos", "Freelance"): ("Trabajo independiente", "Honorarios", "ingreso"),
    ("Otros ingresos", "Otros"): ("Otros", "Otros", "ingreso"),
    ("Otros ingresos", "Otros ingresos"): ("Otros", "Otros", "ingreso"),
    ("Otros ingresos", "Regalos recibidos"): ("Otros", "Regalos", "ingreso"),
    ("Otros ingresos", "Reintegros"): ("Otros", "Reintegros", "ingreso"),
    ("Otros ingresos", "Sueldo"): ("Empleo", "Sueldo", "ingreso"),
    ("Otros ingresos", "Venta de bienes"): ("Trabajo independiente", "Venta de productos/servicios", "ingreso"),
    ("Otros ingresos", "Ventas"): ("Trabajo independiente", "Venta de productos/servicios", "ingreso"),

    # Regalos
    ("Regalos", "Regalos"): ("Otros", "Regalos", "egreso"),

    # Ropa e indumentaria
    ("Ropa e indumentaria", "Accesorios"): ("Indumentaria", "Accesorios", "egreso"),
    ("Ropa e indumentaria", "Calzado"): ("Indumentaria", "Calzado", "egreso"),
    ("Ropa e indumentaria", "Ropa"): ("Indumentaria", "Ropa", "egreso"),
    ("Ropa e indumentaria", "Ropa deportiva"): ("Indumentaria", "Ropa", "egreso"),
    ("Ropa e indumentaria", "Ropa interior"): ("Indumentaria", "Ropa", "egreso"),

    # Salud y cuidado personal
    ("Salud y cuidado personal", "Dentista"): ("Salud", "Odontología", "egreso"),
    ("Salud y cuidado personal", "Estudios médicos"): ("Salud", "Estudios y análisis", "egreso"),
    ("Salud y cuidado personal", "Farmacia"): ("Salud", "Farmacia", "egreso"),
    ("Salud y cuidado personal", "Gimnasio"): ("Recreativo", "Deportes y gimnasio", "egreso"),
    ("Salud y cuidado personal", "Kinesiología"): ("Salud", "Terapias", "egreso"),
    ("Salud y cuidado personal", "Médico / Consulta"): ("Salud", "Médico / Consulta", "egreso"),
    ("Salud y cuidado personal", "Obra social / Prepaga"): ("Salud", "Obra social / Prepaga", "egreso"),
    ("Salud y cuidado personal", "Óptica"): ("Salud", "Estudios y análisis", "egreso"),
    ("Salud y cuidado personal", "Peluquería"): ("Otros", "Cuidado personal", "egreso"),
    ("Salud y cuidado personal", "Spa"): ("Otros", "Cuidado personal", "egreso"),
    ("Salud y cuidado personal", "Spa / Cuidado personal"): ("Otros", "Cuidado personal", "egreso"),
    ("Salud y cuidado personal", "Terapia"): ("Salud", "Terapias", "egreso"),

    # Servicios digitales
    ("Servicios digitales", "Almacenamiento en la nube"): ("Recreativo", "Suscripciones", "egreso"),
    ("Servicios digitales", "Dispositivos"): ("Hogar", "Muebles y electrodomésticos", "egreso"),
    ("Servicios digitales", "Dominio / Hosting"): ("Recreativo", "Suscripciones", "egreso"),
    ("Servicios digitales", "Música"): ("Recreativo", "Suscripciones", "egreso"),
    ("Servicios digitales", "Reparaciones"): ("Hogar", "Reparaciones", "egreso"),
    ("Servicios digitales", "Software / Apps"): ("Recreativo", "Suscripciones", "egreso"),
    ("Servicios digitales", "Streaming"): ("Recreativo", "Suscripciones", "egreso"),
    ("Servicios digitales", "Suscripciones digitales"): ("Recreativo", "Suscripciones", "egreso"),

    # Trabajo en relación de dependencia
    ("Trabajo en relación de dependencia", "Aguinaldo"): ("Empleo", "Aguinaldo", "ingreso"),
    ("Trabajo en relación de dependencia", "Bonos"): ("Empleo", "Bonos y horas extras", "ingreso"),
    ("Trabajo en relación de dependencia", "Horas extra"): ("Empleo", "Bonos y horas extras", "ingreso"),
    ("Trabajo en relación de dependencia", "Sueldo"): ("Empleo", "Sueldo", "ingreso"),

    # Trabajo independiente
    ("Trabajo independiente", "Consultoría"): ("Trabajo independiente", "Honorarios", "ingreso"),
    ("Trabajo independiente", "Freelance"): ("Trabajo independiente", "Honorarios", "ingreso"),
    ("Trabajo independiente", "Honorarios"): ("Trabajo independiente", "Honorarios", "ingreso"),
    ("Trabajo independiente", "Venta de productos"): ("Trabajo independiente", "Venta de productos/servicios", "ingreso"),

    # Transporte
    ("Transporte", "Bicicleta / Patineta"): ("Transporte", "Transporte público", "egreso"),
    ("Transporte", "Combustible"): ("Transporte", "Combustible", "egreso"),
    ("Transporte", "Estacionamiento"): ("Transporte", "Estacionamiento", "egreso"),
    ("Transporte", "Mantenimiento del auto"): ("Transporte", "Mantenimiento y seguro del auto", "egreso"),
    ("Transporte", "Peaje"): ("Transporte", "Peajes", "egreso"),
    ("Transporte", "Seguro del auto"): ("Transporte", "Mantenimiento y seguro del auto", "egreso"),
    ("Transporte", "Taxi / Remis"): ("Transporte", "Taxi / Apps", "egreso"),
    ("Transporte", "Transporte público"): ("Transporte", "Transporte público", "egreso"),

    # Vivienda
    ("Vivienda", "Accesorios"): ("Hogar", "Muebles y electrodomésticos", "egreso"),
    ("Vivienda", "Agua"): ("Servicios", "Agua", "egreso"),
    ("Vivienda", "Alquiler"): ("Servicios", "Alquiler", "egreso"),
    ("Vivienda", "Cable / TV"): ("Comunicación", "Internet y cable", "egreso"),
    ("Vivienda", "Donaciones"): ("Otros", "Regalos", "egreso"),
    ("Vivienda", "Electricidad"): ("Servicios", "Luz", "egreso"),
    ("Vivienda", "Expensas"): ("Servicios", "Expensas", "egreso"),
    ("Vivienda", "Gas"): ("Servicios", "Gas", "egreso"),
    ("Vivienda", "General"): ("Otros", "Otros", "egreso"),
    ("Vivienda", "Impuestos"): ("Servicios", "Impuestos", "egreso"),
    ("Vivienda", "Intereses"): ("Banco", "Intereses pagados", "egreso"),
    ("Vivienda", "Internet"): ("Comunicación", "Internet y cable", "egreso"),
    ("Vivienda", "Limpieza"): ("Hogar", "Limpieza", "egreso"),
    ("Vivienda", "Mantenimiento"): ("Hogar", "Reparaciones", "egreso"),
    ("Vivienda", "Muebles y decoración"): ("Hogar", "Muebles y electrodomésticos", "egreso"),
    ("Vivienda", "Otros"): ("Otros", "Otros", "egreso"),
    ("Vivienda", "Seguro auto"): ("Transporte", "Mantenimiento y seguro del auto", "egreso"),
    ("Vivienda", "Seguro de vida / hogar"): ("Servicios", "Seguros", "egreso"),
    ("Vivienda", "Seguro hogar"): ("Servicios", "Seguros", "egreso"),
    ("Vivienda", "Seguro vida"): ("Servicios", "Seguros", "egreso"),
    ("Vivienda", "Tarjetas de crédito"): ("Banco", "Préstamos", "egreso"),
    ("Vivienda", "Teléfono"): ("Comunicación", "Celular", "egreso"),
    ("Vivienda", "Teléfono fijo"): ("Comunicación", "Internet y cable", "egreso"),
}

CATEGORY_ONLY_MAPPING = {
    ("Regalos", "egreso"): ("Otros", "Regalos", "egreso"),
    ("Alimentación", "egreso"): ("Alimentación", "Otros", "egreso"),
    ("Transporte", "egreso"): ("Transporte", None, "egreso"),
    ("Entretenimiento y salidas", "egreso"): ("Recreativo", None, "egreso"),
    ("Banco", "egreso"): ("Banco", None, "egreso"),
    ("Servicios digitales", "egreso"): ("Recreativo", "Suscripciones", "egreso"),
    ("Otros gastos", "egreso"): ("Otros", "Otros", "egreso"),
    ("Otros ingresos", "ingreso"): ("Otros", "Otros", "ingreso"),
    ("Vivienda", "egreso"): ("Servicios", None, "egreso"),
    ("Mascotas", "egreso"): ("Otros", "Mascotas", "egreso"),
}


def run_migration():
    session = SessionLocal()
    try:
        session.begin()

        print("=== 1. OBTENIENDO IDs Y ESTRUCTURAS VIEJAS ===")
        old_cats = {r.id: dict(r._mapping) for r in session.execute(text("SELECT id, nombre, tipo FROM categorias")).fetchall()}
        old_subs = {r.id: dict(r._mapping) for r in session.execute(text("SELECT id, nombre, categoria_id FROM subcategorias")).fetchall()}
        print(f"Categorias viejas existentes: {len(old_cats)}")
        print(f"Subcategorias viejas existentes: {len(old_subs)}")

        print("\n=== 2. INSERTANDO NUEVAS CATEGORIAS Y SUBCATEGORIAS ===")
        # Map: (nombre, tipo) -> cat_id
        new_cat_ids = {}
        # Map: (cat_nombre, subcat_nombre, tipo) -> subcat_id
        new_subcat_ids = {}

        for cat_data in NUEVAS_CATEGORIAS_DATA:
            cat_id = uuid4()
            session.execute(
                text("""
                    INSERT INTO categorias (id, nombre, tipo, icono, color, es_global, creador_id, estado)
                    VALUES (:id, :nombre, :tipo, :icono, :color, :es_global, :creador_id, :estado)
                """),
                {
                    "id": cat_id,
                    "nombre": cat_data["nombre"],
                    "tipo": cat_data["tipo"],
                    "icono": cat_data["icono"],
                    "color": cat_data["color"],
                    "es_global": True,
                    "creador_id": None,
                    "estado": "activa"
                }
            )
            new_cat_ids[(cat_data["nombre"], cat_data["tipo"])] = cat_id
            print(f"[+] Categoria creada: '{cat_data['nombre']}' ({cat_data['tipo']}) -> {cat_id}")

            for idx, sub_name in enumerate(cat_data["subcategorias"]):
                sub_id = uuid4()
                session.execute(
                    text("""
                        INSERT INTO subcategorias (id, categoria_id, nombre, orden, es_global, creador_id, estado)
                        VALUES (:id, :categoria_id, :nombre, :orden, :es_global, :creador_id, :estado)
                    """),
                    {
                        "id": sub_id,
                        "categoria_id": cat_id,
                        "nombre": sub_name,
                        "orden": idx,
                        "es_global": True,
                        "creador_id": None,
                        "estado": "activa"
                    }
                )
                new_subcat_ids[(cat_data["nombre"], sub_name, cat_data["tipo"])] = sub_id

        print(f"\nTotal nuevas categorias insertadas: {len(new_cat_ids)}")
        print(f"Total nuevas subcategorias insertadas: {len(new_subcat_ids)}")

        print("\n=== 3. ACTUALIZANDO TRANSACCIONES ===")
        txs = session.execute(text("SELECT id, descripcion, tipo, categoria_id, subcategoria_id FROM transacciones")).fetchall()
        print(f"Total transacciones a procesar: {len(txs)}")

        tx_updated_count = 0
        for t in txs:
            if t.categoria_id is None:
                continue

            c_info = old_cats[t.categoria_id]
            s_info = old_subs.get(t.subcategoria_id) if t.subcategoria_id else None

            cat_name = c_info['nombre']
            sub_name = s_info['nombre'] if s_info else None
            tipo = c_info['tipo']

            target = None
            if sub_name:
                target = MAPPING_RULES.get((cat_name, sub_name))
            else:
                target = CATEGORY_ONLY_MAPPING.get((cat_name, tipo))

            if not target:
                raise ValueError(f"No hay regla de mapeo para transaccion {t.id}: ('{cat_name}', '{sub_name}', '{tipo}')")

            target_cat_name, target_sub_name, target_tipo = target
            target_cat_id = new_cat_ids[(target_cat_name, target_tipo)]
            target_sub_id = new_subcat_ids[(target_cat_name, target_sub_name, target_tipo)] if target_sub_name else None

            session.execute(
                text("""
                    UPDATE transacciones
                    SET categoria_id = :cat_id, subcategoria_id = :sub_id
                    WHERE id = :tx_id
                """),
                {
                    "cat_id": target_cat_id,
                    "sub_id": target_sub_id,
                    "tx_id": t.id
                }
            )
            tx_updated_count += 1

        print(f"[OK] Transacciones actualizadas: {tx_updated_count} / {len(txs)}")

        print("\n=== 4. ACTUALIZANDO PRESUPUESTOS_CATEGORIAS ===")
        pcs = session.execute(text("SELECT id, presupuesto_id, categoria_id, subcategoria_id FROM presupuestos_categorias")).fetchall()
        print(f"Total presupuestos_categorias a procesar: {len(pcs)}")

        pc_updated_count = 0
        for pc in pcs:
            c_info = old_cats.get(pc.categoria_id) if pc.categoria_id else None
            s_info = old_subs.get(pc.subcategoria_id) if pc.subcategoria_id else None
            cat_name = c_info['nombre'] if c_info else (old_cats[s_info['categoria_id']]['nombre'] if s_info else None)
            sub_name = s_info['nombre'] if s_info else None
            tipo = c_info['tipo'] if c_info else "egreso"

            target = None
            if sub_name:
                target = MAPPING_RULES.get((cat_name, sub_name))
            else:
                target = CATEGORY_ONLY_MAPPING.get((cat_name, tipo))

            if not target:
                raise ValueError(f"No hay regla de mapeo para presupuesto {pc.id}: ('{cat_name}', '{sub_name}', '{tipo}')")

            target_cat_name, target_sub_name, target_tipo = target
            target_cat_id = new_cat_ids[(target_cat_name, target_tipo)]
            target_sub_id = new_subcat_ids[(target_cat_name, target_sub_name, target_tipo)] if target_sub_name else None

            session.execute(
                text("""
                    UPDATE presupuestos_categorias
                    SET categoria_id = :cat_id, subcategoria_id = :sub_id
                    WHERE id = :pc_id
                """),
                {
                    "cat_id": target_cat_id,
                    "sub_id": target_sub_id,
                    "pc_id": pc.id
                }
            )
            pc_updated_count += 1

        print(f"[OK] Presupuestos actualizados: {pc_updated_count} / {len(pcs)}")

        print("\n=== 5. ACTUALIZANDO SUSCRIPCIONES ===")
        subs = session.execute(text("SELECT id, nombre, categoria_id FROM suscripciones")).fetchall()
        print(f"Total suscripciones a procesar: {len(subs)}")

        sub_updated_count = 0
        recreativo_id = new_cat_ids[("Recreativo", "egreso")]
        for s in subs:
            session.execute(
                text("UPDATE suscripciones SET categoria_id = :cat_id WHERE id = :sub_id"),
                {"cat_id": recreativo_id, "sub_id": s.id}
            )
            sub_updated_count += 1
            print(f"[OK] Suscripcion '{s.nombre}' actualizada a Recreativo ({recreativo_id})")

        print("\n=== 6. ELIMINANDO SUBCATEGORIAS VIEJAS ===")
        old_sub_ids_list = list(old_subs.keys())
        if old_sub_ids_list:
            del_sub_res = session.execute(
                text("DELETE FROM subcategorias WHERE id = ANY(:sub_ids)"),
                {"sub_ids": old_sub_ids_list}
            )
            print(f"[OK] Subcategorias viejas eliminadas: {del_sub_res.rowcount}")

        print("\n=== 7. ELIMINANDO CATEGORIAS VIEJAS ===")
        old_cat_ids_list = list(old_cats.keys())
        if old_cat_ids_list:
            del_cat_res = session.execute(
                text("DELETE FROM categorias WHERE id = ANY(:cat_ids)"),
                {"cat_ids": old_cat_ids_list}
            )
            print(f"[OK] Categorias viejas eliminadas: {del_cat_res.rowcount}")

        print("\n=== 8. VALIDACION FINAL DE CONTEOS ===")
        final_cat_count = session.execute(text("SELECT count(*) FROM categorias")).scalar()
        final_sub_count = session.execute(text("SELECT count(*) FROM subcategorias")).scalar()
        print(f"Total categorias en DB: {final_cat_count} (Esperado: 16)")
        print(f"Total subcategorias en DB: {final_sub_count} (Esperado: 61)")

        if final_cat_count != 16 or final_sub_count != 61:
            raise ValueError(f"Conteo final no coincide: {final_cat_count} categorias, {final_sub_count} subcategorias")

        # Commit atomico
        session.commit()
        print("\n==========================================")
        print("MIGRACION COMPLETADA Y COMMITEADA CON EXITO")
        print("==========================================")

    except Exception as e:
        session.rollback()
        print(f"\n[!] ERROR EN LA MIGRACION - ROLLBACK EJECUTADO: {e}")
        raise e
    finally:
        session.close()

if __name__ == "__main__":
    run_migration()
