from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from uuid import UUID
import logging

from app.core.database import get_db
from app.core.auth import get_current_admin_user
from app.models.usuario import Usuario
from app.schemas.admin import (
    UsuarioAdminResponse,
    UsuarioAdminListResponse,
    PaginatedUsuariosResponse,
    CambiarEstadoRequest,
    ResetearOnboardingRequest,
)
from app.services import admin_service
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin_user)],
)


def _map_onboarding_step(user: Usuario) -> str | None:
    if user.onboarding_completo:
        return None
    pasos = []
    if not user.nombre or not user.apellido or not user.fecha_nacimiento or not user.sexo:
        pasos.append("datos_personales")
    elif not user.ciclo_tipo or not user.ciclo_valor:
        pasos.append("ciclo_financiero")
    else:
        pasos.append("moneda")
    return pasos[0] if pasos else "moneda"


@router.get("/usuarios", response_model=dict)
def get_usuarios(
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    estado: str | None = None,
    onboarding: str | None = None,
    wpp: str | None = None,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Lista paginada y filtrada de usuarios. Excluye datos financieros."""
    logger.info("[ADMIN] Admin %s (%s) listó usuarios. page=%s limit=%s", admin.id, admin.email, page, limit)
    res = admin_service.listar_usuarios(
        db, page=page, limit=limit, search=search, estado=estado, onboarding=onboarding, wpp=wpp
    )
    
    # Mapear a formato de respuesta estándar
    usuarios_mapped = [UsuarioAdminListResponse.model_validate(u) for u in res["usuarios"]]
    data = PaginatedUsuariosResponse(
        total=res["total"],
        page=res["page"],
        limit=res["limit"],
        pages=res["pages"],
        usuarios=usuarios_mapped,
    )
    return {
        "success": True,
        "data": data.model_dump(),
        "message": "Usuarios listados correctamente."
    }


@router.get("/usuarios/{usuario_id}", response_model=dict)
def get_usuario_detalle(
    usuario_id: UUID,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Detalle operativo de un usuario específico."""
    logger.info("[ADMIN] Admin %s (%s) solicitó detalle de usuario %s", admin.id, admin.email, usuario_id)
    user = admin_service.obtener_usuario(db, usuario_id)
    
    user_data = UsuarioAdminResponse.model_validate(user)
    user_data.paso_onboarding_actual = _map_onboarding_step(user)
    
    return {
        "success": True,
        "data": user_data.model_dump(),
        "message": "Usuario obtenido correctamente."
    }


@router.patch("/usuarios/{usuario_id}/estado", response_model=dict)
def cambiar_estado(
    usuario_id: UUID,
    body: CambiarEstadoRequest,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Activar o desactivar cuenta de usuario."""
    logger.info("[ADMIN] Admin %s (%s) cambió estado de usuario %s a %s", admin.id, admin.email, usuario_id, body.is_active)
    user = admin_service.cambiar_estado_usuario(db, usuario_id, is_active=body.is_active, admin_id=admin.id)
    
    user_data = UsuarioAdminResponse.model_validate(user)
    user_data.paso_onboarding_actual = _map_onboarding_step(user)
    
    return {
        "success": True,
        "data": user_data.model_dump(),
        "message": "Estado del usuario actualizado correctamente."
    }


@router.post("/usuarios/{usuario_id}/reset-password", response_model=dict)
def reset_password(
    usuario_id: UUID,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Envía email de recuperación de contraseña."""
    logger.info("[ADMIN] Admin %s (%s) solicitó reset de contraseña para usuario %s", admin.id, admin.email, usuario_id)
    admin_service.enviar_reset_password(db, usuario_id, settings.FRONTEND_URL)
    return {
        "success": True,
        "message": "Email de restablecimiento de contraseña enviado correctamente."
    }


@router.post("/usuarios/{usuario_id}/revocar-sesiones", response_model=dict)
def revocar_sesiones_usuario(
    usuario_id: UUID,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Fuerza cierre de todas las sesiones de un usuario."""
    logger.info("[ADMIN] Admin %s (%s) revocó sesiones del usuario %s", admin.id, admin.email, usuario_id)
    admin_service.revocar_sesiones(db, usuario_id, admin_id=admin.id)
    return {
        "success": True,
        "message": "Sesiones del usuario revocadas exitosamente."
    }


@router.post("/usuarios/{usuario_id}/desconectar-wpp", response_model=dict)
def desconectar_whatsapp(
    usuario_id: UUID,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Desconecta WhatsApp de un usuario."""
    logger.info("[ADMIN] Admin %s (%s) desconectó WhatsApp para usuario %s", admin.id, admin.email, usuario_id)
    user = admin_service.desconectar_wpp(db, usuario_id)
    
    user_data = UsuarioAdminResponse.model_validate(user)
    user_data.paso_onboarding_actual = _map_onboarding_step(user)
    
    return {
        "success": True,
        "data": user_data.model_dump(),
        "message": "WhatsApp desconectado correctamente."
    }


@router.post("/usuarios/{usuario_id}/resetear-onboarding", response_model=dict)
def resetear_onboarding_usuario(
    usuario_id: UUID,
    body: ResetearOnboardingRequest,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Resetea estado de onboarding del usuario."""
    logger.info("[ADMIN] Admin %s (%s) reseteó onboarding para usuario %s", admin.id, admin.email, usuario_id)
    
    if not body.confirmar:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "success": False,
                "error": {
                    "code": "CONFIRMATION_REQUIRED",
                    "message": "Debe confirmar explícitamente la acción de reseteo de onboarding.",
                },
            },
        )
        
    user = admin_service.resetear_onboarding(db, usuario_id)
    
    user_data = UsuarioAdminResponse.model_validate(user)
    user_data.paso_onboarding_actual = _map_onboarding_step(user)
    
    return {
        "success": True,
        "data": user_data.model_dump(),
        "message": "Onboarding reseteado correctamente."
    }


@router.get("/stats", response_model=dict)
def get_stats(
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Obtiene estadísticas generales para el panel de administración."""
    logger.info("[ADMIN] Admin %s (%s) solicitó estadísticas generales.", admin.id, admin.email)
    stats = admin_service.obtener_estadisticas(db)
    return {
        "success": True,
        "data": stats,
        "message": "Estadísticas obtenidas correctamente."
    }


@router.post("/feriados/refresh", response_model=dict)
async def refresh_feriados_admin(
    anio: int | None = None,
    admin: Usuario = Depends(get_current_admin_user),
):
    """Fuerza la recarga de feriados de un año desde la API externa y actualiza BD y caché."""
    from app.services.dias_habiles_service import recargar_feriados_anio
    from app.utils.fecha import hoy_argentina

    target_anio = anio or hoy_argentina().year
    logger.info("[ADMIN] Admin %s (%s) solicitó refresh de feriados para año %s", admin.id, admin.email, target_anio)

    res = await recargar_feriados_anio(target_anio)
    return {
        "success": res["success"],
        "data": res,
        "message": f"Feriados para el año {target_anio} actualizados. Se encontraron {res['cantidad']} feriados."
        if res["success"]
        else f"Error al actualizar feriados: {res['error']}",
    }

