from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict, PlainSerializer

from app.models.usuario import AuthProvider

DecimalNumero = Annotated[
    Decimal,
    PlainSerializer(
        lambda v: float(v) if v is not None else None,
        return_type=float | None,
        when_used="json",
    ),
]


class CostoUsuarioMesSchema(BaseModel):
    ia_usd: DecimalNumero
    ia_ars: int | None = None
    whatsapp_cobrados: int
    whatsapp_ars: int
    total_ars: int
    dolar_oficial: DecimalNumero | None = None
    dolar_fecha: date | None = None
    sin_dolar: bool


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
    estado_cuenta: str = "activa"
    estado_visible: str = "en_desuso"
    movimientos_mes: int = 0
    ultimo_uso: datetime | None = None
    ultimo_uso_canal: str | None = None
    costo_mes_ars: int = 0

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class UsuarioAdminResponse(UsuarioAdminListResponse):
    paso_onboarding_actual: str | None = None
    ultimo_uso_web: datetime | None = None
    ultimo_uso_whatsapp: datetime | None = None
    tiene_contrasena: bool = False
    costo_mes: CostoUsuarioMesSchema | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PaginatedUsuariosResponse(BaseModel):
    total: int = Field(ge=0, description="Total de usuarios encontrados")
    page: int = Field(ge=1, description="Número de página actual")
    limit: int = Field(ge=1, le=100, description="Límite de elementos por página")
    pages: int = Field(ge=0, description="Cantidad total de páginas")
    conteos_estado: dict[str, int] = Field(default_factory=dict)
    usuarios: list[UsuarioAdminListResponse]


class CambiarEstadoRequest(BaseModel):
    is_active: bool = Field(description="Nuevo estado activo o suspendido de la cuenta")


class CambiarRolAdminRequest(BaseModel):
    is_admin: bool = Field(description="Indica si el usuario tendrá rol de administrador")
    password_actual: str | None = None
    google_credential: str | None = None


class EliminarCuentaAdminRequest(BaseModel):
    email_confirmacion: str = Field(description="Email del usuario para confirmar la eliminación irreversible")
    password_actual: str | None = None
    google_credential: str | None = None


class AdminStatsResponse(BaseModel):
    total: int = Field(ge=0, description="Total de usuarios registrados")
    activos: int = Field(ge=0, description="Total de cuentas activas")
    onboarding_completo: int = Field(ge=0, description="Total de usuarios con configuración completa")
    whatsapp_vinculados: int = Field(ge=0, description="Total de usuarios con WhatsApp vinculado")
    nuevos_hoy: int = Field(ge=0, description="Usuarios registrados hoy")
    nuevos_7_dias: int = Field(ge=0, description="Usuarios registrados en los últimos 7 días")
    activos_7_dias: int = Field(ge=0, description="Usuarios activos en los últimos 7 días")
    usaron_7_dias: int = Field(
        default=0, ge=0, description="Usuarios con uso real en los últimos 7 días"
    )
    en_desuso: int = Field(
        default=0, ge=0, description="Usuarios con estado visible en_desuso"
    )
    activos_pct: int = Field(
        default=0, ge=0, description="Porcentaje redondeado de usuarios activos en 7 días"
    )
    costo_mes_ars: int = Field(default=0, ge=0, description="Costo total estimado del mes en pesos")
    costo_mes_sin_dolar: bool = Field(
        default=False,
        description="Indica si faltó el dólar oficial para calcular la parte de IA",
    )
    costo_mes_fuente: str = Field(
        default="estimado",
        description="Fuente del costo de IA del mes (openai o estimado)",
    )
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


class CostoUsuarioMesItem(BaseModel):
    usuario_id: UUID
    nombre: str
    total_ars: int
    ia_usd: DecimalNumero
    whatsapp_cobrados: int


class CostoResumen(BaseModel):
    total_usd: Decimal
    por_funcion: dict[str, Decimal]
    por_dia: list[CostoPorDia]
    por_usuario: list[CostoPorUsuario]
    por_usuario_mes: list[CostoUsuarioMesItem] = Field(default_factory=list)
    sin_usuario_usd: Decimal
    openai_usd: Decimal | None = None
    openai_estado: str | None = None
    openai_hasta: date | None = None


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
    wpp_fallados: int = 0
    wpp_interrumpidos: int = 0
    wpp_vencidos: int = 0


class SalientesMes(BaseModel):
    enviados: int = 0
    plantillas: int = 0
    cobrados_meta: int = 0
    gratis_meta: int = 0
    sin_dato_meta: int = 0
    cupo_gratis: int = 1000
    cupo_usado: int = 0
    no_enviados: int = 0


class SalientesPorDia(BaseModel):
    fecha: str
    total: int = 0
    respuestas: int = 0
    plantillas: int = 0
    otros: int = 0


class SalientesWppResumen(BaseModel):
    mes: SalientesMes = Field(default_factory=SalientesMes)
    por_dia: list[SalientesPorDia] = Field(default_factory=list)
    no_enviados_por_motivo: dict[str, int] = Field(default_factory=dict)
    entrantes_con_mas_de_una_respuesta: int = 0


class MonitorWppResumenResponse(BaseModel):
    calidad: CalidadResumen
    costo: CostoResumen
    topes_hoy: TopesHoyResumen
    salud: SaludResumen
    whatsapp_salientes: SalientesWppResumen = Field(default_factory=SalientesWppResumen)


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
