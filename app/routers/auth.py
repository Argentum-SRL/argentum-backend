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
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status, BackgroundTasks, Cookie
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import settings

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
from app.schemas.auth import (
    AuthResponse,
    CompletarPerfilRequest,
    EnviarCodigoEmailRequest,
    EnviarCodigoRequest,
    GoogleLoginRequest,
    LoginRequest,
    RecuperarPasswordRequest,
    RegisterRequest,
    TokenResponse,
    VerificarCodigoEmailRequest,
    VerificarCodigoTelefonoRequest,
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
from app.services.whatsapp_service import (
    enviar_whatsapp,
    enviar_mensaje_whatsapp,
    generar_codigo,
    guardar_codigo,
    verificar_codigo,
)
from app.services import usuario_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
def register(user_in: RegisterRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Registra un usuario con email/password.
    No devuelve tokens: primero debe verificar email y luego teléfono.
    """
    email_existente = db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none()
    if email_existente:
        if email_existente.auth_provider == AuthProvider.GOOGLE:
            raise HTTPException(
                status_code=400,
                detail="Este email ya está registrado con Google. Usá el botón de Google para iniciar sesión.",
            )
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese email.")

    if db.execute(select(Usuario).where(Usuario.telefono == user_in.telefono)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ese número de teléfono ya está registrado.")

    nuevo = Usuario(
        nombre=user_in.nombre,
        apellido=user_in.apellido,
        email=user_in.email,
        telefono=user_in.telefono,
        telefono_normalizado=normalizar_telefono_ar(user_in.telefono) if user_in.telefono else None,
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
def login(user_in: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    """Login con email y password. Requiere email verificado y contraseña configurada."""
    user = db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none()

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
        raise HTTPException(status_code=401, detail="El email o la contraseña no son correctos. Revisalos e intentá de nuevo.")

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
def recuperar_password(
    body: RecuperarPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
):
    """Inicia recuperación de contraseña. No revela si el email existe."""
    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if user:
        if user.auth_provider == AuthProvider.EMAIL:
            codigo = generar_codigo_recuperacion()
            guardar_codigo_recuperacion(body.email, codigo)
            background_tasks.add_task(enviar_email_recuperacion, body.email, codigo)
        elif user.auth_provider == AuthProvider.GOOGLE:
            background_tasks.add_task(enviar_email_aviso_google, body.email)
    return {"detail": "Si el email existe, te enviamos un código de recuperación."}


@router.post("/recuperar-password/verificar")
def verificar_recuperacion(body: VerificarRecuperacionRequest, db: Session = Depends(get_db)):
    """Verifica el código de recuperación y actualiza la contraseña."""
    if not verificar_codigo_recuperacion(body.email, body.codigo):
        raise HTTPException(status_code=400, detail="El código que ingresaste no es válido. Revisalo o pedí uno nuevo.")

    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")

    if len(body.nueva_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")

    user.password_hash = get_password_hash(body.nueva_password)
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


@router.post("/reset-password/confirmar", response_model=AuthResponse)
def confirmar_token(
    body: ConfirmarResetPasswordRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):
    """Verifica el token, actualiza la contraseña, revoca las sesiones y emite tokens de acceso."""
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

    access, refresh = _tokens(usuario, request, db)
    setear_cookies_auth(response, access, refresh, settings)
    return AuthResponse(
        access_token=access,
        usuario=UsuarioRead.model_validate(usuario),
        requiere_onboarding=_requiere_onboarding(usuario),
    )


# ---------------------------------------------------------------------------
# Verificación de email
# ---------------------------------------------------------------------------

@router.post("/email/enviar-codigo")
def enviar_codigo_email(body: EnviarCodigoEmailRequest, db: Session = Depends(get_db)):
    """Reenvía el código de verificación de email."""
    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")
    
    if user.email_verificado:
        raise HTTPException(status_code=400, detail="El email ya está verificado.")

    generar_y_enviar_verificacion_email(body.email)
    
    return {"detail": "Código enviado a tu casilla de correo."}


@router.get("/email/verificar-link")
def verificar_email_link(email: str, codigo: str, db: Session = Depends(get_db)):
    """
    Verifica el email a través de un link (método GET).
    Si es exitoso, redirige a una página de confirmación en el frontend.
    """
    ok, error = verificar_codigo_email(email, codigo)
    if not ok:
        raise HTTPException(status_code=400, detail="Este enlace de verificación no es válido. Revisá tu email y usá el más reciente.")

    user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")

    user.email_verificado = True
    
    # Si es provider EMAIL, también debemos disparar el envío del código de WhatsApp
    # para el siguiente paso del registro.
    user.email_verificado = True
    db.commit()

    # Redirigir a verificar teléfono (el frontend se encargará de pedir el código al cargar)
    from fastapi.responses import RedirectResponse
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
    ok, error = verificar_codigo_email(body.email, body.codigo)
    if not ok:
        raise HTTPException(status_code=400, detail=error)

    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No encontramos una cuenta con esos datos.")

    user.email_verificado = True

    if user.auth_provider == AuthProvider.EMAIL:
        db.commit()
        return AuthResponse(
            usuario=UsuarioRead.model_validate(user),
            requiere_verificacion_telefono=True,
        )

    if user.auth_provider == AuthProvider.TELEFONO:
        # El usuario completó su perfil; activar cuenta y emitir tokens
        user.estado = EstadoUsuario.ACTIVO
        db.commit()
        access, refresh = _tokens(user, request, db)
        setear_cookies_auth(response, access, refresh, settings)
        return AuthResponse(
            access_token=access,
            usuario=UsuarioRead.model_validate(user),
            requiere_onboarding=_requiere_onboarding(user),
        )

    db.commit()
    return AuthResponse(usuario=UsuarioRead.model_validate(user))


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
# Teléfono (WhatsApp)
# ---------------------------------------------------------------------------

@router.post("/telefono/enviar-codigo")
def enviar_codigo_telefono(body: EnviarCodigoRequest):
    """Envía un código de 6 dígitos al número dado. Expira en 10 minutos."""
    codigo = generar_codigo()
    guardar_codigo(body.telefono, codigo)

    mensaje = f"Tu código de verificación de Argentum es *{codigo}*. Expira en 10 minutos."
    enviado = enviar_mensaje_whatsapp(body.telefono, mensaje)
    if not enviado:
        raise HTTPException(status_code=500, detail="No pudimos mandarte el código por WhatsApp. Intentá de nuevo.")

    return {"detail": "Código de verificación enviado.", "telefono": body.telefono}


@router.post("/telefono/verificar", response_model=AuthResponse)
def verificar_codigo_telefono(
    body: VerificarCodigoTelefonoRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    usuario_autenticado: Usuario | None = Depends(get_optional_user),
):
    """
    Verifica el código de WhatsApp. Comportamiento según contexto:

    A) Usuario autenticado (Google sin teléfono):
       Vincula el teléfono a la cuenta existente.

    B) Usuario no autenticado + teléfono en BD (auth_provider=EMAIL, completando registro):
       Marca telefono_verificado=True, activa la cuenta, emite tokens.

    C) Usuario no autenticado + teléfono en BD (auth_provider=TELEFONO, login):
       Login normal, emite tokens.

    D) Usuario no autenticado + teléfono no existe:
       Crea usuario nuevo con auth_provider=TELEFONO, devuelve requiere_datos=True.
    """
    ok, error = verificar_codigo(body.telefono, body.codigo)
    if not ok:
        raise HTTPException(status_code=400, detail=error)

    # --- Caso A: usuario autenticado (Google añadiendo teléfono) ---
    if usuario_autenticado and not usuario_autenticado.telefono_verificado:
        # Verificar que el teléfono no esté tomado por otro usuario
        otro = db.execute(
            select(Usuario).where(
                Usuario.telefono == body.telefono,
                Usuario.id != usuario_autenticado.id,
            )
        ).scalar_one_or_none()
        if otro:
            raise HTTPException(status_code=400, detail="Ese número de teléfono ya está registrado.")

        usuario_autenticado.telefono = body.telefono
        usuario_autenticado.telefono_normalizado = normalizar_telefono_ar(body.telefono) if body.telefono else None
        usuario_autenticado.telefono_verificado = True
        db.commit()

        try:
            from app.services.notificacion_service import crear_notificacion
            from app.models.notificacion import TipoNotificacion, NivelNotificacion
            crear_notificacion(
                db=db,
                usuario_id=usuario_autenticado.id,
                tipo=TipoNotificacion.WHATSAPP_NUEVO_VINCULADO,
                nivel=NivelNotificacion.CRITICA,
                mensaje="Tu número de WhatsApp fue vinculado exitosamente. Si no fuiste vos, contactanos.",
                canal_web=True,
                canal_whatsapp=False,
                canal_email=False,
            )
        except Exception:
            pass

        return AuthResponse(
            usuario=UsuarioRead.model_validate(usuario_autenticado),
            requiere_onboarding=_requiere_onboarding(usuario_autenticado),
        )

    # --- Casos B, C, D: flujo no autenticado ---
    user = db.execute(select(Usuario).where(Usuario.telefono == body.telefono)).scalar_one_or_none()

    if not user:
        # Caso D: nuevo usuario por teléfono
        user = Usuario(
            telefono=body.telefono,
            telefono_normalizado=normalizar_telefono_ar(body.telefono) if body.telefono else None,
            auth_provider=AuthProvider.TELEFONO,
            estado=EstadoUsuario.ACTIVO,
            telefono_verificado=True,
            email_verificado=False,
            onboarding_completo=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # Crear billeteras efectivo default
        usuario_service.crear_billeteras_efectivo_default(db, user.id)

        access, refresh = _tokens(user, request, db)
        setear_cookies_auth(response, access, refresh, settings)
        return AuthResponse(
            access_token=access,
            usuario=UsuarioRead.model_validate(user),
            requiere_datos=True,
        )

    # Caso B/C: Usuario existente.
    # Si es una cuenta de EMAIL que nunca verificó email, seguimos pidiendo verificación de email.
    if user.auth_provider == AuthProvider.EMAIL and not user.email_verificado:
        raise HTTPException(status_code=401, detail="Todavía no verificaste tu cuenta. Revisá tu email para activarla.")

    # Marcamos como verificado y activo (por si venía de pendiente)
    user.telefono_verificado = True
    if user.estado == EstadoUsuario.PENDIENTE_VERIFICACION and user.email_verificado:
        user.estado = EstadoUsuario.ACTIVO
    
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    try:
        from app.services.notificacion_service import crear_notificacion
        from app.models.notificacion import TipoNotificacion, NivelNotificacion
        crear_notificacion(
            db=db,
            usuario_id=user.id,
            tipo=TipoNotificacion.WHATSAPP_NUEVO_VINCULADO,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Tu número de WhatsApp fue vinculado exitosamente. Si no fuiste vos, contactanos.",
            canal_web=True,
            canal_whatsapp=False,
            canal_email=False,
        )
    except Exception:
        pass

    access, refresh = _tokens(user, request, db)
    setear_cookies_auth(response, access, refresh, settings)
    
    # requiere_datos si no tiene nombre, email o password (usuarios de teléfono que no completaron perfil)
    if user.auth_provider == AuthProvider.TELEFONO:
        req_datos = not (
            user.nombre and user.nombre.strip() and 
            user.email and user.email.strip() and 
            user.password_configurada
        )
    else:
        # Para Google o Email, ya tienen los datos básicos en el registro
        req_datos = not (user.nombre and user.nombre.strip())

    # Si el usuario ya tiene email, no debería pedir completar datos (evita bucles en registro por email)
    if user.email and user.auth_provider == AuthProvider.EMAIL:
        req_datos = False

    # Solo pedimos onboarding si ya tiene los datos básicos completos
    req_onboarding = _requiere_onboarding(user) if not req_datos else False

    return AuthResponse(
        access_token=access,
        usuario=UsuarioRead.model_validate(user),
        requiere_datos=req_datos,
        requiere_onboarding=req_onboarding,
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
    email_existente = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if email_existente and email_existente.id != current_user.id:
        raise HTTPException(status_code=400, detail="Ya existe una cuenta con ese email.")

    current_user.nombre = body.nombre
    current_user.apellido = body.apellido
    current_user.email = body.email
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



