import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

sys.path.insert(0, ".")
from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.models.consejo import ConsejoEmitido
from app.utils.fecha import hoy_argentina

db = SessionLocal()
u = db.query(Usuario).filter(Usuario.email == "demo.argentum@argentum.com").first()
if not u:
    print("Usuario demo.argentum@argentum.com no encontrado!")
    sys.exit(1)

hoy = hoy_argentina()
vence = hoy + timedelta(days=7)

# Limpiar consejos previos de prueba
db.query(ConsejoEmitido).filter(ConsejoEmitido.usuario_id == u.id).delete()
db.commit()

now = datetime.now(timezone.utc)

# 1. Sin ingresos (cargar ingreso)
c_ingreso = ConsejoEmitido(
    id=uuid4(),
    usuario_id=u.id,
    tipo="sin_ingresos",
    clave=f"sin_ingresos/ARS/{hoy.isoformat()}",
    moneda="ARS",
    texto="Hace más de 45 días que no registrás ningún ingreso. Si entró plata, sumala para que las cuentas y proyecciones den bien.",
    accion="cargar_ingreso",
    accion_datos={},
    valores={"egresos_45": 20, "ingresos_45": 0},
    fecha_emision=hoy,
    fecha_vence=vence,
    created_at=now - timedelta(minutes=4),
)

# 2. Gastos hormiga (ver movimientos)
c_hormiga = ConsejoEmitido(
    id=uuid4(),
    usuario_id=u.id,
    tipo="hormiga",
    clave=f"hormiga/ARS/{hoy.isoformat()}",
    moneda="ARS",
    texto="Los gastos chicos y seguidos (Cafetería y Taxi y apps) suman unos $38.000 por mes: $456.000 al año.",
    accion="ver_movimientos",
    accion_datos={"categoria": "Gastronomía", "subcategoria": "Cafetería"},
    valores={"total": "38000.00"},
    fecha_emision=hoy,
    fecha_vence=vence,
    created_at=now - timedelta(minutes=3),
)

# 3. Plata quieta (sin acción, solo texto + feedback)
c_plata_quieta = ConsejoEmitido(
    id=uuid4(),
    usuario_id=u.id,
    tipo="plata_quieta",
    clave=f"plata_quieta/ARS/{hoy.isoformat()}",
    moneda="ARS",
    texto="Tenés unos $1.153.000 que este mes no vas a necesitar. Con la inflación esperada (1,8% en diciembre, REM de septiembre), pierden unos $21.000 por mes si quedan quietos. Podés pasar una parte a Balanz.",
    accion=None,
    accion_datos=None,
    valores={"sobrante": "1153000.00"},
    fecha_emision=hoy,
    fecha_vence=vence,
    created_at=now - timedelta(minutes=2),
)

# 4. Colchón (crear meta) -> Este es el más reciente, se verá primero!
c_colchon = ConsejoEmitido(
    id=uuid4(),
    usuario_id=u.id,
    tipo="colchon",
    clave=f"colchon/ARS/{hoy.isoformat()}",
    moneda="ARS",
    texto="Si un mes no entrara plata, lo que tenés alcanza para unos 15 días de tus gastos. Un primer objetivo es medio mes: $750.000.",
    accion="crear_meta",
    accion_datos={
        "nombre": "Fondo de emergencia",
        "monto_objetivo": 750000,
        "moneda": "ARS",
    },
    valores={"dias": 15, "objetivo": "750000"},
    fecha_emision=hoy,
    fecha_vence=vence,
    created_at=now - timedelta(minutes=1),
)

db.add_all([c_ingreso, c_hormiga, c_plata_quieta, c_colchon])
db.commit()

print(f"OK: 4 consejos forzados en la cuenta {u.email}.")
print("Orden de visualización (del más nuevo al más viejo al ir respondiendo):")
print("1. Colchón (con botón 'Crear meta')")
print("2. Plata quieta (solo texto informativo y menú de feedback)")
print("3. Gastos hormiga (con botón 'Ver movimientos')")
print("4. Sin ingresos (con botón 'Cargar ingreso')")
db.close()

