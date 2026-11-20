# -*- coding: utf-8 -*-
"""
Reautenticación obligatoria del administrador antes de acciones delicadas
(dar o quitar rol admin y eliminar cuenta).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import normalizar_email, verify_password
from app.models.usuario import Usuario
from app.services import admin_registro_service, auth_service

TEXTO_REQUERIDA = "Para hacer esto, confirmá que sos vos."
TEXTO_CLAVE_MAL = "La contraseña no es correcta."
TEXTO_GOOGLE_MAL = "Elegí la misma cuenta de Google con la que entrás."
TEXTO_BLOQUEO = "Probaste muchas veces. Esperá 15 minutos y probá de nuevo."

MAX_FALLAS = 5
VENTANA = timedelta(minutes=15)
ANTIGUEDAD_GOOGLE = timedelta(minutes=5)


def usa_contrasena(admin: Usuario) -> bool:
    """Indica si el admin actual tiene contraseña configurada en Argentum."""
    return bool(admin.password_configurada and admin.password_hash)


def _normalizar_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def verificar_identidad_admin(
    db: Session,
    admin: Usuario,
    password_actual: str | None,
    google_credential: str | None,
    ahora: datetime,
) -> None:
    """
    Exige que el administrador vuelva a demostrar su identidad antes de una acción delicada.
    Nunca registra la contraseña ni el token en logs.
    """
    ahora_utc = _normalizar_utc(ahora)

    if admin_registro_service.fallas_recientes(db, admin.id, ahora_utc) >= MAX_FALLAS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "success": False,
                "error": {
                    "code": "REAUTH_BLOQUEADA",
                    "message": TEXTO_BLOQUEO,
                },
            },
        )

    if usa_contrasena(admin):
        if not password_actual:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "success": False,
                    "error": {
                        "code": "REAUTH_REQUERIDA",
                        "message": TEXTO_REQUERIDA,
                    },
                },
            )
        if not verify_password(password_actual, admin.password_hash or ""):
            admin_registro_service.registrar_accion(
                db, admin, "verificacion_fallida", detalle="Contraseña"
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "success": False,
                    "error": {
                        "code": "REAUTH_INVALIDA",
                        "message": TEXTO_CLAVE_MAL,
                    },
                },
            )
        return None

    if not google_credential:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "REAUTH_REQUERIDA",
                    "message": TEXTO_REQUERIDA,
                },
            },
        )

    valido = False
    try:
        datos = auth_service.verify_google_token(google_credential)
        email_token = normalizar_email(datos.get("email"))
        email_admin = normalizar_email(admin.email)
        email_verificado = datos.get("email_verified") is True
        iat_raw = datos.get("iat")
        iat_valido = False
        if isinstance(iat_raw, (int, float)):
            iat_dt = datetime.fromtimestamp(iat_raw, tz=timezone.utc)
            if iat_dt >= ahora_utc - ANTIGUEDAD_GOOGLE:
                iat_valido = True
        valido = bool(
            email_admin
            and email_token == email_admin
            and email_verificado
            and iat_valido
        )
    except Exception:
        valido = False

    if not valido:
        admin_registro_service.registrar_accion(
            db, admin, "verificacion_fallida", detalle="Google"
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "success": False,
                "error": {
                    "code": "REAUTH_INVALIDA",
                    "message": TEXTO_GOOGLE_MAL,
                },
            },
        )

    return None
