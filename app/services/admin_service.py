from __future__ import annotations
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select, or_, func, update

from app.models.usuario import Usuario, EstadoUsuario, AuthProvider, RolUsuario
from app.models.refresh_token import RefreshToken
from app.services.email_service import enviar_reset_password_email
from app.services import usuario_service
from app.core.config import settings


def listar_usuarios(
    db: Session,
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    estado: str | None = None,
    onboarding: str | None = None,
    wpp: str | None = None,
) -> dict:
    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit

    conditions = []

    # 1. Búsqueda por texto (nombre, apellido, nombre completo, email, teléfono)
    if search and search.strip():
        sanitized_search = search.strip()[:150]
        search_term = f"%{sanitized_search}%"
        conditions.append(
            or_(
                Usuario.nombre.ilike(search_term),
                Usuario.apellido.ilike(search_term),
                func.concat(func.coalesce(Usuario.nombre, ""), " ", func.coalesce(Usuario.apellido, "")).ilike(search_term),
                Usuario.email.ilike(search_term),
                Usuario.telefono.ilike(search_term),
                Usuario.telefono_normalizado.ilike(search_term),
            )
        )

    # 2. Filtro por estado
    if estado:
        if estado == "activo":
            conditions.append(Usuario.estado == EstadoUsuario.ACTIVO)
        elif estado == "inactivo":
            conditions.append(Usuario.estado == EstadoUsuario.INACTIVO)
        elif estado == "bloqueado":
            conditions.append(Usuario.estado == EstadoUsuario.PENDIENTE_VERIFICACION)

    # 3. Filtro por onboarding
    if onboarding:
        if onboarding == "completo":
            conditions.append(Usuario.onboarding_completo == True)
        elif onboarding == "incompleto":
            conditions.append(Usuario.onboarding_completo == False)

    # 4. Filtro por WhatsApp (telefono_verificado)
    if wpp:
        if wpp == "vinculado":
            conditions.append(Usuario.telefono_verificado == True)
        elif wpp == "no_vinculado":
            conditions.append(Usuario.telefono_verificado == False)

    # Construir queries
    base_query = select(Usuario).where(*conditions)
    
    # Contar total
    total = db.scalar(select(func.count()).select_from(base_query.subquery()))

    # Ejecutar consulta paginada ordenada por fecha de registro descendente
    stmt = base_query.order_by(Usuario.fecha_registro.desc()).offset(offset).limit(limit)
    usuarios = db.scalars(stmt).all()

    pages = (total + limit - 1) // limit if total > 0 else 0

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "usuarios": usuarios,
    }


def obtener_usuario(db: Session, usuario_id: UUID) -> Usuario:
    user = db.execute(select(Usuario).where(Usuario.id == usuario_id)).scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "USER_NOT_FOUND",
                    "message": "No encontramos al usuario.",
                },
            },
        )
    return user


def cambiar_estado_usuario(
    db: Session, usuario_id: UUID, is_active: bool, admin_id: UUID
) -> Usuario:
    user = obtener_usuario(db, usuario_id)

    if usuario_id == admin_id and not is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "ADMIN_CANNOT_DEACTIVATE_SELF",
                    "message": "No podés suspender tu propia cuenta.",
                },
            },
        )

    user.is_active = is_active
    if not is_active:
        user.tokens_revocados_at = datetime.now(timezone.utc)
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.usuario_id == usuario_id, RefreshToken.revocado == False)
            .values(revocado=True)
        )
    db.commit()
    db.refresh(user)
    return user


def enviar_reset_password(db: Session, usuario_id: UUID, frontend_url: str) -> None:
    user = obtener_usuario(db, usuario_id)

    if user.auth_provider == AuthProvider.GOOGLE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "USER_IS_GOOGLE_AUTH",
                    "message": "Este usuario utiliza Google OAuth como método de autenticación y no requiere contraseña.",
                },
            },
        )

    if not user.email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "USER_HAS_NO_EMAIL",
                    "message": "Este usuario no tiene email registrado.",
                },
            },
        )

    token_raw = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token_raw.encode("utf-8")).hexdigest()

    user.reset_token_hash = token_hash
    user.reset_token_expira_at = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    base_frontend_url = frontend_url.rstrip("/") if frontend_url else settings.FRONTEND_URL.rstrip("/")
    reset_url = f"{base_frontend_url}/reset-password?token={token_raw}"

    enviado = enviar_reset_password_email(user.email, user.nombre or "Usuario", reset_url)

    if not enviado:
        # Rollback el token generado
        user.reset_token_hash = None
        user.reset_token_expira_at = None
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "success": False,
                "error": {
                    "code": "EMAIL_SEND_FAILED",
                    "message": "No pudimos enviar el email. Intentá de nuevo.",
                },
            },
        )


def revocar_sesiones(db: Session, usuario_id: UUID, admin_id: UUID) -> None:
    user = obtener_usuario(db, usuario_id)

    if usuario_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "ADMIN_CANNOT_REVOKE_OWN_SESSIONS",
                    "message": "Para cerrar tu sesión usá el logout normal.",
                },
            },
        )

    user.tokens_revocados_at = datetime.now(timezone.utc)
    
    # Marcar como revocados todos sus refresh tokens
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.usuario_id == usuario_id, RefreshToken.revocado == False)
        .values(revocado=True)
    )
    db.commit()


def desconectar_wpp(db: Session, usuario_id: UUID) -> Usuario:
    user = obtener_usuario(db, usuario_id)

    if not user.telefono_verificado:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "WPP_NOT_CONNECTED",
                    "message": "Este usuario no tiene WhatsApp vinculado.",
                },
            },
        )

    user.telefono_verificado = False
    db.commit()
    db.refresh(user)
    return user


def resetear_onboarding(db: Session, usuario_id: UUID) -> Usuario:
    user = obtener_usuario(db, usuario_id)

    if not user.onboarding_completo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "ONBOARDING_ALREADY_INCOMPLETE",
                    "message": "El onboarding de este usuario ya está incompleto.",
                },
            },
        )

    user.onboarding_completo = False
    db.commit()
    db.refresh(user)
    return user


def cambiar_rol_admin(
    db: Session, usuario_id: UUID, is_admin: bool, admin_id: UUID
) -> Usuario:
    user = obtener_usuario(db, usuario_id)

    if usuario_id == admin_id and not is_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "ADMIN_CANNOT_DEMOTE_SELF",
                    "message": "No podés revocar tus propios permisos de administrador.",
                },
            },
        )

    user.is_admin = is_admin
    user.rol = RolUsuario.ADMIN if is_admin else RolUsuario.USUARIO
    db.commit()
    db.refresh(user)
    return user


def eliminar_usuario_admin(
    db: Session, usuario_id: UUID, email_confirmacion: str, admin_id: UUID
) -> dict:
    if usuario_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "ADMIN_CANNOT_DELETE_SELF",
                    "message": "No podés eliminar tu propia cuenta desde el panel de administración.",
                },
            },
        )

    user = obtener_usuario(db, usuario_id)

    target_identifier = (user.email or user.telefono or "").strip().lower()
    provided_identifier = (email_confirmacion or "").strip().lower()

    if not provided_identifier or provided_identifier != target_identifier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "EMAIL_MISMATCH",
                    "message": "El email ingresado no coincide con el de la cuenta a eliminar.",
                },
            },
        )

    return usuario_service.eliminar_usuario(db, user)


def obtener_estadisticas(db: Session) -> dict:
    total = db.scalar(select(func.count(Usuario.id))) or 0
    activos = db.scalar(select(func.count(Usuario.id)).where(Usuario.estado == EstadoUsuario.ACTIVO)) or 0
    onboarding_completo = db.scalar(select(func.count(Usuario.id)).where(Usuario.onboarding_completo == True)) or 0
    wpp_vinculados = db.scalar(select(func.count(Usuario.id)).where(Usuario.telefono_verificado == True)) or 0

    # Grupo 1: Actividad reciente
    inicio_hoy_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    hace_7_dias = datetime.now(timezone.utc) - timedelta(days=7)

    nuevos_hoy = db.scalar(
        select(func.count(Usuario.id)).where(Usuario.fecha_registro >= inicio_hoy_utc)
    ) or 0
    nuevos_7_dias = db.scalar(
        select(func.count(Usuario.id)).where(Usuario.fecha_registro >= hace_7_dias)
    ) or 0
    activos_7_dias = db.scalar(
        select(func.count(Usuario.id)).where(
            Usuario.ultimo_acceso.isnot(None),
            Usuario.ultimo_acceso >= hace_7_dias
        )
    ) or 0
    admins_total = db.scalar(
        select(func.count(Usuario.id)).where(Usuario.is_admin == True)
    ) or 0

    # Grupo 2: Desglose por proveedor de registro
    por_proveedor = {
        "EMAIL": 0,
        "GOOGLE": 0,
        "TELEFONO": 0
    }
    stmt_prov = select(Usuario.auth_provider, func.count(Usuario.id)).group_by(Usuario.auth_provider)
    results = db.execute(stmt_prov).all()
    for prov, count in results:
        if prov:
            if isinstance(prov, AuthProvider):
                key = prov.name
            else:
                try:
                    key = AuthProvider(prov).name
                except ValueError:
                    key = str(prov).upper()
            por_proveedor[key] = count

    return {
        "total": total,
        "activos": activos,
        "onboarding_completo": onboarding_completo,
        "whatsapp_vinculados": wpp_vinculados,
        "nuevos_hoy": nuevos_hoy,
        "nuevos_7_dias": nuevos_7_dias,
        "activos_7_dias": activos_7_dias,
        "admins_total": admins_total,
        "por_proveedor": por_proveedor,
    }
