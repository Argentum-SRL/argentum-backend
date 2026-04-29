from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuthProvider(str, Enum):
    EMAIL = "email"
    GOOGLE = "google"
    TELEFONO = "telefono"


class RolUsuario(str, Enum):
    USUARIO = "usuario"
    ADMIN = "admin"


class EstadoUsuario(str, Enum):
    ACTIVO = "activo"
    INACTIVO = "inactivo"
    PENDIENTE_VERIFICACION = "pendiente_verificacion"


class Moneda(str, Enum):
    ARS = "ARS"
    USD = "USD"


class CicloTipo(str, Enum):
    DIA_FIJO = "dia_fijo"
    REGLA = "regla"


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    apellido: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    foto_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[AuthProvider] = mapped_column(
        SAEnum(AuthProvider, values_callable=lambda obj: [e.value for e in obj], name="auth_provider_enum"), nullable=False
    )
    rol: Mapped[RolUsuario] = mapped_column(
        SAEnum(RolUsuario, values_callable=lambda obj: [e.value for e in obj], name="rol_usuario_enum"), nullable=False, default=RolUsuario.USUARIO
    )
    estado: Mapped[EstadoUsuario] = mapped_column(
        SAEnum(EstadoUsuario, values_callable=lambda obj: [e.value for e in obj], name="estado_usuario_enum"), nullable=False, default=EstadoUsuario.ACTIVO
    )
    moneda_principal: Mapped[Moneda | None] = mapped_column(
        SAEnum(Moneda, values_callable=lambda obj: [e.value for e in obj], name="moneda_enum"), nullable=True
    )
    moneda_secundaria_activa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tipo_dolar: Mapped[str] = mapped_column(String(30), nullable=False, default="blue")
    ciclo_tipo: Mapped[CicloTipo | None] = mapped_column(
        SAEnum(CicloTipo, values_callable=lambda obj: [e.value for e in obj], name="ciclo_tipo_enum"), nullable=True
    )
    ciclo_valor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    onboarding_completo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telefono_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            "Usuario("
            f"id={self.id!r}, "
            f"telefono={self.telefono!r}, "
            f"email={self.email!r}, "
            f"rol={self.rol.value!r}, "
            f"estado={self.estado.value!r}"
            ")"
        )
