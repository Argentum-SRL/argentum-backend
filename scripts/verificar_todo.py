"""
Script de verificación exhaustiva post-seed y post-suite.
Verifica 5.1, 5.4, 5.5 y 5.6 imprimiendo salidas crudas.
"""
from __future__ import annotations
import os
import sys
from decimal import Decimal

sys.path.insert(0, ".")
os.environ["LOG_LEVEL"] = "CRITICAL"

from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.cuota import Cuota
from app.models.suscripcion import Suscripcion
from app.models.meta import Meta
from app.models.conversacion_wpp import ConversacionWpp
from app.services.dashboard_service import get_resumen_completo
from scripts.regresion.suite_regresion_whatsapp import SALDOS_REFERENCIA_21

def run_verificaciones():
    db = SessionLocal()
    try:
        # 5.1 App import check
        print("=== 5.1 VERIFICACION DE ARRANQUE ===")
        from app.main import app
        print(f"FastAPI app instancia cargada exitosamente: {app.title} v{app.version}")

        # 5.4 Dashboard de los 6 usuarios
        print("\n=== 5.4 DASHBOARD DE LOS 6 USUARIOS ===")
        emails = [
            "mrm291201@gmail.com",
            "angieperiolo@hotmail.com",
            "giordaninosebas@gmail.com",
            "albanopavia@gmail.com",
            "sebastiangiordaninoformoso@gmail.com",
            "testingadmin@argentum.com",
        ]
        for email in emails:
            u = db.query(Usuario).filter(Usuario.email == email).first()
            if not u:
                print(f"Usuario {email}: NO ENCONTRADO")
                continue
            res = get_resumen_completo(db, u)
            resumen = res.get("resumen") or {}
            billeteras = res.get("billeteras", [])
            total_disponible = sum((Decimal(str(b["saldo_actual"])) for b in billeteras if str(b.get("moneda")) in ("ARS", "Moneda.ARS")), Decimal("0"))
            proximos_pagos = resumen.get("proximos_pagos", [])
            proximos_pagos_usd = resumen.get("proximos_pagos_usd", [])
            gastos_cat = resumen.get("gastos_por_categoria", [])
            print(f"[{u.email}] Nombre: {u.nombre} {u.apellido} | Billeteras: {len(billeteras)} | Disp ARS: ${total_disponible:,.2f} | Próx Pagos ARS: {len(proximos_pagos)} | Próx Pagos USD: {len(proximos_pagos_usd)} | Categorías con gasto: {len(gastos_cat)}")

        # 5.5 Conteos de todas las tablas y saldos de las 21 billeteras
        print("\n=== 5.5 CONTEOS DE TABLAS ===")
        from sqlalchemy import text
        tablas = [
            "usuarios", "billeteras", "tarjetas_credito", "transacciones",
            "cuotas", "grupos_cuotas", "suscripciones", "historial_suscripciones",
            "metas", "movimientos_meta", "transferencias_internas",
            "conversaciones_wpp", "perfiles_financieros", "ipc_cache"
        ]
        for t in tablas:
            cnt = db.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            print(f"  {t}: {cnt}")

        print("\n=== 5.5 SALDOS DE LAS 21 BILLETERAS ===")
        bills = db.query(Billetera, Usuario.email).join(Usuario, Billetera.usuario_id == Usuario.id).order_by(Usuario.email, Billetera.nombre).all()
        print(f"Total billeteras encontradas: {len(bills)}")
        for b, u_email in bills:
            print(f"  {u_email} | {b.nombre} ({b.moneda.value}): ${b.saldo_actual:,.2f}")

        # 5.6 Confirmación de que ninguna cuenta que no sea testingadmin fue modificada
        print("\n=== 5.6 COMPARACION DE LAS OTRAS 5 CUENTAS CONTRA REFERENCIA ===")
        otras_cuentas_alteradas = []
        for (ref_email, ref_bill), ref_monto in SALDOS_REFERENCIA_21.items():
            if ref_email == "testingadmin@argentum.com":
                continue
            b = db.query(Billetera).join(Usuario, Billetera.usuario_id == Usuario.id).filter(Usuario.email == ref_email, Billetera.nombre == ref_bill).first()
            if not b:
                otras_cuentas_alteradas.append((ref_email, ref_bill, "NO ENCONTRADA", ref_monto))
            elif b.saldo_actual != ref_monto:
                otras_cuentas_alteradas.append((ref_email, ref_bill, b.saldo_actual, ref_monto))
            else:
                print(f"  [OK] {ref_email} | {ref_bill}: actual={b.saldo_actual} == ref={ref_monto}")

        if otras_cuentas_alteradas:
            print(f"ALERTA: Se modificaron cuentas no autorizadas ({len(otras_cuentas_alteradas)}):")
            for alt in otras_cuentas_alteradas:
                print(f"  - {alt}")
        else:
            print("CONFIRMADO: Ninguna de las otras 5 cuentas sufrió alteraciones (0 desviaciones).")

    finally:
        db.close()

if __name__ == "__main__":
    run_verificaciones()
