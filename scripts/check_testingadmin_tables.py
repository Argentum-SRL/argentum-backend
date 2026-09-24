"""
Check all table counts for testingadmin
"""
from __future__ import annotations
import sys
sys.path.insert(0, ".")
from sqlalchemy import text, inspect
from app.core.database import SessionLocal
from app.models.usuario import Usuario

db = SessionLocal()
u = db.query(Usuario).filter(Usuario.email == 'testingadmin@argentum.com').first()
insp = inspect(db.get_bind())
tables = insp.get_table_names()
print("=== TABLAS CON FILAS DE TESTINGADMIN ===")
for t in sorted(tables):
    cols = [c['name'] for c in insp.get_columns(t)]
    if 'usuario_id' in cols:
        cnt = db.execute(text(f'SELECT count(*) FROM "{t}" WHERE usuario_id = :uid'), {'uid': u.id}).scalar()
        if cnt > 0:
            print(f"{t}: {cnt}")
db.close()
