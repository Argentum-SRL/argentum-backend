import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.database import SessionLocal
from app.models.usuario import Usuario
from app.services.analisis_financiero_service import calcular_perfil_nuevo
from app.services.proyeccion_service import calcular_proyeccion

def main():
    print("--- INICIANDO BACKFILL MODAL BIENVENIDA FINANCIERA ---")
    db = SessionLocal()
    try:
        users = db.query(Usuario).order_by(Usuario.email).all()
        print(f"Total usuarios en base de datos: {len(users)}")
        
        marcados_true = []
        marcados_false = []
        detalles = []

        for u in users:
            perfil_mostrar = False
            proy_mostrar = False

            try:
                res_perfil = calcular_perfil_nuevo(db, u)
                perfil_mostrar = bool(res_perfil.get("mostrar_card", False))
            except Exception as e:
                print(f"[WARN] Error al calcular perfil para {u.email}: {e}")

            try:
                res_proy = calcular_proyeccion(db, u)
                proy_mostrar = bool(res_proy.get("mostrar_card", False))
            except Exception as e:
                print(f"[WARN] Error al calcular proyección para {u.email}: {e}")

            califica = perfil_mostrar or proy_mostrar
            u.modal_bienvenida_financiera_visto = califica

            info = {
                "email": u.email,
                "nombre": f"{u.nombre or ''} {u.apellido or ''}".strip() or "Sin nombre",
                "perfil_mostrar": perfil_mostrar,
                "proy_mostrar": proy_mostrar,
                "resultado_flag": califica,
            }
            detalles.append(info)

            if califica:
                marcados_true.append(info)
            else:
                marcados_false.append(info)

        db.commit()
        print("Transacción commiteada exitosamente.")

        print("\n=== DETALLE POR USUARIO ===")
        for d in detalles:
            print(f"- {d['email']} ({d['nombre']}): Perfil={d['perfil_mostrar']}, Proyección={d['proy_mostrar']} -> visto={d['resultado_flag']}")

        print(f"\nRESUMEN BACKFILL:")
        print(f"Total evaluados: {len(users)}")
        print(f"Marcados como ya visto (True): {len(marcados_true)}")
        print(f"Permanecen en False: {len(marcados_false)}")

    finally:
        db.close()

if __name__ == "__main__":
    main()
