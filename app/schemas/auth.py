import re
from pydantic import BaseModel, field_validator
from app.schemas.usuario import UsuarioRead

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_REGEX = re.compile(r"^\+?[0-9]{8,20}$")
OTP_REGEX = re.compile(r"^\d{6}$")


def _validar_password(v: str) -> str:
    if not v:
        raise ValueError("La contraseña no puede estar vacía.")
    if len(v) < 8:
        raise ValueError("La contraseña debe tener al menos 8 caracteres.")
    if len(v) > 128:
        raise ValueError("La contraseña no puede superar los 128 caracteres.")
    if not re.search(r"[A-Z]", v):
        raise ValueError("La contraseña debe incluir al menos una letra mayúscula.")
    if not re.search(r"[a-z]", v):
        raise ValueError("La contraseña debe incluir al menos una letra minúscula.")
    if not re.search(r"[0-9]", v):
        raise ValueError("La contraseña debe incluir al menos un número.")
    return v


def _validar_email(v: str) -> str:
    email = v.strip().lower()
    if not email:
        raise ValueError("El correo electrónico no puede estar vacío.")
    if len(email) > 255:
        raise ValueError("El correo electrónico no puede superar los 255 caracteres.")
    if not EMAIL_REGEX.match(email):
        raise ValueError("Ingresá un correo electrónico válido.")
    return email


def _validar_telefono(v: str) -> str:
    tel = v.strip().replace(" ", "").replace("-", "")
    if not tel:
        raise ValueError("El número de teléfono no puede estar vacío.")
    if not PHONE_REGEX.match(tel):
        raise ValueError("Ingresá un número de teléfono válido (entre 8 y 20 dígitos numéricos).")
    return tel


def _validar_codigo_otp(v: str) -> str:
    cod = v.strip()
    if not OTP_REGEX.match(cod):
        raise ValueError("El código debe tener exactamente 6 dígitos numéricos.")
    return cod


def _validar_texto_nombre(v: str, campo: str) -> str:
    txt = v.strip()
    if len(txt) < 2:
        raise ValueError(f"El {campo} debe tener al menos 2 caracteres.")
    if len(txt) > 100:
        raise ValueError(f"El {campo} no puede superar los 100 caracteres.")
    return txt


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class GoogleLoginRequest(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        t = v.strip()
        if not t:
            raise ValueError("El token de Google no puede estar vacío.")
        return t


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if not v:
            raise ValueError("Ingresá tu contraseña.")
        if len(v) > 128:
            raise ValueError("La contraseña no puede superar los 128 caracteres.")
        return v


class EnviarCodigoRequest(BaseModel):
    telefono: str

    @field_validator("telefono")
    @classmethod
    def sanitize_telefono(cls, v: str) -> str:
        return _validar_telefono(v)


class VerificarCodigoTelefonoRequest(BaseModel):
    telefono: str
    codigo: str

    @field_validator("telefono")
    @classmethod
    def sanitize_telefono(cls, v: str) -> str:
        return _validar_telefono(v)

    @field_validator("codigo")
    @classmethod
    def sanitize_codigo(cls, v: str) -> str:
        return _validar_codigo_otp(v)


class EnviarCodigoWhatsappRequest(BaseModel):
    telefono: str

    @field_validator("telefono")
    @classmethod
    def sanitize_telefono(cls, v: str) -> str:
        return _validar_telefono(v)


class VerificarCodigoRequest(BaseModel):
    telefono: str
    codigo: str

    @field_validator("telefono")
    @classmethod
    def sanitize_telefono(cls, v: str) -> str:
        return _validar_telefono(v)

    @field_validator("codigo")
    @classmethod
    def sanitize_codigo(cls, v: str) -> str:
        return _validar_codigo_otp(v)


class EnviarCodigoEmailRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)


class VerificarCodigoEmailRequest(BaseModel):
    email: str
    codigo: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)

    @field_validator("codigo")
    @classmethod
    def sanitize_codigo(cls, v: str) -> str:
        return _validar_codigo_otp(v)


class CompletarPerfilRequest(BaseModel):
    nombre: str
    apellido: str
    email: str
    password: str

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str) -> str:
        return _validar_texto_nombre(v, "nombre")

    @field_validator("apellido")
    @classmethod
    def validate_apellido(cls, v: str) -> str:
        return _validar_texto_nombre(v, "apellido")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validar_password(v)


class RecuperarPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)


class VerificarRecuperacionRequest(BaseModel):
    email: str
    codigo: str
    nueva_password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)

    @field_validator("codigo")
    @classmethod
    def sanitize_codigo(cls, v: str) -> str:
        return _validar_codigo_otp(v)

    @field_validator("nueva_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validar_password(v)


class RegisterRequest(BaseModel):
    nombre: str
    apellido: str
    email: str
    telefono: str
    password: str
    acepta_terminos: bool = True

    @field_validator("nombre")
    @classmethod
    def validate_nombre(cls, v: str) -> str:
        return _validar_texto_nombre(v, "nombre")

    @field_validator("apellido")
    @classmethod
    def validate_apellido(cls, v: str) -> str:
        return _validar_texto_nombre(v, "apellido")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _validar_email(v)

    @field_validator("telefono")
    @classmethod
    def sanitize_telefono(cls, v: str) -> str:
        return _validar_telefono(v)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validar_password(v)

    @field_validator("acepta_terminos")
    @classmethod
    def validate_terminos(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Debés aceptar los Términos y Condiciones para crear tu cuenta.")
        return v


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class AuthResponse(BaseModel):
    """
    Respuesta estándar para todos los endpoints de autenticación.
    Los tokens son null cuando el usuario aún no completó la verificación.
    El frontend debe leer los flags y redirigir según corresponda.
    """
    access_token: str | None = None
    token_type: str = "bearer"
    usuario: UsuarioRead | None = None
    requiere_telefono: bool = False
    requiere_datos: bool = False
    requiere_verificacion_email: bool = False
    requiere_verificacion_telefono: bool = False
    requiere_onboarding: bool = False


class TokenResponse(BaseModel):
    """Respuesta de /auth/refresh con los nuevos tokens."""
    access_token: str
    token_type: str = "bearer"


class MeResponse(BaseModel):
    """Respuesta de /auth/me."""
    usuario: UsuarioRead


class OkResponse(BaseModel):
    ok: bool = True


class ValidarResetTokenResponse(BaseModel):
    nombre: str


class ConfirmarResetPasswordRequest(BaseModel):
    token: str
    nueva_password: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        t = v.strip()
        if not t:
            raise ValueError("El token de restablecimiento es requerido.")
        return t

    @field_validator("nueva_password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        return _validar_password(v)

