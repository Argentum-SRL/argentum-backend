from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.usuario import (
    UsuarioResponse,
    MetodosLoginResponse,
    EditarDatosPersonales,
    EditarEmail,
    EditarPassword,
    EditarTelefono,
    EditarCicloFinanciero,
    EditarMoneda,
)
from app.services import usuario_service

router = APIRouter(prefix="/usuarios", tags=["usuarios"])

@router.get("/me", response_model=UsuarioResponse)
def get_me(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Devuelve el usuario autenticado completo."""
    return current_user

@router.get("/me/metodos-login", response_model=MetodosLoginResponse)
def get_metodos_login(
    current_user: Usuario = Depends(get_current_user)
):
    """Devuelve los métodos de inicio de sesión disponibles y configurables."""
    return {
        "email_password": current_user.email_verificado and current_user.password_configurada,
        "telefono": current_user.telefono_verificado,
        "google": current_user.email_verificado,
        "puede_agregar_password": not current_user.password_configurada and current_user.email_verificado,
        "puede_agregar_email": not current_user.email_verificado,
        "puede_agregar_telefono": not current_user.telefono_verificado
    }

@router.put("/me/datos-personales", response_model=UsuarioResponse)
def update_datos_personales(
    datos: EditarDatosPersonales,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza nombre y apellido del usuario autenticado."""
    return usuario_service.actualizar_datos_personales(db, current_user, datos)

@router.put("/me/email")
def update_email(
    datos: EditarEmail,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza email del usuario autenticado (requiere contraseña y verificación)."""
    return usuario_service.actualizar_email(db, current_user, datos)

@router.put("/me/password")
def update_password(
    datos: EditarPassword,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza contraseña del usuario autenticado."""
    return usuario_service.actualizar_password(db, current_user, datos)

@router.put("/me/telefono")
def update_telefono(
    datos: EditarTelefono,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza teléfono del usuario autenticado (envía código por WhatsApp)."""
    return usuario_service.actualizar_telefono(db, current_user, datos)

@router.put("/me/ciclo-financiero", response_model=UsuarioResponse)
def update_ciclo_financiero(
    datos: EditarCicloFinanciero,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza configuración de ciclo financiero."""
    return usuario_service.actualizar_ciclo_financiero(db, current_user, datos)

@router.put("/me/moneda", response_model=UsuarioResponse)
def update_moneda(
    datos: EditarMoneda,
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Actualiza configuración de moneda."""
    return usuario_service.actualizar_moneda(db, current_user, datos)

@router.post("/me/foto")
def upload_foto(
    archivo: UploadFile = File(...),
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Sube o actualiza la foto de perfil."""
    foto_url = usuario_service.actualizar_foto(db, current_user, archivo)
    return {"foto_url": foto_url}

@router.delete("/me/foto")
def delete_foto(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Elimina la foto de perfil."""
    return usuario_service.eliminar_foto(db, current_user)

@router.delete("/me")
def delete_me(
    current_user: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Elimina el usuario y todos sus datos en cascada."""
    return usuario_service.eliminar_usuario(db, current_user)
