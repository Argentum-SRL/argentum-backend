from __future__ import annotations

from datetime import datetime, date, timezone
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, Enum as SAEnum, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.models.notificacion import Notificacion
    from app.models.configuracion_notificacion import ConfiguracionNotificacion


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


class CicloRegla(str, Enum):
    PRIMER_LUNES = "primer_lunes"
    PRIMER_MARTES = "primer_martes"
    PRIMER_MIERCOLES = "primer_miercoles"
    PRIMER_JUEVES = "primer_jueves"
    PRIMER_VIERNES = "primer_viernes"
    ULTIMO_LUNES = "ultimo_lunes"
    ULTIMO_MARTES = "ultimo_martes"
    ULTIMO_MIERCOLES = "ultimo_miercoles"
    ULTIMO_JUEVES = "ultimo_jueves"
    ULTIMO_VIERNES = "ultimo_viernes"


class CicloAjusteDireccion(str, Enum):
    ANTERIOR = "anterior"
    POSTERIOR = "posterior"


class Sexo(str, Enum):
    MASCULINO = "masculino"
    FEMENINO = "femenino"
    NO_BINARIO = "no_binario"
    PREFIERO_NO_DECIR = "prefiero_no_decir"


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    nombre: Mapped[str | None] = mapped_column(String(100), nullable=True)
    apellido: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    telefono: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    telefono_normalizado: Mapped[str | None] = mapped_column(String(20), index=True, nullable=True)
    fecha_nacimiento: Mapped[date | None] = mapped_column(
        Date, nullable=True
    )
    sexo: Mapped[Sexo | None] = mapped_column(
        SAEnum(Sexo, values_callable=lambda obj: [e.value for e in obj], name="sexo_enum"),
        nullable=True
    )
    foto_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_configurada: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
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
    ciclo_ajuste_direccion: Mapped[CicloAjusteDireccion | None] = mapped_column(
        SAEnum(
            CicloAjusteDireccion,
            values_callable=lambda obj: [e.value for e in obj],
            name="ciclo_ajuste_direccion_enum",
        ),
        nullable=True,
        default=None,
    )
    onboarding_completo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    telefono_verificado: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fecha_registro: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reset_token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reset_token_expira_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tokens_revocados_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_active(self) -> bool:
        return self.estado == EstadoUsuario.ACTIVO

    @is_active.setter
    def is_active(self, value: bool):
        self.estado = EstadoUsuario.ACTIVO if value else EstadoUsuario.INACTIVO

    @property
    def onboarding_completado(self) -> bool:
        return self.onboarding_completo

    @onboarding_completado.setter
    def onboarding_completado(self, value: bool):
        self.onboarding_completo = value

    @property
    def whatsapp_vinculado(self) -> bool:
        return self.telefono_verificado

    @whatsapp_vinculado.setter
    def whatsapp_vinculado(self, value: bool):
        self.telefono_verificado = value

    notificaciones = relationship(
        "Notificacion",
        back_populates="usuario",
        cascade="all, delete-orphan",
        lazy="dynamic"
    )
    configuracion_notificacion = relationship(
        "ConfiguracionNotificacion",
        back_populates="usuario",
        uselist=False,
        cascade="all, delete-orphan"
    )

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
