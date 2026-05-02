import sys
import os

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.categoria import Categoria, TipoCategoria, EstadoCategoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from sqlalchemy import func

# Configurar la salida para que soporte caracteres especiales en Windows
if sys.stdout.encoding != 'utf-8':
    try:
        import codecs
        sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
        sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
    except Exception:
        pass


def seed_categorias_subcategorias():
    """
    Carga todas las categorías y subcategorías en la base de datos.
    Idempotente: no duplica datos si ya existen.
    """
    db = SessionLocal()
    
    try:
        # Datos proporcionados por el usuario
        datos = {
            "Alimentación": [
                "Supermercado", "Verdulería", "Carnicería", "Restaurante", "Delivery", "Cafetería"
            ],
            "Transporte": [
                "Combustible", "Transporte público", "Taxi / Remis / Uber", "Peaje", "Estacionamiento", "Mantenimiento vehículo"
            ],
            "Vivienda": [
                "Alquiler", "Expensas", "Mantenimiento / Reparaciones", "Muebles", "Limpieza"
            ],
            "Servicios": [
                "Electricidad", "Gas", "Agua", "Internet", "Teléfono", "Cable / TV"
            ],
            "Salud": [
                "Farmacia", "Médico / Consulta", "Obra social / Seguro médico", "Estudios / Análisis"
            ],
            "Bienestar": [
                "Gimnasio", "Terapia", "Estética / Peluquería", "Spa / Cuidado personal"
            ],
            "Educación": [
                "Cursos", "Libros", "Materiales", "Cuotas educativas"
            ],
            "Entretenimiento": [
                "Streaming", "Cine / Teatro", "Juegos", "Salidas / Bares"
            ],
            "Ropa e indumentaria": [
                "Ropa", "Calzado", "Accesorios"
            ],
            "Tecnología": [
                "Dispositivos", "Software / Apps", "Suscripciones digitales", "Reparaciones"
            ],
            "Finanzas": [
                "Comisiones bancarias", "Intereses", "Tarjetas de crédito", "Inversiones", "Préstamos"
            ],
            "Seguros": [
                "Seguro auto", "Seguro hogar", "Seguro vida"
            ],
            "Impuestos": [
                "Impuestos nacionales", "Impuestos provinciales", "Impuestos municipales"
            ],
            "Mascotas": [
                "Alimento mascotas", "Veterinario", "Accesorios"
            ],
            "Ingresos": [
                "Sueldo", "Freelance", "Ventas", "Inversiones", "Otros ingresos"
            ],
            "Regalos y donaciones": [
                "Regalos", "Donaciones"
            ],
            "Otros": [
                "General"
            ]
        }

        total_categorias_creadas = 0
        total_subcategorias_creadas = 0

        for nombre_cat, subcats in datos.items():
            # Buscar si la categoría existe (case insensitive)
            categoria = db.query(Categoria).filter(
                func.lower(Categoria.nombre) == func.lower(nombre_cat)
            ).first()

            if not categoria:
                # Definir tipo: solo "Ingresos" es INGRESO
                tipo = TipoCategoria.INGRESO if nombre_cat == "Ingresos" else TipoCategoria.EGRESO
                
                categoria = Categoria(
                    nombre=nombre_cat,
                    tipo=tipo,
                    es_global=True,
                    estado=EstadoCategoria.ACTIVA
                )
                db.add(categoria)
                db.flush()  # Para obtener el ID
                print(f"✔ Categoría creada: {nombre_cat}")
                total_categorias_creadas += 1
            else:
                print(f"⚠ Categoría ya existe: {nombre_cat}")

            # Procesar subcategorías
            for nombre_sub in subcats:
                subcategoria = db.query(Subcategoria).filter(
                    Subcategoria.categoria_id == categoria.id,
                    func.lower(Subcategoria.nombre) == func.lower(nombre_sub)
                ).first()

                if not subcategoria:
                    subcategoria = Subcategoria(
                        categoria_id=categoria.id,
                        nombre=nombre_sub,
                        es_global=True,
                        estado=EstadoSubcategoria.ACTIVA
                    )
                    db.add(subcategoria)
                    print(f"✔ Subcategoría creada: {nombre_sub}")
                    total_subcategorias_creadas += 1
                else:
                    print(f"⚠ Subcategoría ya existe: {nombre_sub}")

        db.commit()
        
        print("\n" + "="*40)
        print("Seed completado correctamente")
        print(f"Categorías creadas: {total_categorias_creadas}")
        print(f"Subcategorías creadas: {total_subcategorias_creadas}")
        print("="*40 + "\n")

    except Exception as e:
        db.rollback()
        print(f"❌ Error durante el seed: {e}")
        raise e
    finally:
        db.close()


if __name__ == "__main__":
    seed_categorias_subcategorias()
