import sys
import os

# Asegurar que el directorio raíz esté en sys.path para poder importar 'app'
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.database import SessionLocal
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria

categorias_data = [
    {
        "nombre": "Alimentación",
        "tipo": TipoCategoria.EGRESO,
        "icono": "fast-food",
        "color": "#FF5733",
        "subcategorias": ["Supermercado", "Verdulería", "Restaurante", "Delivery", "Cafetería"]
    },
    {
        "nombre": "Transporte",
        "tipo": TipoCategoria.EGRESO,
        "icono": "car",
        "color": "#3357FF",
        "subcategorias": ["Combustible", "Transporte público", "Taxi/Remis", "Peaje", "Estacionamiento"]
    },
    {
        "nombre": "Salud",
        "tipo": TipoCategoria.EGRESO,
        "icono": "medkit",
        "color": "#33FF57",
        "subcategorias": ["Farmacia", "Médico", "Obra social", "Gimnasio"]
    },
    {
        "nombre": "Entretenimiento",
        "tipo": TipoCategoria.EGRESO,
        "icono": "game-controller",
        "color": "#FF33A8",
        "subcategorias": ["Streaming", "Cine/Teatro", "Juegos", "Salidas"]
    },
    {
        "nombre": "Servicios",
        "tipo": TipoCategoria.EGRESO,
        "icono": "flash",
        "color": "#F3FF33",
        "subcategorias": ["Electricidad", "Gas", "Internet", "Teléfono", "Agua"]
    },
    {
        "nombre": "Ropa e indumentaria",
        "tipo": TipoCategoria.EGRESO,
        "icono": "shirt",
        "color": "#33FFF3",
        "subcategorias": ["Ropa", "Calzado", "Accesorios"]
    },
    {
        "nombre": "Educación",
        "tipo": TipoCategoria.EGRESO,
        "icono": "school",
        "color": "#FF8C33",
        "subcategorias": ["Cursos", "Libros", "Materiales", "Cuotas educativas"]
    },
    {
        "nombre": "Vivienda",
        "tipo": TipoCategoria.EGRESO,
        "icono": "home",
        "color": "#8C33FF",
        "subcategorias": ["Alquiler", "Expensas", "Mantenimiento", "Muebles"]
    },
    {
        "nombre": "Ingresos",
        "tipo": TipoCategoria.INGRESO,
        "icono": "cash",
        "color": "#33FF8C",
        "subcategorias": ["Sueldo", "Freelance", "Venta", "Otros ingresos"]
    },
    {
        "nombre": "Otros",
        "tipo": TipoCategoria.EGRESO,
        "icono": "ellipsis-horizontal",
        "color": "#999999",
        "subcategorias": []
    }
]

def run_seed():
    db = SessionLocal()
    try:
        print("Iniciando carga de categorías globales...")
        
        # Verificar si ya existen categorías para no duplicar
        if db.query(Categoria).count() > 0:
            print("Ya existen categorías en la base de datos. El seed no se ejecutará para evitar duplicados.")
            return

        for cat_data in categorias_data:
            categoria = Categoria(
                nombre=cat_data["nombre"],
                tipo=cat_data["tipo"].value,
                icono=cat_data["icono"],
                color=cat_data["color"],
                es_global=True
            )
            db.add(categoria)
            db.flush() # Para obtener el ID de la categoría recién creada
            
            for sub_nombre in cat_data["subcategorias"]:
                subcategoria = Subcategoria(
                    nombre=sub_nombre,
                    categoria_id=categoria.id,
                    es_global=True
                )
                db.add(subcategoria)
                
        db.commit()
        print("¡Categorías globales cargadas con éxito!")
        
    except Exception as e:
        db.rollback()
        print(f"Error al cargar el seed: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    run_seed()
