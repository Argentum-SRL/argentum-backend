import sys
import os
from uuid import UUID
from decimal import Decimal
from datetime import date

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.models.billetera import Billetera
from app.schemas.transaccion import TransaccionCreate
from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion
from app.services import transaccion_service

db = SessionLocal()
try:
    # 1. Obtener un usuario y una de sus billeteras
    user = db.query(Usuario).first()
    if not user:
        print("No user found in DB")
        sys.exit(0)
    
    billetera = db.query(Billetera).filter(Billetera.usuario_id == user.id).first()
    if not billetera:
        print("No billetera found for user", user.id)
        sys.exit(0)

    print(f"Testing with User ID: {user.id}, Billetera ID: {billetera.id}")

    # 2. Crear payload para crear_transaccion
    # Simular lo que manda el front: sin categoria_id ni subcategoria_id
    data = TransaccionCreate(
        tipo=TipoTransaccion.EGRESO,
        monto=Decimal("150.00"),
        moneda=billetera.moneda,
        fecha=date.today(),
        descripcion="Test transaction",
        categoria_id=None,
        subcategoria_id=None,
        metodo_pago=MetodoPago.DEBITO,
        billetera_id=billetera.id,
        tarjeta_id=None,
        origen=OrigenTransaccion.MANUAL,
        estado_verificacion=None
    )

    print("Calling transaccion_service.crear_transaccion...")
    tx = transaccion_service.crear_transaccion(db, user.id, data)
    print("SUCCESS! Created transaction ID:", tx.id)

except Exception as e:
    import traceback
    print("FAILED with exception:")
    traceback.print_exc()
finally:
    db.close()
