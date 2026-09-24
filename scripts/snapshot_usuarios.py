"""
Snapshot of all users to ensure NO other user is modified
"""
from __future__ import annotations
import sys
sys.path.insert(0, ".")
from sqlalchemy import text
from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion

db = SessionLocal()
users = db.query(Usuario).order_by(Usuario.email).all()
print("=== SNAPSHOT USUARIOS (PRE-MODIFICACION) ===")
for u in users:
    tx_cnt = db.query(Transaccion).filter(Transaccion.usuario_id == u.id).count()
    wallets = db.query(Billetera).filter(Billetera.usuario_id == u.id).all()
    w_info = ", ".join([f"{w.nombre}: ${w.saldo_actual:,.2f}" for w in wallets])
    print(f"{u.email} (ID: {u.id}) | TXs: {tx_cnt} | Billeteras: [{w_info}]")
db.close()
