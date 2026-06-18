import sys
import os

# Asegurar que el directorio raíz del proyecto esté en el PYTHONPATH
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy.orm import Session
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.usuario import Usuario, RolUsuario


def create_admin():
    if len(sys.argv) < 2:
        print("Uso: python scripts/create_admin.py usuario@email.com")
        sys.exit(1)

    email = sys.argv[1].strip().lower()
    
    db: Session = SessionLocal()
    try:
        user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()
        
        if not user:
            print(f"Error: No se encontró ningún usuario con el email '{email}'.")
            sys.exit(1)
            
        if user.is_admin and user.rol == RolUsuario.ADMIN:
            print(f"Aviso: El usuario '{user.nombre} {user.apellido}' ({email}) ya es administrador.")
            sys.exit(0)
            
        user.is_admin = True
        user.rol = RolUsuario.ADMIN
        db.commit()
        
        print(f"¡Éxito! El usuario '{user.nombre} {user.apellido}' ({email}) ha sido promovido a administrador.")
    except Exception as e:
        print(f"Error inesperado al ejecutar el script: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    create_admin()
