"""
app/routers/auth.py — Endpoints de autenticación para Argentum.

RESPUESTA ESTÁNDAR (AuthResponse) para todos los endpoints de auth:
{
    "access_token":                string | null,
    "refresh_token":               string | null,
    "token_type":                  "bearer",
    "usuario":                     UsuarioRead | null,
    "requiere_telefono":           bool,   # Google: necesita agregar teléfono
    "requiere_datos":              bool,   # Teléfono: necesita nombre/apellido/email/password
    "requiere_verificacion_email": bool,   # Falta verificar email
    "requiere_verificacion_telefono": bool, # Falta verificar teléfono
    "requiere_onboarding":         bool,   # onboarding_completo=False
}

FLUJOS:
  1. Google → tokens inmediatos + requiere_telefono si no tiene teléfono verificado
  2. Teléfono → WhatsApp → si es usuario nuevo: requiere_datos → completar-perfil → verificar-email
  3. Email/password → registro → verificar-email → verificar-teléfono → tokens
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, BackgroundTasks, Cookie
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.limiter import limiter

from app.core.auth import (
    crear_access_token,
    crear_refresh_token,
    get_current_user,
    get_optional_user,
    renovar_tokens,
    revocar_refresh_token,
    revocar_todos_los_tokens,
    setear_cookies_auth,
    limpiar_cookies_auth,
)
from app.core.database import get_db
from app.core.security import get_password_hash, verify_password
from app.models.usuario import AuthProvider, EstadoUsuario, Usuario
from app.utils.telefono import normalizar_telefono_ar
from urllib.parse import quote
from app.schemas.auth import (
    AuthResponse,
    CodigoVinculacionResponse,
    CompletarPerfilRequest,
    EnviarCodigoEmailRequest,
    GoogleLoginRequest,
    LoginRequest,
    RecuperarPasswordRequest,
    RegisterRequest,
    TokenResponse,
    VerificarCodigoEmailRequest,
    VerificarRecuperacionRequest,
    ConfirmarResetPasswordRequest,
)
from app.schemas.usuario import UsuarioRead
from app.services.auth_service import (
    verify_google_token,
    validar_reset_token,
    confirmar_reset_password,
)
from app.services.email_service import (
    enviar_email_recuperacion,
    enviar_email_aviso_google,
    generar_codigo_recuperacion,
    generar_y_enviar_verificacion_email,
    guardar_codigo_recuperacion,
    verificar_codigo_email,
    verificar_codigo_recuperacion,
)
from app.services import usuario_service
from app.services import whatsapp_service
from app.services.whatsapp_service import (
    enviar_whatsapp,
    enviar_mensaje_whatsapp,
    generar_codigo_vinculacion,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_envios_otp_telefono: dict[str, list[float]] = {}
MAX_OTP_POR_TELEFONO = 3
VENTANA_OTP_SEGUNDOS = 10 * 60  # 10 minutos


def _verificar_rate_limit_otp_telefono(telefono: str) -> bool:
    ahora = time.time()
    limite_tiempo = ahora - VENTANA_OTP_SEGUNDOS
    tel_key = normalizar_telefono_ar(telefono) or telefono.strip()

    timestamps = _envios_otp_telefono.get(tel_key, [])
    timestamps = [t for t in timestamps if t > limite_tiempo]

    if len(timestamps) >= MAX_OTP_POR_TELEFONO:
        _envios_otp_telefono[tel_key] = timestamps
        return False

    timestamps.append(ahora)
    _envios_otp_telefono[tel_key] = timestamps

    # Purgar periódicamente si el diccionario crece demasiado
    if len(_envios_otp_telefono) > 2000:
        for k in list(_envios_otp_telefono.keys()):
            filtrados = [t for t in _envios_otp_telefono[k] if t > limite_tiempo]
            if not filtrados:
                del _envios_otp_telefono[k]
            else:
                _envios_otp_telefono[k] = filtrados

    return True


def _device_info(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:200] if ua else None


def _tokens(
    user_or_id: Usuario | UUID | str,
    request: Request,
    db: Session,
    hacer_commit: bool = True,
) -> tuple[str, str]:
    if isinstance(user_or_id, Usuario):
        usuario_id = user_or_id.id
        is_admin = bool(user_or_id.is_admin)
    else:
        usuario_id = user_or_id
        user = db.execute(select(Usuario).where(Usuario.id == usuario_id)).scalar_one_or_none()
        is_admin = user.is_admin if user else False

    return (
        crear_access_token(usuario_id, is_admin=is_admin),
        crear_refresh_token(
            usuario_id,
            db,
            device_info=_device_info(request),
            hacer_commit=hacer_commit,
        ),
    )


def _requiere_onboarding(user: Usuario) -> bool:
    return not user.onboarding_completo


# ---------------------------------------------------------------------------
# Email / Password
# ---------------------------------------------------------------------------

@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
def register(request: Request, user_in: RegisterRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Registra un usuario con email/password.
    No devuelve tokens: primero debe verificar email y luego teléfono.
    """
    email_clean = user_in.email.strip().lower()
    tel_norm = normalizar_telefono_ar(user_in.telefono) if user_in.telefono else None

    email_existente = db.execute(
        select(Usuario).where(Usuario.email.ilike(email_clean))
    ).scalar_one_or_none()
    if email_existente:
        if email_existente.auth_provider == AuthProvider.GOOGLE:
            raise HTTPException(
                status_code=400,
                detail="Este email ya está registrado con Google. Usá el botón de Google para iniciar sesión.",
            )
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese email.")

    condicion_tel = (Usuario.telefono == user_in.telefono)
    if tel_norm:
        condicion_tel = condicion_tel | (Usuario.telefono_normalizado == tel_norm)

    if db.execute(select(Usuario).where(condicion_tel)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ese número de teléfono ya está registrado.")

    nuevo = Usuario(
        nombre=user_in.nombre.strip(),
        apellido=user_in.apellido.strip(),
        email=email_clean,
        telefono=user_in.telefono.strip(),
        telefono_normalizado=tel_norm,
        password_hash=get_password_hash(user_in.password),
        password_configurada=True, # Ya la puso en el registro
        auth_provider=AuthProvider.EMAIL,
        estado=EstadoUsuario.PENDIENTE_VERIFICACION,
        email_verificado=False,
        telefono_verificado=False,
        onboarding_completo=False,
        moneda_principal="ARS",
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)

    # Crear billeteras efectivo default
    usuario_service.crear_billeteras_efectivo_default(db, nuevo.id)

    # Enviar email en segundo plano para no bloquear el registro
    background_tasks.add_task(generar_y_enviar_verificacion_email, nuevo.email, nombre=nuevo.nombre)

    return AuthResponse(
        usuario=UsuarioRead.model_validate(nuevo),
        requiere_verificacion_email=True,
        requiere_verificacion_telefono=False,
    )


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
def login(
    request: Request,
    user_in: LoginRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Login con email y password. Requiere email verificado y contraseña configurada."""
    email_clean = user_in.email.strip().lower()
    user = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="El email o la contraseña no son correctos. Revisalos e intentá de nuevo.")

    if not user.email_verificado:
        raise HTTPException(status_code=401, detail="Todavía no verificaste tu cuenta. Revisá tu email para activarla.")

    if not user.password_hash:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta no tiene contraseña configurada. Ingresá con tu teléfono o configurá una contraseña desde tu perfil.",
        )

    if not verify_password(user_in.password, user.password_hash):
        user.intentos_fallidos_login = (user.intentos_fallidos_login or 0) + 1
        user.ultimo_intento_fallido_at = datetime.now(timezone.utc)
        db.commit()

        if user.intentos_fallidos_login == 3 and user.email:
            try:
                from app.utils.fecha import ahora_argentina
                from app.services.notificacion_email_service import (
                    enviar_email_notificacion,
                    generar_email_intentos_login,
                )
                fecha_str = ahora_argentina().strftime("%d/%m/%Y a las %H:%M")
                link_recupero = f"{settings.FRONTEND_URL}/auth/recuperar-password"
                asunto, html, texto = generar_email_intentos_login(
                    usuario_nombre=user.nombre or "Usuario",
                    cantidad_intentos=3,
                    fecha_hora_argentina=fecha_str,
                    link_recupero=link_recupero,
                )
                background_tasks.add_task(enviar_email_notificacion, user.email, asunto, html, texto)
            except Exception as e:
                logger.error("Error al disparar alerta de intentos fallidos de login: %s", e)

        raise HTTPException(status_code=401, detail="El email o la contraseña no son correctos. Revisalos e intentá de nuevo.")

    user.intentos_fallidos_login = 0
    user.ultimo_acceso = datetime.now(timezone.utc)

    # Asegurar que tenga las billeteras de efectivo default
    usuario_service.crear_billeteras_efectivo_default(db, user.id)

    access, refresh = _tokens(user, request, db, hacer_commit=False)
    db.commit()
    setear_cookies_auth(response, access, refresh, settings)
    return AuthResponse(
        access_token=access,
        usuario=UsuarioRead.model_validate(user),
        requiere_onboarding=_requiere_onboarding(user),
    )


@router.post("/recuperar-password")
@limiter.limit("5/minute")
def recuperar_password(
    request: Request,
    body: RecuperarPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Inicia recuperación de contraseña. No revela si el email existe."""
    email_clean = body.email.strip().lower()
    user = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()
    if user and user.email_verificado:
        codigo = generar_codigo_recuperacion()
        guardar_codigo_recuperacion(email_clean, codigo)
        if user.auth_provider == AuthProvider.GOOGLE:
            background_tasks.add_task(enviar_email_aviso_google, email_clean, codigo)
        elif user.auth_provider in (AuthProvider.EMAIL, AuthProvider.TELEFONO):
            background_tasks.add_task(enviar_email_recuperacion, email_clean, codigo)
    return {"detail": "Si el email existe, te enviamos un código de recuperación."}


@router.post("/recuperar-password/verificar")
@limiter.limit("5/minute")
def verificar_recuperacion(
    request: Request,
    body: VerificarRecuperacionRequest,
    db: Session = Depends(get_db)
):
    """Verifica el código de recuperación y actualiza la contraseña con revocación de sesiones previas."""
    email_clean = body.email.strip().lower()
    if not verificar_codigo_recuperacion(email_clean, body.codigo):
        raise HTTPException(status_code=400, detail="El código que ingresaste no es válido. Revisalo o pedí uno nuevo.")

    user = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")

    if not user.email_verificado:
        raise HTTPException(status_code=400, detail="El email de la cuenta no está verificado.")

    pw = body.nueva_password
    if len(pw) < 8 or len(pw) > 128:
        raise HTTPException(status_code=400, detail="La contraseña debe tener entre 8 y 128 caracteres.")
    if not any(c.isupper() for c in pw) or not any(c.islower() for c in pw) or not any(c.isdigit() for c in pw):
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe incluir al menos una mayúscula, una minúscula y un número."
        )

    now = datetime.now(timezone.utc)
    user.password_hash = get_password_hash(pw)
    user.password_configurada = True
    user.tokens_revocados_at = now

    from app.models.refresh_token import RefreshToken
    from sqlalchemy import update
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.usuario_id == user.id)
        .values(revocado=True)
    )
    db.commit()

    try:
        from app.services.notificacion_service import crear_notificacion
        from app.models.notificacion import TipoNotificacion, NivelNotificacion
        crear_notificacion(
            db=db,
            usuario_id=user.id,
            tipo=TipoNotificacion.CAMBIO_CONTRASENA,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Tu contraseña fue actualizada. Si no fuiste vos, contactanos de inmediato.",
            canal_web=True,
            canal_whatsapp=True,
            canal_email=False,
        )
    except Exception:
        pass

    # Enviar email de notificación de cambio de contraseña
    try:
        from app.services.notificacion_email_service import (
            enviar_email_notificacion,
            generar_email_cambio_contrasena,
        )
        asunto, html, texto = generar_email_cambio_contrasena(
            usuario_nombre=user.nombre or "Usuario",
            dispositivo="Recuperación de contraseña"
        )
        enviar_email_notificacion(user.email, asunto, html, texto)
    except Exception as e:
        logger.error("Error al enviar email de cambio de contraseña en recuperación: %s", e)

    return {"detail": "Contraseña actualizada correctamente."}


@router.get("/reset-password/validar", response_model=dict)
def validar_token(token: str, db: Session = Depends(get_db)):
    """Verifica si el token de restablecimiento es válido."""
    nombre = validar_reset_token(db, token)
    return {"success": True, "data": {"nombre": nombre}}


@router.post("/reset-password/confirmar", response_model=dict)
def confirmar_token(
    body: ConfirmarResetPasswordRequest,
    db: Session = Depends(get_db)
):
    """Verifica el token, actualiza la contraseña y revoca las sesiones existentes."""
    usuario = confirmar_reset_password(db, body.token, body.nueva_password)

    try:
        from app.services.notificacion_service import crear_notificacion
        from app.models.notificacion import TipoNotificacion, NivelNotificacion
        crear_notificacion(
            db=db,
            usuario_id=usuario.id,
            tipo=TipoNotificacion.CAMBIO_CONTRASENA,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Tu contraseña fue actualizada. Si no fuiste vos, contactanos de inmediato.",
            canal_web=True,
            canal_whatsapp=True,
            canal_email=False,
        )
    except Exception:
        pass

    return {
        "success": True,
        "message": "Tu contraseña fue actualizada. Iniciá sesión con tu nueva contraseña.",
    }


# ---------------------------------------------------------------------------
# Verificación de email
# ---------------------------------------------------------------------------

@router.post("/email/enviar-codigo")
@limiter.limit("5/minute")
def enviar_codigo_email(request: Request, body: EnviarCodigoEmailRequest, db: Session = Depends(get_db)):
    """Reenvía el código de verificación de email."""
    email_clean = body.email.strip().lower()
    user = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")
    
    if user.email_verificado:
        raise HTTPException(status_code=400, detail="El email ya está verificado.")

    generar_y_enviar_verificacion_email(email_clean, nombre=user.nombre)
    
    return {"detail": "Código enviado a tu casilla de correo."}


@router.get("/email/verificar-link")
def verificar_email_link(email: str, codigo: str, db: Session = Depends(get_db)):
    """
    Verifica el email a través de un link (método GET).
    Si es exitoso, redirige al frontend a la confirmación o al siguiente paso de verificación.
    """
    from fastapi.responses import RedirectResponse
    import urllib.parse

    email_clean = email.strip().lower()
    ok, error = verificar_codigo_email(email_clean, codigo.strip())
    if not ok:
        error_msg = urllib.parse.quote(error or "Este enlace de verificación no es válido o expiró.")
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/verificar-email?email={email_clean}&error={error_msg}"
        )

    user = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()
    if not user:
        error_msg = urllib.parse.quote("No encontramos una cuenta con esos datos.")
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/verificar-email?email={email_clean}&error={error_msg}"
        )

    user.email_verificado = True

    # Si ya tiene el teléfono verificado o es usuario que vino de teléfono
    if user.telefono_verificado or user.auth_provider == AuthProvider.TELEFONO:
        user.estado = EstadoUsuario.ACTIVO
        db.commit()
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/verificar-email?email={user.email}&verificado=true"
        )

    db.commit()
    # Si falta verificar teléfono (caso registro normal por email):
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/verificar-telefono?telefono={user.telefono}&modoVerificacion=true"
    )


@router.post("/email/verificar", response_model=AuthResponse)
def verificar_email(
    body: VerificarCodigoEmailRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """
    Verifica el código enviado al email.

    - Provider EMAIL: marca email_verificado=True y pide verificación de teléfono.
    - Provider TELEFONO (viene de completar-perfil): marca email_verificado=True,
      activa la cuenta y devuelve tokens + requiere_onboarding.
    """
    email_clean = body.email.strip().lower()
    ok, error = verificar_codigo_email(email_clean, body.codigo.strip())
    if not ok:
        raise HTTPException(status_code=400, detail=error)

    user = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")

    user.email_verificado = True

    if user.auth_provider == AuthProvider.EMAIL and not user.telefono_verificado:
        db.commit()
        return AuthResponse(
            usuario=UsuarioRead.model_validate(user),
            requiere_verificacion_telefono=True,
        )

    # Usuario con teléfono ya verificado o provider TELEFONO: activar cuenta y emitir tokens
    user.estado = EstadoUsuario.ACTIVO
    db.commit()
    access, refresh = _tokens(user, request, db)
    setear_cookies_auth(response, access, refresh, settings)
    return AuthResponse(
        access_token=access,
        usuario=UsuarioRead.model_validate(user),
        requiere_onboarding=_requiere_onboarding(user),
    )


# ---------------------------------------------------------------------------
# Google OAuth2
# ---------------------------------------------------------------------------

@router.post("/google", response_model=AuthResponse)
def login_google(
    body: GoogleLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """Login / registro con Google ID token."""
    logger.debug(
        '[Auth][Google][Backend] /auth/google recibido origin=%s userAgent=%s tokenPresent=%s tokenLength=%s tokenPrefix=%s',
        request.headers.get('origin'),
        request.headers.get('user-agent'),
        bool(body.token),
        len(body.token),
        body.token[:12] + '...' if body.token else None,
    )

    token_info = verify_google_token(body.token)
    email = token_info.get("email")
    if not email:
        logger.warning('[Auth][Google][Backend] Token válido pero sin email')
        raise HTTPException(status_code=400, detail="El token de Google no contiene un email válido.")

    user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()

    logger.debug(
        '[Auth][Google][Backend] Usuario buscado email=%s exists=%s authProvider=%s',
        email,
        bool(user),
        getattr(user.auth_provider, 'value', None) if user else None,
    )

    if user:
        if not user.email_verificado:
            raise HTTPException(
                status_code=401,
                detail="Tu cuenta de Argentum aún no ha verificado el email. Verificalo para poder usar Google.",
            )
        
        # Si no tiene foto, actualizamos con la de Google
        if not user.foto_url:
            user.foto_url = token_info.get("picture")
            db.commit()
    else:
        # No existe: crear usuario nuevo
        nombre = token_info.get("given_name", "")
        apellido = token_info.get("family_name", "")
        if not nombre and "name" in token_info:
            partes = token_info["name"].split(" ", 1)
            nombre = partes[0]
            apellido = partes[1] if len(partes) > 1 else ""

        logger.info(
            '[Auth][Google][Backend] Creando usuario nuevo email=%s nombre=%s apellido=%s picture=%s',
            email,
            nombre,
            apellido,
            bool(token_info.get('picture')),
        )

        user = Usuario(
            nombre=nombre or None,
            apellido=apellido or None,
            email=email,
            telefono=None,
            telefono_normalizado=None,
            foto_url=token_info.get("picture"),
            auth_provider=AuthProvider.GOOGLE,
            estado=EstadoUsuario.ACTIVO,
            email_verificado=True,
            telefono_verificado=False,
            onboarding_completo=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # Asegurar que tenga las billeteras de efectivo default
    usuario_service.crear_billeteras_efectivo_default(db, user.id)

    # Emitir tokens siempre (ya sea login o registro)
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        '[Auth][Google][Backend] Tokens emitidos userId=%s email=%s requiereTelefono=%s requiereOnboarding=%s',
        user.id,
        user.email,
        not user.telefono_verificado,
        _requiere_onboarding(user) if user.telefono_verificado else False,
    )

    access, refresh = _tokens(user, request, db)
    setear_cookies_auth(response, access, refresh, settings)

    return AuthResponse(
        access_token=access,
        usuario=UsuarioRead.model_validate(user),
        requiere_telefono=False,
        requiere_onboarding=_requiere_onboarding(user),
    )


# ---------------------------------------------------------------------------
# Teléfono (WhatsApp) — Nuevo Flujo de Vinculación
# ---------------------------------------------------------------------------

_solicitudes_codigo_vinculacion: dict[str, list[float]] = {}
MAX_CODIGOS_VINCULACION_POR_HORA = 5
VENTANA_CODIGO_VINCULACION_SEGUNDOS = 60 * 60  # 1 hora


def _verificar_rate_limit_codigo_vinculacion(usuario_id: str) -> bool:
    ahora = time.time()
    limite = ahora - VENTANA_CODIGO_VINCULACION_SEGUNDOS
    timestamps = [t for t in _solicitudes_codigo_vinculacion.get(usuario_id, []) if t > limite]

    if len(timestamps) >= MAX_CODIGOS_VINCULACION_POR_HORA:
        _solicitudes_codigo_vinculacion[usuario_id] = timestamps
        return False

    timestamps.append(ahora)
    _solicitudes_codigo_vinculacion[usuario_id] = timestamps

    if len(_solicitudes_codigo_vinculacion) > 2000:
        for k in list(_solicitudes_codigo_vinculacion.keys()):
            filtrados = [t for t in _solicitudes_codigo_vinculacion[k] if t > limite]
            if not filtrados:
                del _solicitudes_codigo_vinculacion[k]
            else:
                _solicitudes_codigo_vinculacion[k] = filtrados

    return True


@router.post("/telefono/solicitar-vinculacion", response_model=CodigoVinculacionResponse)
def solicitar_codigo_vinculacion(
    current_user: Usuario = Depends(get_current_user),
):
    """
    Genera un código de 6 caracteres alfanuméricos en mayúscula para vincular WhatsApp.
    El usuario inicia la conversación enviando este código al bot de WhatsApp.
    Expira en 15 minutos. Máximo 5 códigos por usuario por hora.
    """
    user_key = str(current_user.id)
    if not _verificar_rate_limit_codigo_vinculacion(user_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Superaste el límite de solicitudes de vinculación (máximo 5 por hora). Por favor, esperá unos minutos.",
        )

    codigo, expiracion_ts = whatsapp_service.generar_codigo_vinculacion(current_user.id)
    numero_bot = getattr(settings, "WHATSAPP_BOT_NUMBER", None) or "5491100000000"
    texto_precargado = f"Hola, quiero vincular mi cuenta de Argentum. Codigo: {codigo}"
    link_whatsapp = f"https://wa.me/{numero_bot}?text={quote(texto_precargado)}"
    expiracion_dt = datetime.fromtimestamp(expiracion_ts, tz=timezone.utc)

    return CodigoVinculacionResponse(
        codigo=codigo,
        link_whatsapp=link_whatsapp,
        mensaje_precargado=texto_precargado,
        expiracion=expiracion_dt,
        expira_en_segundos=max(0, int(expiracion_ts - time.time())),
        telefono_bot=numero_bot,
    )



# ---------------------------------------------------------------------------
# Completar perfil (para usuarios que se registraron solo con teléfono)
# ---------------------------------------------------------------------------

@router.post("/completar-perfil", response_model=AuthResponse)
def completar_perfil(
    body: CompletarPerfilRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Completa los datos del perfil (nombre, email, password) para usuarios que
    registraron solo con teléfono. Al terminar, requiere verificar email.
    """
    # Verificar que el email no esté tomado
    email_clean = body.email.strip().lower()
    email_existente = db.execute(select(Usuario).where(Usuario.email.ilike(email_clean))).scalar_one_or_none()
    if email_existente and email_existente.id != current_user.id:
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese email.")

    current_user.nombre = body.nombre.strip()
    current_user.apellido = body.apellido.strip()
    current_user.email = email_clean
    current_user.password_hash = get_password_hash(body.password)
    current_user.password_configurada = True
    current_user.email_verificado = False
    db.commit()

    # Enviar email en segundo plano
    background_tasks.add_task(generar_y_enviar_verificacion_email, current_user.email, nombre=current_user.nombre)

    return AuthResponse(
        usuario=UsuarioRead.model_validate(current_user),
        requiere_verificacion_email=True,
    )


# ---------------------------------------------------------------------------
# Refresh / Logout / Me
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
@limiter.limit("20/minute")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: Optional[str] = Cookie(default=None, alias="refresh_token"),
):
    """Renueva los tokens usando rotation. El token usado se revoca."""
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tu sesión expiró. Iniciá sesión nuevamente.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    nuevos = renovar_tokens(refresh_token, db, device_info=_device_info(request))
    setear_cookies_auth(response, nuevos["access_token"], nuevos["refresh_token"], settings)
    return TokenResponse(
        access_token=nuevos["access_token"],
        token_type=nuevos["token_type"]
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(
    response: Response,
    db: Session = Depends(get_db),
    refresh_token: Optional[str] = Cookie(default=None, alias="refresh_token"),
):
    """Cierra la sesión del dispositivo actual revocando el refresh token."""
    if refresh_token:
        revocar_refresh_token(refresh_token, db)
    limpiar_cookies_auth(response)
    return {"detail": "Sesión cerrada correctamente."}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
def logout_all(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Cierra sesión en todos los dispositivos. Requiere access token válido."""
    count = revocar_todos_los_tokens(current_user.id, db)
    return {"detail": f"Sesión cerrada en {count} dispositivo(s)."}



