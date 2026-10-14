from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict

from app.models.usuario import AuthProvider

class UsuarioAdminListResponse(BaseModel):
    id: UUID
    nombre: str | None
    apellido: str | None = None
    email: str | None
    telefono: str | None
    auth_provider: AuthProvider | None = None
    is_active: bool
    is_admin: bool
    onboarding_completado: bool
    whatsapp_vinculado: bool
    created_at: datetime = Field(validation_alias="fecha_registro")
    ultima_actividad: datetime | None = Field(validation_alias="ultimo_acceso")
    foto_url: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class UsuarioAdminResponse(UsuarioAdminListResponse):
    paso_onboarding_actual: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

class PaginatedUsuariosResponse(BaseModel):
    total: int = Field(ge=0, description="Total de usuarios encontrados")
    page: int = Field(ge=1, description="Número de página actual")
    limit: int = Field(ge=1, le=100, description="Límite de elementos por página")
    pages: int = Field(ge=0, description="Cantidad total de páginas")
    usuarios: list[UsuarioAdminListResponse]

class CambiarEstadoRequest(BaseModel):
    is_active: bool = Field(description="Nuevo estado activo o suspendido de la cuenta")

class CambiarRolAdminRequest(BaseModel):
    is_admin: bool = Field(description="Indica si el usuario tendrá rol de administrador")

class EliminarCuentaAdminRequest(BaseModel):
    email_confirmacion: str = Field(description="Email del usuario para confirmar la eliminación irreversible")

class ResetearOnboardingRequest(BaseModel):
    confirmar: bool = Field(description="Confirmación requerida para reiniciar la configuración inicial")

class AdminStatsResponse(BaseModel):
    total: int = Field(ge=0, description="Total de usuarios registrados")
    activos: int = Field(ge=0, description="Total de cuentas activas")
    onboarding_completo: int = Field(ge=0, description="Total de usuarios con configuración completa")
    whatsapp_vinculados: int = Field(ge=0, description="Total de usuarios con WhatsApp vinculado")
    nuevos_hoy: int = Field(ge=0, description="Usuarios registrados hoy")
    nuevos_7_dias: int = Field(ge=0, description="Usuarios registrados en los últimos 7 días")
    activos_7_dias: int = Field(ge=0, description="Usuarios activos en los últimos 7 días")
    admins_total: int = Field(ge=0, description="Total de administradores")
    por_proveedor: dict[str, int] = Field(default_factory=dict, description="Desglose por método de autenticación")


class UltimaCorridaJob(BaseModel):
    inicio: datetime
    fin: datetime
    resultado: str
    duracion_ms: int
    detalle: str | None = None

    model_config = ConfigDict(from_attributes=True)


class JobSchedulerItem(BaseModel):
    job_id: str
    nombre: str
    que_hace: str
    periodo_esperado_minutos: int
    estado: str
    puede_correr_ahora: bool
    proxima_corrida: datetime | None = None
    ultima_corrida: UltimaCorridaJob | None = None
    errores_24h: int = 0
    omitidas_24h: int = 0

    model_config = ConfigDict(from_attributes=True)


class SchedulerResponse(BaseModel):
    scheduler_activo: bool
    jobs: list[JobSchedulerItem]

    model_config = ConfigDict(from_attributes=True)


class CalidadResumen(BaseModel):
    total: int
    entendidos_primera: int
    porcentaje_entendidos_primera: float
    no_entendidos: int
    deshechos_o_corregidos: int
    correcciones: int
    errores_ia: int
    topes: int


class CostoPorDia(BaseModel):
    fecha: str
    total_usd: Decimal


class CostoPorUsuario(BaseModel):
    usuario_id: UUID
    email: str | None = None
    total_usd: Decimal


class CostoResumen(BaseModel):
    total_usd: Decimal
    por_funcion: dict[str, Decimal]
    por_dia: list[CostoPorDia]
    por_usuario: list[CostoPorUsuario]
    sin_usuario_usd: Decimal


class TopesHoyResumen(BaseModel):
    gasto_hoy_usd: Decimal
    presupuesto_usd: Decimal
    nivel: str
    usuarios_tope_mensajes: int
    usuarios_tope_archivos: int


class SaludResumen(BaseModel):
    tiempo_respuesta_mediana_ms: int | float | None = None
    tiempo_respuesta_p90_ms: int | float | None = None
    mensajes_con_tiempo: int
    avisos_whatsapp_enviados: int
    avisos_whatsapp_sin_enviar: int


class MonitorWppResumenResponse(BaseModel):
    calidad: CalidadResumen
    costo: CostoResumen
    topes_hoy: TopesHoyResumen
    salud: SaludResumen


class ConversacionMonitorItem(BaseModel):
    fecha: datetime
    usuario_id: UUID
    email: str | None = None
    tipo_mensaje: str
    intent: str | None = None
    categoria: str
    confianza: float | Decimal | None = None
    tiempo_respuesta_ms: int | None = None
    error_ia: bool
    texto_visible: bool
    mensaje_usuario: str | None = None
    transcripcion: str | None = None
    mensaje_bot: str | None = None


class PaginatedConversacionesMonitorResponse(BaseModel):
    total: int
    pagina: int
    items: list[ConversacionMonitorItem]
