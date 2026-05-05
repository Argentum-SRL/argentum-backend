import sys
import os

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.subcategoria import Subcategoria
from app.models.transaccion import Transaccion

# MAPPING: "Nombre viejo" -> "Nombre nuevo"
SUBCAT_MAPPING = {
    "Taxi / Remis / Uber": "Taxi / Remis",
    "Mantenimiento vehículo": "Mantenimiento del auto"
}

def cleanup_subcats():
    db = SessionLocal()
    try:
        print("🚀 Iniciando limpieza de subcategorías duplicadas en Transporte...\n")
        
        for viejo_nombre, nuevo_nombre in SUBCAT_MAPPING.items():
            # Buscar subcategoría vieja y nueva
            vieja = db.query(Subcategoria).filter(Subcategoria.nombre == viejo_nombre).first()
            nueva = db.query(Subcategoria).filter(Subcategoria.nombre == nuevo_nombre).first()
            
            if vieja and nueva:
                print(f"📦 Migrando transacciones: '{viejo_nombre}' -> '{nuevo_nombre}'...")
                
                # Migrar Transacciones que usen la vieja subcat
                txs = db.query(Transaccion).filter(Transaccion.subcategoria_id == vieja.id).all()
                for tx in txs:
                    tx.subcategoria_id = nueva.id
                
                db.flush()
                # Borrar la vieja
                db.delete(vieja)
                print(f"✅ '{viejo_nombre}' eliminada.")
            else:
                print(f"ℹ️ No se requirió acción para '{viejo_nombre}' (ya limpia).")
        
        db.commit()
        print("\n✨ Limpieza de subcategorías completada.")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    cleanup_subcats()
