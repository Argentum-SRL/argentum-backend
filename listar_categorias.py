import sys
sys.path.insert(0, '.')
from sqlalchemy import text
from app.core.database import SessionLocal

db = SessionLocal()
rows = db.execute(text('''
    SELECT c.tipo, c.nombre AS categoria, sc.nombre AS subcategoria
    FROM categorias c
    LEFT JOIN subcategorias sc ON sc.categoria_id = c.id
    ORDER BY c.tipo, c.nombre, sc.nombre
''')).mappings().all()

actual_cat = None
for r in rows:
    if r['categoria'] != actual_cat:
        print(f"\n[{r['tipo']}] {r['categoria']}")
        actual_cat = r['categoria']
    if r['subcategoria']:
        print(f"   - {r['subcategoria']}")

print(f"\nTotal categorias: {db.execute(text('SELECT COUNT(*) FROM categorias')).scalar()}")
print(f"Total subcategorias: {db.execute(text('SELECT COUNT(*) FROM subcategorias')).scalar()}")
db.close()
