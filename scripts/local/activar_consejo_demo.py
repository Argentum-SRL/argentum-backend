import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")
from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.models.consejo import ConsejoEmitido

if len(sys.argv) < 2:
    print("Uso: python activar_consejo_demo.py <colchon|plata_quieta|hormiga|sin_ingresos|reset>")
    sys.exit(1)

tipo_elegido = sys.argv[1].lower()

db = SessionLocal()
u = db.query(Usuario).filter(Usuario.email == "demo.argentum@argentum.com").first()
if not u:
    print("Usuario demo no encontrado")
    sys.exit(1)

if tipo_elegido == "reset":
    # Quita respuestas de todos
    for c in db.query(ConsejoEmitido).filter(ConsejoEmitido.usuario_id == u.id):
        c.respuesta = None
    db.commit()
    print("Reset OK: todas las respuestas limpiadas.")
    db.close()
    sys.exit(0)

target = db.query(ConsejoEmitido).filter(
    ConsejoEmitido.usuario_id == u.id,
    ConsejoEmitido.tipo == tipo_elegido,
).first()

if not target:
    print(f"Tipo '{tipo_elegido}' no encontrado. Disponibles: colchon, plata_quieta, hormiga, sin_ingresos")
    db.close()
    sys.exit(1)

target.respuesta = None
target.created_at = datetime.now(timezone.utc)
db.commit()
print(f"OK: Consejo '{tipo_elegido}' activado como el más reciente para {u.email}.")
db.close()

