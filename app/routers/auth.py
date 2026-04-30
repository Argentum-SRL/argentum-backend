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

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import (
    crear_access_token,
    crear_refresh_token,
    get_current_user,
    get_optional_user,
    renovar_tokens,
    revocar_refresh_token,
    revocar_todos_los_tokens,
)
from app.core.database import get_db
from app.core.security import get_password_hash, verify_password
from app.models.usuario import AuthProvider, EstadoUsuario, Usuario
from app.schemas.auth import (
    AuthResponse,
    CompletarPerfilRequest,
    EnviarCodigoEmailRequest,
    EnviarCodigoRequest,
    EnviarCodigoWhatsappRequest,
    GoogleLoginRequest,
    LoginRequest,
    LogoutRequest,
    MeResponse,
    OkResponse,
    RecuperarPasswordRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    VerificarCodigoRequest,
    VerificarCodigoEmailRequest,
    VerificarCodigoTelefonoRequest,
    VerificarRecuperacionRequest,
)
from app.schemas.usuario import UsuarioRead
from app.services.auth_service import verify_google_token
from app.services.email_service import (
    enviar_email_recuperacion,
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

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/enviar-codigo-whatsapp", response_model=OkResponse)
def enviar_codigo_whatsapp(body: EnviarCodigoWhatsappRequest):
    """Genera OTP de 6 digitos y lo envia por WhatsApp al telefono recibido."""
    codigo = generar_codigo()
    guardar_codigo(body.telefono, codigo)

    mensaje = (
        "*Argentum*\n"
        f"Tu codigo de verificacion es: *{codigo}*\n"
        "Expira en 10 minutos.\n"
        "Si no lo pediste, ignora este mensaje."
    )

    if not enviar_whatsapp(body.telefono, mensaje):
        raise HTTPException(status_code=500, detail="No se pudo enviar el codigo por WhatsApp.")

    return OkResponse(ok=True)


@router.post("/verificar-codigo", response_model=OkResponse)
def verificar_codigo_whatsapp(body: VerificarCodigoRequest):
    """Verifica OTP de WhatsApp guardado previamente."""
    ok, error = verificar_codigo(body.telefono, body.codigo)
    if not ok:
        raise HTTPException(status_code=400, detail=error or "Codigo invalido o expirado.")
    return OkResponse(ok=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _device_info(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:200] if ua else None


def _tokens(usuario_id, request: Request, db: Session) -> tuple[str, str]:
    return (
        crear_access_token(usuario_id),
        crear_refresh_token(usuario_id, db, device_info=_device_info(request)),
    )


def _requiere_onboarding(user: Usuario) -> bool:
    return not user.onboarding_completo


# ---------------------------------------------------------------------------
# Email / Password
# ---------------------------------------------------------------------------

@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
def register(user_in: RegisterRequest, db: Session = Depends(get_db)):
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
        raise HTTPException(status_code=400, detail="Ese mail ya está registrado en otra cuenta.")

    if db.execute(select(Usuario).where(Usuario.telefono == user_in.telefono)).scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ese número de teléfono ya está registrado.")

    nuevo = Usuario(
        nombre=user_in.nombre,
        apellido=user_in.apellido,
        email=user_in.email,
        telefono=user_in.telefono,
        password_hash=get_password_hash(user_in.password),
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

    generar_y_enviar_verificacion_email(nuevo.email)

    return AuthResponse(
        usuario=UsuarioRead.model_validate(nuevo),
        requiere_verificacion_email=True,
    )


@router.post("/login", response_model=AuthResponse)
def login(user_in: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login con email y password. Requiere email verificado y contraseña configurada."""
    user = db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

    if not user.email_verificado:
        raise HTTPException(status_code=401, detail="Primero verificá tu email. Revisá tu casilla.")

    if not user.password_hash:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta no tiene contraseña configurada. Ingresá con tu teléfono o configurá una contraseña desde tu perfil.",
        )

    if not verify_password(user_in.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas.")

    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    access, refresh = _tokens(user.id, request, db)
    return AuthResponse(
        access_token=access,
        refresh_token=refresh,
        usuario=UsuarioRead.model_validate(user),
        requiere_onboarding=_requiere_onboarding(user),
    )


@router.post("/recuperar-password")
def recuperar_password(body: RecuperarPasswordRequest, db: Session = Depends(get_db)):
    """Inicia recuperación de contraseña. No revela si el email existe."""
    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if user and user.auth_provider == AuthProvider.EMAIL:
        codigo = generar_codigo_recuperacion()
        guardar_codigo_recuperacion(body.email, codigo)
        enviar_email_recuperacion(body.email, codigo)
    return {"detail": "Si el email existe, te enviamos un código de recuperación."}


@router.post("/recuperar-password/verificar")
def verificar_recuperacion(body: VerificarRecuperacionRequest, db: Session = Depends(get_db)):
    """Verifica el código de recuperación y actualiza la contraseña."""
    if not verificar_codigo_recuperacion(body.email, body.codigo):
        raise HTTPException(status_code=400, detail="Código incorrecto o expirado.")

    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    if len(body.nueva_password) < 8:
        raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 8 caracteres.")

    user.password_hash = get_password_hash(body.nueva_password)
    db.commit()
    return {"detail": "Contraseña actualizada correctamente."}


# ---------------------------------------------------------------------------
# Verificación de email
# ---------------------------------------------------------------------------

@router.post("/email/enviar-codigo")
def enviar_codigo_email(body: EnviarCodigoEmailRequest, db: Session = Depends(get_db)):
    """Reenvía el código de verificación de email."""
    user = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No existe una cuenta con ese email.")
    if user.email_verificado:
        raise HTTPException(status_code=400, detail="El email ya está verificado.")

    generar_y_enviar_verificacion_email(body.email)
    return {"detail": "Código enviado al email."}


@router.post("/email/verificar", response_model=AuthResponse)
def verificar_email(body: VerificarCodigoEmailRequest, request: Request, db: Session = Depends(get_db)):
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
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    user.email_verificado = True

    if user.auth_provider == AuthProvider.EMAIL:
        # Enviar WhatsApp al teléfono registrado para completar la verificación
        codigo_wa = generar_codigo()
        guardar_codigo(user.telefono, codigo_wa)
        
        mensaje = (
            f"*Argentum*\n"
            f"Tu codigo de verificacion es: *{codigo_wa}*\n"
            f"Expira en 10 minutos.\n"
            f"Si no lo pediste, ignora este mensaje."
        )
        enviado = enviar_mensaje_whatsapp(user.telefono, mensaje)
        if not enviado:
            raise HTTPException(status_code=500, detail="No se pudo enviar el mensaje de WhatsApp. Intentá de nuevo.")

        db.commit()
        return AuthResponse(
            usuario=UsuarioRead.model_validate(user),
            requiere_verificacion_telefono=True,
        )

    if user.auth_provider == AuthProvider.TELEFONO:
        # El usuario completó su perfil; activar cuenta y emitir tokens
        user.estado = EstadoUsuario.ACTIVO
        db.commit()
        access, refresh = _tokens(user.id, request, db)
        return AuthResponse(
            access_token=access,
            refresh_token=refresh,
            usuario=UsuarioRead.model_validate(user),
            requiere_onboarding=_requiere_onboarding(user),
        )

    db.commit()
    return AuthResponse(usuario=UsuarioRead.model_validate(user))


# ---------------------------------------------------------------------------
# Google OAuth2
# ---------------------------------------------------------------------------

@router.post("/google", response_model=AuthResponse)
def login_google(body: GoogleLoginRequest, request: Request, db: Session = Depends(get_db)):
    """Login / registro con Google ID token."""
    print('[Auth][Google][Backend] /auth/google recibido', {
        'origin': request.headers.get('origin'),
        'userAgent': request.headers.get('user-agent'),
        'tokenPresent': bool(body.token),
        'tokenLength': len(body.token),
        'tokenPrefix': body.token[:12] + '...' if body.token else None,
    })

    token_info = verify_google_token(body.token)
    email = token_info.get("email")
    if not email:
        print('[Auth][Google][Backend] Token válido pero sin email')
        raise HTTPException(status_code=400, detail="El token de Google no contiene un email válido.")

    user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()

    print('[Auth][Google][Backend] Usuario buscado', {
        'email': email,
        'exists': bool(user),
        'authProvider': getattr(user.auth_provider, 'value', None) if user else None,
    })

    if user:
        if not user.email_verificado:
            raise HTTPException(
                status_code=400,
                detail="Primero verificá tu email para poder ingresar con Google.",
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

        print('[Auth][Google][Backend] Creando usuario nuevo', {
            'email': email,
            'nombre': nombre,
            'apellido': apellido,
            'picture': bool(token_info.get('picture')),
        })

        user = Usuario(
            nombre=nombre or None,
            apellido=apellido or None,
            email=email,
            telefono=None,
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

    # Emitir tokens siempre (ya sea login o registro)
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    print('[Auth][Google][Backend] Tokens emitidos', {
        'userId': user.id,
        'email': user.email,
        'requiereTelefono': not user.telefono_verificado,
        'requiereOnboarding': _requiere_onboarding(user) if user.telefono_verificado else False,
    })

    access, refresh = _tokens(user.id, request, db)

    return AuthResponse(
        access_token=access,
        refresh_token=refresh,
        usuario=UsuarioRead.model_validate(user),
        requiere_telefono=not user.telefono_verificado,
        requiere_onboarding=_requiere_onboarding(user) if user.telefono_verificado else False,
    )


# ---------------------------------------------------------------------------
# Teléfono (WhatsApp)
# ---------------------------------------------------------------------------

@router.post("/telefono/enviar-codigo")
def enviar_codigo_telefono(body: EnviarCodigoRequest):
    """Envía un código de 6 dígitos al número dado. Expira en 10 minutos."""
    codigo = generar_codigo()
    guardar_codigo(body.telefono, codigo)

    mensaje = (
        f"*Argentum*\n"
        f"Tu codigo de verificacion es: *{codigo}*\n"
        f"Expira en 10 minutos.\n"
        f"Si no lo pediste, ignora este mensaje."
    )
    enviado = enviar_mensaje_whatsapp(body.telefono, mensaje)
    if not enviado:
        raise HTTPException(status_code=500, detail="No se pudo enviar el mensaje de WhatsApp. Intentá de nuevo.")

    return {"detail": "Código de verificación enviado.", "telefono": body.telefono}


@router.post("/telefono/verificar", response_model=AuthResponse)
def verificar_codigo_telefono(
    body: VerificarCodigoTelefonoRequest,
    request: Request,
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
        usuario_autenticado.telefono_verificado = True
        db.commit()

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
            auth_provider=AuthProvider.TELEFONO,
            estado=EstadoUsuario.ACTIVO,
            telefono_verificado=True,
            email_verificado=False,
            onboarding_completo=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        access, refresh = _tokens(user.id, request, db)
        return AuthResponse(
            access_token=access,
            refresh_token=refresh,
            usuario=UsuarioRead.model_validate(user),
            requiere_datos=True,
        )

    # Caso B/C: Usuario existente.
    # Si es una cuenta de EMAIL que nunca verificó email, seguimos pidiendo verificación de email.
    if user.auth_provider == AuthProvider.EMAIL and not user.email_verificado:
        raise HTTPException(status_code=401, detail="Primero verificá tu email. Revisá tu casilla.")

    # Marcamos como verificado y activo (por si venía de pendiente)
    user.telefono_verificado = True
    if user.estado == EstadoUsuario.PENDIENTE_VERIFICACION and user.email_verificado:
        user.estado = EstadoUsuario.ACTIVO
    
    user.ultimo_acceso = datetime.now(timezone.utc)
    db.commit()

    access, refresh = _tokens(user.id, request, db)
    
    # requiere_datos si no tiene nombre (usuarios de teléfono que no completaron perfil)
    # requiere_onboarding si tiene nombre pero no completó onboarding
    req_datos = not user.nombre
    req_onboarding = _requiere_onboarding(user) if user.nombre else False

    return AuthResponse(
        access_token=access,
        refresh_token=refresh,
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
    request: Request,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Agrega nombre, apellido, email y password a una cuenta creada solo con teléfono.
    Después envía un código de verificación al email.
    Requiere autenticación.
    """
    if current_user.auth_provider != AuthProvider.TELEFONO:
        raise HTTPException(status_code=400, detail="Este endpoint es solo para cuentas creadas con teléfono.")

    email_existente = db.execute(select(Usuario).where(Usuario.email == body.email)).scalar_one_or_none()
    if email_existente:
        raise HTTPException(status_code=400, detail="Ese mail ya está registrado en otra cuenta.")

    current_user.nombre = body.nombre
    current_user.apellido = body.apellido
    current_user.email = body.email
    current_user.password_hash = get_password_hash(body.password)
    current_user.email_verificado = False
    db.commit()

    generar_y_enviar_verificacion_email(body.email)

    return AuthResponse(
        usuario=UsuarioRead.model_validate(current_user),
        requiere_verificacion_email=True,
    )


# ---------------------------------------------------------------------------
# Refresh / Logout / Me
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """Renueva los tokens usando rotation. El token usado se revoca."""
    nuevos = renovar_tokens(body.refresh_token, db, device_info=_device_info(request))
    return TokenResponse(**nuevos)


@router.post("/logout", status_code=status.HTTP_200_OK)
def logout(body: LogoutRequest, db: Session = Depends(get_db)):
    """Cierra la sesión del dispositivo actual revocando el refresh token."""
    revocar_refresh_token(body.refresh_token, db)
    return {"detail": "Sesión cerrada correctamente."}


@router.post("/logout-all", status_code=status.HTTP_200_OK)
def logout_all(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """Cierra sesión en todos los dispositivos. Requiere access token válido."""
    count = revocar_todos_los_tokens(current_user.id, db)
    return {"detail": f"Sesión cerrada en {count} dispositivo(s)."}


@router.get("/me", response_model=MeResponse)
def me(current_user: Usuario = Depends(get_current_user)):
    """Devuelve el perfil del usuario autenticado."""
    return MeResponse(usuario=UsuarioRead.model_validate(current_user))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_cuenta(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    """
    Elimina permanentemente la cuenta del usuario y todos sus datos relacionados.
    Realiza una limpieza manual de las tablas dependientes para evitar errores de FK.
    """
    from sqlalchemy import text

    # 1. Tablas dependientes indirectas (borrar antes que sus padres)
    db.execute(text("DELETE FROM movimientos_meta WHERE meta_id IN (SELECT id FROM metas WHERE usuario_id = :uid)"), {"uid": current_user.id})
    db.execute(text("DELETE FROM cuotas WHERE grupo_id IN (SELECT id FROM grupos_cuotas WHERE usuario_id = :uid)"), {"uid": current_user.id})
    db.execute(text("DELETE FROM historial_suscripciones WHERE suscripcion_id IN (SELECT id FROM suscripciones WHERE usuario_id = :uid)"), {"uid": current_user.id})
    db.execute(text("DELETE FROM periodos_presupuesto WHERE presupuesto_id IN (SELECT id FROM presupuestos WHERE usuario_id = :uid)"), {"uid": current_user.id})
    db.execute(text("DELETE FROM presupuestos_categorias WHERE presupuesto_id IN (SELECT id FROM presupuestos WHERE usuario_id = :uid)"), {"uid": current_user.id})

    # 2. Tablas con usuario_id (ordenadas por dependencias)
    tablas_usuario = [
        "transacciones",
        "transferencias_internas",
        "grupos_cuotas",
        "transacciones_recurrentes",
        "suscripciones",
        "presupuestos",
        "notificaciones",
        "perfiles_financieros",
        "metas",
        "conversaciones_wpp",
        "categorias_excluidas",
        "configuraciones_notificacion",
        "billeteras",
        "refresh_tokens"
    ]

    for tabla in tablas_usuario:
        db.execute(text(f"DELETE FROM {tabla} WHERE usuario_id = :uid"), {"uid": current_user.id})

    # 3. Tablas con creador_id (en lugar de usuario_id)
    db.execute(text("DELETE FROM subcategorias WHERE creador_id = :uid"), {"uid": current_user.id})
    db.execute(text("DELETE FROM categorias WHERE creador_id = :uid"), {"uid": current_user.id})
    
    # 4. Finalmente borrar el usuario
    db.delete(current_user)
    db.commit()
    return None
