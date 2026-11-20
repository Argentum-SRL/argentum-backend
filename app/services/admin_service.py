from __future__ import annotations
import secrets
import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import case, select, or_, func, update

from app.models.usuario import Usuario, EstadoUsuario, AuthProvider, RolUsuario
from app.models.refresh_token import RefreshToken
from app.services.email_service import enviar_reset_password_email
from app.services import admin_uso_service, costos_service, usuario_service
from app.core.config import settings
from app.utils.fecha import hoy_argentina


def listar_usuarios(
    db: Session,
    page: int = 1,
    limit: int = 20,
    search: str | None = None,
    estado: str | None = None,
    orden: str = "ultimo_uso",
    hoy: date | None = None,
    ahora: datetime | None = None,
) -> dict:
    page = max(1, page)
    limit = max(1, min(limit, 100))
    offset = (page - 1) * limit
    hoy_ref = hoy if hoy is not None else hoy_argentina()
    ahora_ref = ahora if ahora is not None else datetime.now(timezone.utc)
    if ahora_ref.tzinfo is None:
        ahora_ref = ahora_ref.replace(tzinfo=timezone.utc)

    search_conditions = []

    # 1. Búsqueda por texto (nombre, apellido, nombre completo, email, teléfono)
    if search and search.strip():
        sanitized_search = search.strip()[:150]
        search_term = f"%{sanitized_search}%"
        search_conditions.append(
            or_(
                Usuario.nombre.ilike(search_term),
                Usuario.apellido.ilike(search_term),
                func.concat(func.coalesce(Usuario.nombre, ""), " ", func.coalesce(Usuario.apellido, "")).ilike(search_term),
                Usuario.email.ilike(search_term),
                Usuario.telefono.ilike(search_term),
                Usuario.telefono_normalizado.ilike(search_term),
            )
        )

    # 2. Conteos por estado_visible sobre los usuarios que coinciden con la búsqueda
    subq_web_est, subq_wpp_est, expr_estado = admin_uso_service.expresion_estado_visible(ahora_ref)
    conteos_rows = db.execute(
        select(expr_estado, func.count(Usuario.id))
        .select_from(Usuario)
        .outerjoin(subq_web_est, subq_web_est.c.usuario_id == Usuario.id)
        .outerjoin(subq_wpp_est, subq_wpp_est.c.usuario_id == Usuario.id)
        .where(*search_conditions)
        .group_by(expr_estado)
    ).all()
    conteos_estado: dict[str, int] = {
        "todos": 0,
        **{k: 0 for k in admin_uso_service.ESTADOS_VISIBLES},
    }
    for est_val, cnt in conteos_rows:
        cnt_int = int(cnt or 0)
        conteos_estado["todos"] += cnt_int
        if est_val in conteos_estado:
            conteos_estado[est_val] = cnt_int

    # 3. Filtro por estado visible y total
    stmt = select(Usuario).where(*search_conditions)
    if estado:
        stmt = (
            stmt.outerjoin(subq_web_est, subq_web_est.c.usuario_id == Usuario.id)
            .outerjoin(subq_wpp_est, subq_wpp_est.c.usuario_id == Usuario.id)
            .where(expr_estado == estado)
        )
        total = conteos_estado.get(estado, 0)
    else:
        total = conteos_estado["todos"]

    # 4. Ordenamiento
    if orden == "costo_mes":
        venta, _ = costos_service.dolar_oficial_venta(db, hoy_ref)
        subq_ia, subq_wpp_c, expr_costo = costos_service.expresion_costo_mes_ars(hoy_ref, venta)
        stmt = (
            stmt.outerjoin(subq_ia, subq_ia.c.usuario_id == Usuario.id)
            .outerjoin(subq_wpp_c, subq_wpp_c.c.usuario_id == Usuario.id)
            .order_by(expr_costo.desc(), Usuario.fecha_registro.desc())
        )
    elif orden == "movimientos":
        subq_mov, expr_mov = admin_uso_service.expresion_movimientos_mes(hoy_ref)
        stmt = stmt.outerjoin(subq_mov, subq_mov.c.usuario_id == Usuario.id).order_by(
            expr_mov.desc(),
            Usuario.fecha_registro.desc(),
        )
    elif orden == "registro":
        stmt = stmt.order_by(Usuario.fecha_registro.desc())
    else:
        subq_web, subq_wpp, expr_ultimo = admin_uso_service.expresiones_ultimo_uso()
        stmt = (
            stmt.outerjoin(subq_web, subq_web.c.usuario_id == Usuario.id)
            .outerjoin(subq_wpp, subq_wpp.c.usuario_id == Usuario.id)
            .order_by(
                case((expr_ultimo.is_(None), 1), else_=0).asc(),
                expr_ultimo.desc(),
                Usuario.fecha_registro.desc(),
            )
        )

    usuarios = list(db.scalars(stmt.offset(offset).limit(limit)).all())
    uids = [u.id for u in usuarios]
    usos = admin_uso_service.ultimos_usos(db, uids)
    costos = costos_service.costo_mes_por_usuario(db, hoy_ref, usuario_ids=uids)
    movimientos = admin_uso_service.movimientos_mes(db, uids, hoy_ref)
    estados_visibles = {
        u.id: admin_uso_service.estado_visible(u, usos[u.id]["ultimo_uso"], ahora_ref)
        for u in usuarios
    }

    pages = (total + limit - 1) // limit if total > 0 else 0

    return {
        "total": total,
        "page": page,
        "limit": limit,
        "pages": pages,
        "conteos_estado": conteos_estado,
        "usuarios": usuarios,
        "usos": usos,
        "costos": costos,
        "estados_visibles": estados_visibles,
        "movimientos": movimientos,
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
                    "message": "No se encontró la cuenta.",
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


def enviar_reset_password(db: Session, usuario_id: UUID, frontend_url: str) -> Usuario:
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
                    "message": "No se pudo enviar el email. Probá de nuevo.",
                },
            },
        )

    return user


def revocar_sesiones(db: Session, usuario_id: UUID, admin_id: UUID) -> Usuario:
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
    return user


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


def validar_eliminacion_admin(
    db: Session, usuario_id: UUID, email_confirmacion: str, admin_id: UUID
) -> Usuario:
    """Valida auto-eliminación, existencia y coincidencia de email antes de verificar identidad."""
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
    return user


def eliminar_usuario_admin(
    db: Session, usuario_id: UUID, email_confirmacion: str, admin_id: UUID, commit: bool = True
) -> dict:
    user = validar_eliminacion_admin(db, usuario_id, email_confirmacion, admin_id)
    return usuario_service.eliminar_usuario(db, user, commit=commit)


def obtener_estadisticas(
    db: Session,
    ahora: datetime | None = None,
    hoy: date | None = None,
) -> dict:
    ahora_ref = ahora if ahora is not None else datetime.now(timezone.utc)
    if ahora_ref.tzinfo is None:
        ahora_ref = ahora_ref.replace(tzinfo=timezone.utc)
    hoy_ref = hoy if hoy is not None else hoy_argentina()

    total = db.scalar(select(func.count(Usuario.id))) or 0
    activos = db.scalar(select(func.count(Usuario.id)).where(Usuario.estado == EstadoUsuario.ACTIVO)) or 0
    onboarding_completo = db.scalar(select(func.count(Usuario.id)).where(Usuario.onboarding_completo == True)) or 0
    wpp_vinculados = db.scalar(select(func.count(Usuario.id)).where(Usuario.telefono_verificado == True)) or 0

    # Grupo 1: Actividad reciente
    inicio_hoy_utc = ahora_ref.replace(hour=0, minute=0, second=0, microsecond=0)
    hace_7_dias = ahora_ref - timedelta(days=7)

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

    subq_web, subq_wpp, expr_ultimo = admin_uso_service.expresiones_ultimo_uso()
    usaron_7_dias = int(
        db.scalar(
            select(func.count(Usuario.id))
            .select_from(Usuario)
            .outerjoin(subq_web, subq_web.c.usuario_id == Usuario.id)
            .outerjoin(subq_wpp, subq_wpp.c.usuario_id == Usuario.id)
            .where(expr_ultimo.is_not(None), expr_ultimo >= hace_7_dias)
        )
        or 0
    )

    costo_mes_ars, costo_mes_sin_dolar, costo_mes_fuente = costos_service.costo_mes_total_con_fuente(
        db, hoy_ref, ahora_ref
    )

    subq_web_est, subq_wpp_est, expr_estado = admin_uso_service.expresion_estado_visible(ahora_ref)
    en_desuso = int(
        db.scalar(
            select(func.count(Usuario.id))
            .select_from(Usuario)
            .outerjoin(subq_web_est, subq_web_est.c.usuario_id == Usuario.id)
            .outerjoin(subq_wpp_est, subq_wpp_est.c.usuario_id == Usuario.id)
            .where(expr_estado == "en_desuso")
        )
        or 0
    )
    if total > 0:
        activos_pct = int(
            (Decimal(usaron_7_dias) * Decimal("100") / Decimal(total)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    else:
        activos_pct = 0

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
        "usaron_7_dias": usaron_7_dias,
        "en_desuso": en_desuso,
        "activos_pct": activos_pct,
        "costo_mes_ars": costo_mes_ars,
        "costo_mes_sin_dolar": costo_mes_sin_dolar,
        "costo_mes_fuente": costo_mes_fuente,
        "admins_total": admins_total,
        "por_proveedor": por_proveedor,
    }
