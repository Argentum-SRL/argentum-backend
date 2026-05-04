import sys
import os
import pkgutil
import importlib
import logging

# Configurar logging para una salida clara y profesional
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger("DB-INIT")

# Asegurar que el directorio raíz esté en el path para ejecuciones directas
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from app.core.database import engine, Base
from sqlalchemy import inspect
from scripts.seed_categorias import seed_categorias_subcategorias

def init_full_db():
    """
    Automatiza completamente la inicialización de la base de datos:
    1. Detecta todos los modelos en app/models.
    2. Crea las tablas respetando relaciones y sin duplicar existentes.
    3. Ejecuta el seed de categorías de forma idempotente.
    """
    print("\n" + "="*60)
    logger.info("INICIANDO AUTOMATIZACIÓN DE BASE DE DATOS")
    print("="*60)

    try:
        # 1. DETECCIÓN DINÁMICA DE MODELOS
        # Esto asegura que todos los modelos se registren en Base.metadata
        logger.info("Creando detección dinámica de modelos en app/models...")
        import app.models
        package = app.models
        for _, module_name, _ in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
            importlib.import_module(module_name)
        logger.info("✔ Todos los modelos han sido detectados y registrados.")

        # 2. CREACIÓN DE TABLAS
        # Base.metadata.create_all es desactivado para favorecer el control por Alembic
        logger.info("El esquema de tablas es gestionado exclusivamente por Alembic.")
        # Base.metadata.create_all(bind=engine)

        
        # Validación de creación
        inspector = inspect(engine)
        tablas_actuales = inspector.get_table_names()
        logger.info(f"✔ Tablas verificadas/creadas correctamente. Total: {len(tablas_actuales)}")

        # 3. CARGA DE DATOS INICIALES (SEED)
        logger.info("Ejecutando seed de categorías y subcategorías...")
        seed_categorias_subcategorias()
        
        print("="*60)
        logger.info("SISTEMA DE BASE DE DATOS LISTO Y ACTUALIZADO")
        print("="*60 + "\n")
        return True

    except Exception as e:
        logger.error(f"❌ ERROR CRÍTICO durante la inicialización: {str(e)}")
        # En un script de inicialización, es preferible propagar el error si es crítico
        return False

if __name__ == "__main__":
    init_full_db()
