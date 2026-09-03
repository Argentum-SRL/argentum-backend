import sys
import os

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from sqlalchemy.orm import Session

# 16 CATEGORÍAS CANÓNICAS OFICIALES DE ARGENTUM (12 Egresos + 4 Ingresos)
CATEGORIAS_SEED = [
    # ── EGRESOS (12) ──────────────────────────────────────
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
        "subcategorias": [
            "Comisiones y gastos bancarios",
            "Impuesto al cheque / movimientos",
            "Préstamos",
            "Intereses pagados",
            "Tarjeta de crédito",
            "Impuestos"
        ]
    },
    # ── INGRESOS (4) ─────────────────────────────────────
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
    },
]


# ==============================================================================
# ADVERTENCIA PREVENTIVA:
# Este seed busca categorías y subcategorías por NOMBRE, no por un identificador
# inmutable (slug o UUID). Es puramente aditivo: si no encuentra el nombre exacto,
# lo INSERTA como nuevo y NUNCA borra ni reconcilia registros que sobren.
#
# Si en el futuro renombrás una categoría o subcategoría en la lista CATEGORIAS_SEED
# de este archivo, tenés que ejecutar también un UPDATE manual/migración en la base
# de datos para la fila existente. Si solo cambiás el string acá y reiniciás el
# backend, el seeder insertará la nueva entidad y dejará la vieja huérfana y
# duplicada en la base de datos (consecuencia sufrida en agosto 2026).
# ==============================================================================

def seed_categorias(db: Session):
    """
    Seed idempotente de las 16 categorías canónicas y sus 59 subcategorías oficiales.
    - Idempotencia estricta en categorías: busca por (nombre, tipo).
    - Idempotencia estricta en subcategorías: busca por (categoria_id, nombre).
    - Actualiza icono y color si ya existían, y actualiza el campo orden según la lista canónica.
    - Archiva subcategorías que ya no formen parte de la lista canónica.
    """
    for cat_data in CATEGORIAS_SEED:
        # 1. Buscar si ya existe la categoría por nombre y tipo
        existente = db.query(Categoria).filter(
            Categoria.nombre == cat_data["nombre"],
            Categoria.tipo == cat_data["tipo"]
        ).first()

        if not existente:
            categoria = Categoria(
                nombre=cat_data["nombre"],
                tipo=cat_data["tipo"],
                icono=cat_data["icono"],
                color=cat_data["color"],
                estado="activa"
            )
            db.add(categoria)
            db.flush()  # Para obtener categoria.id
            print(f"[+] Categoria creada: {categoria.nombre} ({categoria.tipo})")
        else:
            categoria = existente
            categoria.icono = cat_data["icono"]
            categoria.color = cat_data["color"]

        # 2. Seedear subcategorías con idempotencia estricta por categoría
        for idx, nombre_sub in enumerate(cat_data["subcategorias"]):
            sub_existente = db.query(Subcategoria).filter(
                Subcategoria.categoria_id == categoria.id,
                Subcategoria.nombre == nombre_sub
            ).first()

            if not sub_existente:
                subcategoria = Subcategoria(
                    categoria_id=categoria.id,
                    nombre=nombre_sub,
                    orden=idx,
                    estado="activa"
                )
                db.add(subcategoria)
                print(f"  [+] Subcategoria creada: {nombre_sub} en {categoria.nombre} (orden {idx})")
            else:
                sub_existente.orden = idx
                sub_existente.estado = "activa"

        # 3. Archivar subcategorías obsoletas de esta categoría
        sub_sobrantes = db.query(Subcategoria).filter(
            Subcategoria.categoria_id == categoria.id,
            ~Subcategoria.nombre.in_(cat_data["subcategorias"])
        ).all()
        for sub_del in sub_sobrantes:
            sub_del.estado = "archivada"
            print(f"  [-] Subcategoria archivada: {sub_del.nombre} en {categoria.nombre}")

    db.commit()
    try:
        from app.services.categoria_service import invalidar_cache_categorias_globales
        invalidar_cache_categorias_globales()
    except Exception:
        pass
    print(f"[OK] Seed de categorias completado: {len(CATEGORIAS_SEED)} categorias canonicas contempladas.")



if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_categorias(db)
    finally:
        db.close()
