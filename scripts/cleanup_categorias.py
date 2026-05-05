import sys
import os

# Añadir el directorio raíz al path para poder importar la app
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.categoria import Categoria
from app.models.transaccion import Transaccion
from app.models.transaccion_recurrente import TransaccionRecurrente
from app.models.subcategoria import Subcategoria
from app.models.presupuesto_categoria import PresupuestoCategoria

MAPPING = {
    "Ropa": "Ropa e indumentaria",
    "Entretenimiento": "Entretenimiento y salidas",
    "Otros": "Otros gastos",
    "Servicios": "Vivienda",  # La vieja servicios tenía luz/gas/agua, ahora están en Vivienda
    "Bienestar": "Salud",
    "Tecnología": "Servicios digitales",
    "Finanzas": "Otros gastos",
    "Seguros": "Otros gastos",
    "Impuestos": "Otros gastos",
    "Mascotas": "Otros gastos",
    "Ingresos": "Otros ingresos",
    "Comida Afuera": "Alimentación"
}

def cleanup():
    db = SessionLocal()
    try:
        print("🚀 Iniciando limpieza de categorías duplicadas...\n")
        
        for viejo_nombre, nuevo_nombre in MAPPING.items():
            # Buscar categoría vieja
            vieja = db.query(Categoria).filter(Categoria.nombre == viejo_nombre).first()
            # Buscar categoría nueva
            nueva = db.query(Categoria).filter(Categoria.nombre == nuevo_nombre).first()
            
            if vieja and nueva:
                print(f"📦 Migrando '{viejo_nombre}' -> '{nuevo_nombre}'...")
                
                # 1. Migrar Transacciones
                txs = db.query(Transaccion).filter(Transaccion.categoria_id == vieja.id).all()
                for tx in txs:
                    tx.categoria_id = nueva.id
                
                # 2. Migrar Recurrentes
                recs = db.query(TransaccionRecurrente).filter(TransaccionRecurrente.categoria_id == vieja.id).all()
                for rec in recs:
                    rec.categoria_id = nueva.id
                
                # 3. Migrar Presupuestos
                pres = db.query(PresupuestoCategoria).filter(PresupuestoCategoria.categoria_id == vieja.id).all()
                for p in pres:
                    p.categoria_id = nueva.id
                
                # 4. Migrar Subcategorías (si la vieja tenía subcats que no existen en la nueva)
                subcats_viejas = db.query(Subcategoria).filter(Subcategoria.categoria_id == vieja.id).all()
                for sub in subcats_viejas:
                    # Verificar si ya existe una con el mismo nombre en la nueva
                    existe = db.query(Subcategoria).filter(
                        Subcategoria.categoria_id == nueva.id,
                        Subcategoria.nombre == sub.nombre
                    ).first()
                    
                    if not existe:
                        sub.categoria_id = nueva.id
                    else:
                        # Si ya existe, migramos las transacciones que usen esta subcat vieja a la nueva
                        txs_sub = db.query(Transaccion).filter(Transaccion.subcategoria_id == sub.id).all()
                        for ts in txs_sub:
                            ts.subcategoria_id = existe.id
                        db.delete(sub)

                db.flush()
                # 5. Borrar categoría vieja
                db.delete(vieja)
                print(f"✅ '{viejo_nombre}' eliminada y migrada.")
            
            elif vieja and not nueva:
                print(f"⚠️ Se encontró '{viejo_nombre}' pero no la destino '{nuevo_nombre}'.")
        
        db.commit()
        print("\n✨ Limpieza completada con éxito.")
        
    except Exception as e:
        db.rollback()
        print(f"❌ Error durante la limpieza: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    cleanup()
