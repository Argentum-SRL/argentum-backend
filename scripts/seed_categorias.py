import sys
import os

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from sqlalchemy.orm import Session

CATEGORIAS_SEED = [
    # ── EGRESOS ──────────────────────────────────────────
    {
        "nombre": "Alimentación",
        "tipo": "egreso",
        "icono": "alimentacion",
        "color": "#3B6D11",
        "subcategorias": [
            "Supermercado", "Verdulería", "Carnicería", "Pollería",
            "Panadería", "Pescadería", "Dietética", "Restaurante",
            "Delivery", "Cafetería", "Bar", "Heladería"
        ]
    },
    {
        "nombre": "Transporte",
        "tipo": "egreso",
        "icono": "transporte",
        "color": "#52565F",
        "subcategorias": [
            "Transporte público", "Taxi / Remis", "Combustible",
            "Peaje", "Estacionamiento", "Mantenimiento del auto",
            "Seguro del auto", "Bicicleta / Patineta"
        ]
    },
    {
        "nombre": "Vivienda",
        "tipo": "egreso",
        "icono": "casa",
        "color": "#92400E",
        "subcategorias": [
            "Alquiler", "Expensas", "Electricidad", "Gas", "Agua",
            "Internet", "Teléfono fijo", "Cable / TV",
            "Limpieza", "Mantenimiento", "Muebles y decoración"
        ]
    },
    {
        "nombre": "Salud y cuidado personal",
        "tipo": "egreso",
        "icono": "medicina",
        "color": "#D97706",
        "subcategorias": [
            "Farmacia", "Médico / Consulta", "Obra social / Prepaga",
            "Dentista", "Óptica", "Terapia",
            "Kinesiología", "Estudios médicos", "Peluquería", "Gimnasio", "Spa"
        ]
    },
    {
        "nombre": "Servicios digitales",
        "tipo": "egreso",
        "icono": "serviciosdigitales",
        "color": "#4F46E5",
        "subcategorias": [
            "Streaming", "Música", "Software / Apps",
            "Almacenamiento en la nube", "Dominio / Hosting"
        ]
    },
    {
        "nombre": "Educación",
        "tipo": "egreso",
        "icono": "libros",
        "color": "#16A34A",
        "subcategorias": [
            "Cuotas escolares / universitarias", "Cursos y capacitaciones",
            "Libros y materiales", "Idiomas", "Guardería / Jardín"
        ]
    },
    {
        "nombre": "Ropa e indumentaria",
        "tipo": "egreso",
        "icono": "remera",
        "color": "#7C3AED",
        "subcategorias": [
            "Ropa", "Calzado", "Accesorios",
            "Ropa deportiva", "Ropa interior"
        ]
    },
    {
        "nombre": "Entretenimiento y salidas",
        "tipo": "egreso",
        "icono": "entretenimiento",
        "color": "#993C1D",
        "subcategorias": [
            "Cine / Teatro / Recitales", "Salidas con amigos",
            "Vacaciones y viajes", "Hobbies",
            "Juegos y videojuegos", "Deportes", "Gimnasio"
        ]
    },
    {
        "nombre": "Otros gastos",
        "tipo": "egreso",
        "icono": "default",
        "color": "#8E9198",
        "subcategorias": [
            "Mascotas", "Regalos y donaciones", "Impuestos y tasas",
            "Seguro de vida / hogar", "Gastos bancarios y comisiones", "Otros"
        ]
    },
    # ── INGRESOS ─────────────────────────────────────────
    {
        "nombre": "Trabajo en relación de dependencia",
        "tipo": "ingreso",
        "icono": "salario",
        "color": "#185FA5",
        "subcategorias": [
            "Sueldo", "Aguinaldo", "Horas extra", "Bonos"
        ]
    },
    {
        "nombre": "Trabajo independiente",
        "tipo": "ingreso",
        "icono": "trato",
        "color": "#16A34A",
        "subcategorias": [
            "Freelance", "Honorarios", "Consultoría", "Venta de productos"
        ]
    },
    {
        "nombre": "Otros ingresos",
        "tipo": "ingreso",
        "icono": "dineroenmano",
        "color": "#D97706",
        "subcategorias": [
            "Alquiler cobrado", "Dividendos / inversiones",
            "Venta de bienes", "Reintegros", "Regalos recibidos", "Otros"
        ]
    },
]

def seed_categorias(db: Session):
    for cat_data in CATEGORIAS_SEED:
        # Buscar si ya existe por nombre + tipo
        existente = db.query(Categoria).filter(
            Categoria.nombre == cat_data["nombre"],
            Categoria.tipo == cat_data["tipo"],
            Categoria.es_global == True
        ).first()

        if not existente:
            categoria = Categoria(
                nombre=cat_data["nombre"],
                tipo=cat_data["tipo"],
                icono=cat_data["icono"],
                color=cat_data["color"],
                es_global=True,
                creador_id=None,
                estado="activa"
            )
            db.add(categoria)
            db.flush()  # para obtener el id antes del commit
            print(f"✔ Categoría creada: {categoria.nombre}")
        else:
            categoria = existente
            # Actualizamos también el color y el icono de la categoría global si cambió
            categoria.icono = cat_data["icono"]
            categoria.color = cat_data["color"]

        # Seedear subcategorías
        for nombre_sub in cat_data["subcategorias"]:
            sub_existente = db.query(Subcategoria).filter(
                Subcategoria.categoria_id == categoria.id,
                Subcategoria.nombre == nombre_sub,
                Subcategoria.es_global == True
            ).first()

            if not sub_existente:
                subcategoria = Subcategoria(
                    categoria_id=categoria.id,
                    nombre=nombre_sub,
                    es_global=True,
                    creador_id=None,
                    estado="activa"
                )
                db.add(subcategoria)
                print(f"  ✔ Subcategoría creada: {nombre_sub}")

    db.commit()
    print(f"\n✅ Seed de categorías completado: {len(CATEGORIAS_SEED)} categorías, {sum(len(c['subcategorias']) for c in CATEGORIAS_SEED)} subcategorías totales contempladas.")

if __name__ == "__main__":
    db = SessionLocal()
    try:
        seed_categorias(db)
    finally:
        db.close()
