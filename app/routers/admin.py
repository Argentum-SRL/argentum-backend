from datetime import date, datetime, timezone, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, status, Query, Path, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from uuid import UUID
import logging

from app.core.database import get_db
from app.core.auth import get_current_admin_user
from app.models.usuario import Usuario
from app.models.ejecucion_job import EjecucionJob
from app.schemas.admin import (
    UsuarioAdminResponse,
    UsuarioAdminListResponse,
    PaginatedUsuariosResponse,
    CambiarEstadoRequest,
    CambiarRolAdminRequest,
    EliminarCuentaAdminRequest,
    CostoUsuarioMesSchema,
    AdminStatsResponse,
    JobSchedulerItem,
    SchedulerResponse,
    UltimaCorridaJob,
    MonitorWppResumenResponse,
    PaginatedConversacionesMonitorResponse,
)
from app.core.jobs_catalogo import JOBS_CATALOGO, JOBS_MANUALES_PERMITIDOS, JOBS_POR_ID
from app.core.registro_jobs import calcular_estado_job
from app.services import (
    admin_registro_service,
    admin_service,
    admin_uso_service,
    costos_service,
    monitor_wpp_service,
    reautenticacion_service,
)
from app.core.config import settings
from app.utils.fecha import hoy_argentina

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(get_current_admin_user)],
)

ACCION_REGISTRO_VALIDA = Literal[
    "suspender",
    "reactivar",
    "mail_contrasena",
    "cerrar_sesiones",
    "desconectar_whatsapp",
    "hacer_admin",
    "quitar_admin",
    "eliminar_cuenta",
    "correr_tarea",
    "verificacion_fallida",
]


def ahora_utc() -> datetime:
    """Devuelve la fecha y hora actual en UTC (facilita pruebas determinísticas)."""
    return datetime.now(timezone.utc)


def _registrar_y_commit(
    db: Session,
    admin: Usuario,
    accion: str,
    usuario: Usuario | None = None,
    detalle: str | None = None,
) -> None:
    try:
        admin_registro_service.registrar_accion(
            db, admin, accion, usuario=usuario, detalle=detalle
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("No se pudo registrar la acción de admin.")


def _armar_usuario_detalle(
    db: Session,
    user: Usuario,
    hoy,
    ahora: datetime | None = None,
) -> UsuarioAdminResponse:
    ahora_ref = ahora if ahora is not None else ahora_utc()
    uso = admin_uso_service.ultimos_usos(db, [user.id])[user.id]
    costo = costos_service.costo_mes_por_usuario(db, hoy, usuario_ids=[user.id])[user.id]
    movimientos = admin_uso_service.movimientos_mes(db, [user.id], hoy).get(user.id, 0)
    user_data = UsuarioAdminResponse.model_validate(user)
    user_data.paso_onboarding_actual = _map_onboarding_step(user)
    user_data.estado_cuenta = admin_uso_service.estado_cuenta(user)
    user_data.estado_visible = admin_uso_service.estado_visible(
        user, uso["ultimo_uso"], ahora_ref
    )
    user_data.movimientos_mes = movimientos
    user_data.ultimo_uso = uso["ultimo_uso"]
    user_data.ultimo_uso_canal = uso["ultimo_uso_canal"]
    user_data.ultimo_uso_web = uso["ultimo_uso_web"]
    user_data.ultimo_uso_whatsapp = uso["ultimo_uso_whatsapp"]
    user_data.tiene_contrasena = reautenticacion_service.usa_contrasena(user)
    user_data.costo_mes_ars = costo.total_ars
    user_data.costo_mes = CostoUsuarioMesSchema(
        ia_usd=costo.ia_usd,
        ia_ars=costo.ia_ars,
        whatsapp_cobrados=costo.whatsapp_cobrados,
        whatsapp_ars=costo.whatsapp_ars,
        total_ars=costo.total_ars,
        dolar_oficial=costo.dolar_oficial,
        dolar_fecha=costo.dolar_fecha,
        sin_dolar=costo.sin_dolar,
    )
    return user_data


def _map_onboarding_step(user: Usuario) -> str | None:
    if user.onboarding_completo:
        return None
    pasos = []
    if not user.nombre or not user.apellido or not user.fecha_nacimiento or not user.sexo:
        pasos.append("datos_personales")
    elif not user.ciclo_tipo or not user.ciclo_valor:
        pasos.append("ciclo_financiero")
    else:
        pasos.append("moneda")
    return pasos[0] if pasos else "moneda"


@router.get("/usuarios", response_model=dict)
def get_usuarios(
    page: int = Query(1, ge=1, description="Número de página"),
    limit: int = Query(20, ge=1, le=100, description="Cantidad de usuarios por página"),
    search: str | None = Query(None, max_length=150, description="Término de búsqueda"),
    estado: Literal[
        "activo",
        "nuevo",
        "poco_uso",
        "en_desuso",
        "sin_empezar",
        "suspendida",
        "sin_verificar",
    ] | None = Query(None, description="Filtro por estado visible de uso"),
    orden: Literal["ultimo_uso", "movimientos", "costo_mes", "registro"] = Query(
        "ultimo_uso", description="Criterio de orden"
    ),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Lista paginada y filtrada de usuarios. Excluye datos financieros."""
    logger.info("[ADMIN] Admin %s listó usuarios. page=%s limit=%s", admin.id, page, limit)
    hoy = hoy_argentina()
    ahora = ahora_utc()
    res = admin_service.listar_usuarios(
        db,
        page=page,
        limit=limit,
        search=search,
        estado=estado,
        orden=orden,
        hoy=hoy,
        ahora=ahora,
    )

    usos = res["usos"]
    costos = res["costos"]
    estados_visibles = res["estados_visibles"]
    movimientos = res["movimientos"]
    usuarios_mapped = []
    for u in res["usuarios"]:
        item = UsuarioAdminListResponse.model_validate(u)
        uso_u = usos.get(u.id, {})
        costo_u = costos.get(u.id)
        item.estado_cuenta = admin_uso_service.estado_cuenta(u)
        item.estado_visible = estados_visibles.get(u.id, "en_desuso")
        item.movimientos_mes = movimientos.get(u.id, 0)
        item.ultimo_uso = uso_u.get("ultimo_uso")
        item.ultimo_uso_canal = uso_u.get("ultimo_uso_canal")
        item.costo_mes_ars = costo_u.total_ars if costo_u else 0
        usuarios_mapped.append(item)

    data = PaginatedUsuariosResponse(
        total=res["total"],
        page=res["page"],
        limit=res["limit"],
        pages=res["pages"],
        conteos_estado=res["conteos_estado"],
        usuarios=usuarios_mapped,
    )
    return {
        "success": True,
        "data": data.model_dump(mode="json"),
        "message": "Usuarios listados correctamente."
    }


@router.get("/usuarios/{usuario_id}", response_model=dict)
def get_usuario_detalle(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Detalle operativo de un usuario específico."""
    logger.info("[ADMIN] Admin %s solicitó detalle de usuario %s", admin.id, usuario_id)
    user = admin_service.obtener_usuario(db, usuario_id)
    user_data = _armar_usuario_detalle(db, user, hoy_argentina())

    return {
        "success": True,
        "data": user_data.model_dump(mode="json"),
        "message": "Usuario obtenido correctamente."
    }


@router.patch("/usuarios/{usuario_id}/estado", response_model=dict)
def cambiar_estado(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    body: CambiarEstadoRequest = ...,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Activar o desactivar cuenta de usuario."""
    logger.info("[ADMIN] Admin %s cambió estado de usuario %s a %s", admin.id, usuario_id, body.is_active)
    user = admin_service.cambiar_estado_usuario(db, usuario_id, is_active=body.is_active, admin_id=admin.id)
    _registrar_y_commit(
        db,
        admin,
        "reactivar" if body.is_active else "suspender",
        usuario=user,
    )

    user_data = _armar_usuario_detalle(db, user, hoy_argentina())

    return {
        "success": True,
        "data": user_data.model_dump(mode="json"),
        "message": "Estado del usuario actualizado correctamente."
    }


@router.post("/usuarios/{usuario_id}/reset-password", response_model=dict)
def reset_password(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Envía email de recuperación de contraseña."""
    logger.info("[ADMIN] Admin %s solicitó reset de contraseña para usuario %s", admin.id, usuario_id)
    user = admin_service.enviar_reset_password(db, usuario_id, settings.FRONTEND_URL)
    _registrar_y_commit(db, admin, "mail_contrasena", usuario=user)
    return {
        "success": True,
        "message": "Email de restablecimiento de contraseña enviado correctamente."
    }


@router.post("/usuarios/{usuario_id}/revocar-sesiones", response_model=dict)
def revocar_sesiones_usuario(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Fuerza cierre de todas las sesiones de un usuario."""
    logger.info("[ADMIN] Admin %s revocó sesiones del usuario %s", admin.id, usuario_id)
    user = admin_service.revocar_sesiones(db, usuario_id, admin_id=admin.id)
    _registrar_y_commit(db, admin, "cerrar_sesiones", usuario=user)
    return {
        "success": True,
        "message": "Sesiones del usuario revocadas exitosamente."
    }


@router.post("/usuarios/{usuario_id}/desconectar-wpp", response_model=dict)
def desconectar_whatsapp(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Desconecta WhatsApp de un usuario."""
    logger.info("[ADMIN] Admin %s desconectó WhatsApp para usuario %s", admin.id, usuario_id)
    user = admin_service.desconectar_wpp(db, usuario_id)
    _registrar_y_commit(db, admin, "desconectar_whatsapp", usuario=user)

    user_data = _armar_usuario_detalle(db, user, hoy_argentina())

    return {
        "success": True,
        "data": user_data.model_dump(mode="json"),
        "message": "WhatsApp desconectado correctamente."
    }


@router.patch("/usuarios/{usuario_id}/admin", response_model=dict)
def cambiar_rol_admin(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    body: CambiarRolAdminRequest = ...,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Promueve o revoca permisos de administrador a un usuario."""
    logger.info(
        "[ADMIN] Admin %s cambió rol admin de usuario %s a %s",
        admin.id,
        usuario_id,
        body.is_admin,
    )
    admin_service.obtener_usuario(db, usuario_id)
    if usuario_id == admin.id and not body.is_admin:
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

    reautenticacion_service.verificar_identidad_admin(
        db,
        admin,
        body.password_actual,
        body.google_credential,
        datetime.now(timezone.utc),
    )

    user = admin_service.cambiar_rol_admin(db, usuario_id, is_admin=body.is_admin, admin_id=admin.id)
    _registrar_y_commit(
        db,
        admin,
        "hacer_admin" if body.is_admin else "quitar_admin",
        usuario=user,
    )

    user_data = _armar_usuario_detalle(db, user, hoy_argentina())

    return {
        "success": True,
        "data": user_data.model_dump(mode="json"),
        "message": f"Usuario {'promovido a administrador' if body.is_admin else 'removido de administradores'} correctamente."
    }


@router.delete("/usuarios/{usuario_id}", response_model=dict)
def eliminar_usuario_admin(
    usuario_id: UUID = Path(..., description="Identificador único del usuario"),
    body: EliminarCuentaAdminRequest = ...,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Elimina permanentemente una cuenta de usuario y todos sus datos."""
    logger.info("[ADMIN] Admin %s solicitó eliminación de usuario %s", admin.id, usuario_id)
    admin_service.validar_eliminacion_admin(
        db,
        usuario_id,
        email_confirmacion=body.email_confirmacion,
        admin_id=admin.id,
    )
    reautenticacion_service.verificar_identidad_admin(
        db,
        admin,
        body.password_actual,
        body.google_credential,
        datetime.now(timezone.utc),
    )
    res = admin_service.eliminar_usuario_admin(
        db, usuario_id, email_confirmacion=body.email_confirmacion, admin_id=admin.id
    )
    _registrar_y_commit(db, admin, "eliminar_cuenta")
    return {
        "success": True,
        "message": "Usuario y todos sus datos fueron eliminados correctamente.",
        "data": res
    }


@router.get("/stats", response_model=dict)
def get_stats(
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Obtiene estadísticas generales para el panel de administración."""
    logger.info("[ADMIN] Admin %s solicitó estadísticas generales.", admin.id)
    stats = admin_service.obtener_estadisticas(
        db, ahora=ahora_utc(), hoy=hoy_argentina()
    )
    stats_data = AdminStatsResponse.model_validate(stats)
    return {
        "success": True,
        "data": stats_data.model_dump(mode="json"),
        "message": "Estadísticas obtenidas correctamente."
    }


@router.get("/registro", response_model=dict)
def get_registro_admin(
    page: int = Query(1, ge=1, description="Número de página"),
    limit: int = Query(20, ge=1, le=100, description="Cantidad de items por página"),
    accion: ACCION_REGISTRO_VALIDA | None = Query(None, description="Filtro por tipo de acción"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Lista las acciones registradas en el panel de administración."""
    res = admin_registro_service.listar_acciones(db, page=page, limit=limit, accion=accion)
    items_iso = [
        {
            **item,
            "fecha": (
                item["fecha"].isoformat()
                if isinstance(item["fecha"], datetime)
                else item["fecha"]
            ),
        }
        for item in res["items"]
    ]
    return {
        "success": True,
        "data": {
            "total": res["total"],
            "page": res["page"],
            "limit": res["limit"],
            "pages": res["pages"],
            "items": items_iso,
        },
        "message": "Registro obtenido.",
    }


@router.post("/feriados/refresh", response_model=dict)
async def refresh_feriados_admin(
    anio: int | None = Query(None, ge=2000, le=2100, description="Año a consultar feriados"),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Fuerza la recarga de feriados de un año desde la API externa y actualiza BD y caché."""
    from app.services.dias_habiles_service import recargar_feriados_anio

    target_anio = anio or hoy_argentina().year
    logger.info("[ADMIN] Admin %s solicitó refresh de feriados para año %s", admin.id, target_anio)

    res = await recargar_feriados_anio(target_anio)
    return {
        "success": res["success"],
        "data": res,
        "message": f"Feriados para el año {target_anio} actualizados. Se encontraron {res['cantidad']} feriados."
        if res["success"]
        else f"Error al actualizar feriados: {res['error']}",
    }


# ===========================================================================
# Endpoints de Auditoría y Catálogo Centralizado de Diseño (Admin Only)
# ===========================================================================
import json
import os
from pydantic import BaseModel, Field

DISENO_STORAGE_DIR = os.path.join(os.getcwd(), "media", "diseno")
os.makedirs(DISENO_STORAGE_DIR, exist_ok=True)
DECISIONES_FILE = os.path.join(DISENO_STORAGE_DIR, "decisiones.json")
ESPECIFICACIONES_FILE = os.path.join(DISENO_STORAGE_DIR, "especificaciones.json")


def _read_json_file(file_path: str, default_val: dict) -> dict:
    if not os.path.exists(file_path):
        return default_val
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("[ADMIN DISENO] Error leyendo archivo %s: %s", file_path, e)
        return default_val


def _write_json_file(file_path: str, data: dict):
    temp_path = f"{file_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, file_path)


class DisenoDecisionPayload(BaseModel):
    categoryId: str = Field(..., max_length=100)
    preferredVariantId: str | None = Field(None, max_length=250)
    preferredVariantName: str | None = Field(None, max_length=200)
    decisionState: Literal["pendiente", "revisada", "aprobada"] = "pendiente"
    notes: str | None = Field(None, max_length=2000)
    equivalentComponents: list[str] = Field(default_factory=list)
    customTokens: dict = Field(default_factory=dict)
    version: int | None = Field(None, ge=1, description="Versión para control de concurrencia optimista")


class DisenoBulkDecisionesPayload(BaseModel):
    decisiones: dict[str, DisenoDecisionPayload]


class DisenoEspecificacionPayload(BaseModel):
    familyId: str = Field(..., max_length=100)
    familyName: str = Field(..., max_length=150)
    targetTokens: dict = Field(default_factory=dict)
    stateGuidelines: dict = Field(default_factory=dict)
    accessibilityRules: list[str] = Field(default_factory=list)
    status: Literal["borrador", "revisada", "aprobada"] = "borrador"
    notes: str | None = Field(None, max_length=2000)
    version: int | None = Field(None, ge=1, description="Versión para control de concurrencia optimista")


@router.get("/diseno/decisiones", response_model=dict)
def get_diseno_decisiones(
    admin: Usuario = Depends(get_current_admin_user),
):
    """Retorna todas las decisiones y preferencias de diseño registradas por administradores."""
    logger.info("[ADMIN DISENO] Admin %s consultó decisiones de diseño.", admin.id)
    decisiones = _read_json_file(DECISIONES_FILE, default_val={})
    return {
        "success": True,
        "data": decisiones,
        "message": "Decisiones de diseño obtenidas correctamente."
    }


@router.post("/diseno/decisiones", response_model=dict)
def save_diseno_decision(
    payload: DisenoDecisionPayload,
    admin: Usuario = Depends(get_current_admin_user),
):
    """Guarda o actualiza una decisión de diseño para una categoría con control de concurrencia optimista."""
    logger.info("[ADMIN DISENO] Admin %s guardó decisión para categoría %s", admin.id, payload.categoryId)
    decisiones = _read_json_file(DECISIONES_FILE, default_val={})
    
    current_entry = decisiones.get(payload.categoryId)
    if current_entry:
        current_version = current_entry.get("version", 1)
        if payload.version is not None and payload.version < current_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "success": False,
                    "error": {
                        "code": "CONCURRENCY_CONFLICT",
                        "message": (
                            f"Otro administrador "
                            f"({current_entry.get('updatedBy', 'otro admin')}) "
                            f"modificó {payload.categoryId} "
                            f"a las {current_entry.get('updatedAt', '')}."
                        ),
                        "currentData": current_entry,
                    },
                },
            )
        next_version = current_version + 1
    else:
        next_version = 1
    
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    
    decision_data = payload.model_dump()
    decision_data["version"] = next_version
    decision_data["updatedAt"] = now_iso
    decision_data["updatedBy"] = admin.email
    
    decisiones[payload.categoryId] = decision_data
    _write_json_file(DECISIONES_FILE, decisiones)

    return {
        "success": True,
        "data": decision_data,
        "message": f"Decisión de diseño para {payload.categoryId} guardada correctamente (v{next_version})."
    }


@router.post("/diseno/decisiones/bulk", response_model=dict)
def save_diseno_decisiones_bulk(
    payload: DisenoBulkDecisionesPayload,
    admin: Usuario = Depends(get_current_admin_user),
):
    """Guarda en lote múltiples decisiones de diseño con incremento de versión."""
    logger.info("[ADMIN DISENO] Admin %s guardó decisiones en lote (%s categorías)", admin.id, len(payload.decisiones))
    decisiones = _read_json_file(DECISIONES_FILE, default_val={})
    
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()

    for cat_id, dec in payload.decisiones.items():
        current_entry = decisiones.get(cat_id)
        current_version = current_entry.get("version", 1) if current_entry else 0
        data = dec.model_dump()
        data["version"] = current_version + 1
        data["updatedAt"] = now_iso
        data["updatedBy"] = admin.email
        decisiones[cat_id] = data

    _write_json_file(DECISIONES_FILE, decisiones)

    return {
        "success": True,
        "data": decisiones,
        "message": f"{len(payload.decisiones)} decisiones de diseño guardadas correctamente."
    }


@router.get("/diseno/especificaciones", response_model=dict)
def get_diseno_especificaciones(
    admin: Usuario = Depends(get_current_admin_user),
):
    """Obtiene las especificaciones de diseño aprobadas y en borrador."""
    logger.info("[ADMIN DISENO] Admin %s consultó especificaciones de diseño.", admin.id)
    specs = _read_json_file(ESPECIFICACIONES_FILE, default_val={})
    return {
        "success": True,
        "data": specs,
        "message": "Especificaciones de diseño obtenidas correctamente."
    }


@router.post("/diseno/especificaciones", response_model=dict)
def save_diseno_especificacion(
    payload: DisenoEspecificacionPayload,
    admin: Usuario = Depends(get_current_admin_user),
):
    """Guarda o actualiza la especificación de diseño para una familia de componentes con control de concurrencia."""
    logger.info("[ADMIN DISENO] Admin %s guardó especificación para familia %s", admin.id, payload.familyId)
    specs = _read_json_file(ESPECIFICACIONES_FILE, default_val={})
    
    current_entry = specs.get(payload.familyId)
    if current_entry:
        current_version = current_entry.get("version", 1)
        if payload.version is not None and payload.version < current_version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "success": False,
                    "error": {
                        "code": "CONCURRENCY_CONFLICT",
                        "message": (
                            f"Otro administrador "
                            f"({current_entry.get('updatedBy', 'otro admin')}) "
                            f"modificó {payload.familyId} "
                            f"a las {current_entry.get('updatedAt', '')}."
                        ),
                        "currentData": current_entry,
                    },
                },
            )
        next_version = current_version + 1
    else:
        next_version = 1
    
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    
    spec_data = payload.model_dump()
    spec_data["version"] = next_version
    spec_data["updatedAt"] = now_iso
    spec_data["updatedBy"] = admin.email
    
    specs[payload.familyId] = spec_data
    _write_json_file(ESPECIFICACIONES_FILE, specs)

    return {
        "success": True,
        "data": spec_data,
        "message": f"Especificación para {payload.familyId} guardada correctamente (v{next_version})."
    }


@router.get("/scheduler", response_model=SchedulerResponse)
def listar_scheduler(
    request: Request,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Lista las tareas automáticas del catálogo con su última corrida, estado y próxima ejecución."""
    logger.info("[ADMIN] Admin %s consultó el estado del scheduler", admin.id)

    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_activo = bool(scheduler and getattr(scheduler, "running", False))

    ahora = datetime.now(timezone.utc)
    hace_24h = ahora - timedelta(hours=24)

    # Conteo de errores y omitidas en las últimas 24 horas agrupadas por job_id y resultado
    conteo_rows = (
        db.query(
            EjecucionJob.job_id,
            EjecucionJob.resultado,
            func.count(EjecucionJob.id),
        )
        .filter(EjecucionJob.inicio >= hace_24h)
        .group_by(EjecucionJob.job_id, EjecucionJob.resultado)
        .all()
    )
    conteo_map: dict[str, dict[str, int]] = {}
    for jid, res, cant in conteo_rows:
        if jid not in conteo_map:
            conteo_map[jid] = {}
        conteo_map[jid][res] = int(cant)

    jobs_res: list[JobSchedulerItem] = []

    for item in JOBS_CATALOGO:
        proxima_corrida = None
        if scheduler_activo:
            job_obj = scheduler.get_job(item.job_id)
            if job_obj and getattr(job_obj, "next_run_time", None):
                proxima_corrida = job_obj.next_run_time

        ultima = (
            db.query(EjecucionJob)
            .filter(EjecucionJob.job_id == item.job_id)
            .order_by(EjecucionJob.inicio.desc())
            .first()
        )
        ultima_corrida_item = None
        if ultima:
            ultima_corrida_item = UltimaCorridaJob(
                inicio=ultima.inicio,
                fin=ultima.fin,
                resultado=ultima.resultado,
                duracion_ms=ultima.duracion_ms,
                detalle=ultima.detalle,
            )

        estado = calcular_estado_job(
            job_id=item.job_id,
            periodo_esperado_minutos=item.periodo_esperado_minutos,
            db=db,
            ahora=ahora,
        )

        job_counts = conteo_map.get(item.job_id, {})
        errores_24h = job_counts.get("error", 0)
        omitidas_24h = job_counts.get("omitido", 0)

        jobs_res.append(
            JobSchedulerItem(
                job_id=item.job_id,
                nombre=item.nombre,
                que_hace=item.que_hace,
                periodo_esperado_minutos=item.periodo_esperado_minutos,
                estado=estado,
                puede_correr_ahora=item.puede_correr_ahora,
                proxima_corrida=proxima_corrida,
                ultima_corrida=ultima_corrida_item,
                errores_24h=errores_24h,
                omitidas_24h=omitidas_24h,
            )
        )

    return SchedulerResponse(
        scheduler_activo=scheduler_activo,
        jobs=jobs_res,
    )


@router.post("/scheduler/{job_id}/correr", status_code=status.HTTP_202_ACCEPTED)
def correr_job_scheduler(
    job_id: str = Path(..., description="ID de la tarea a ejecutar"),
    request: Request = ...,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Ejecuta una tarea automática de forma manual si está permitida."""
    logger.info("[ADMIN] Admin %s ejecutó manualmente el job %s", admin.id, job_id)

    if job_id not in JOBS_MANUALES_PERMITIDOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Esta tarea no se puede correr a mano.",
        )

    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler or not getattr(scheduler, "running", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Las tareas automáticas están apagadas en este servidor.",
        )

    job_obj = scheduler.get_job(job_id)
    if not job_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tarea no encontrada en el scheduler.",
        )

    ahora = datetime.now(timezone.utc)
    job_obj.modify(next_run_time=ahora)
    _registrar_y_commit(
        db,
        admin,
        "correr_tarea",
        detalle=JOBS_POR_ID[job_id].nombre,
    )

    return {"mensaje": "Se va a correr en unos segundos."}


def _resolver_periodo_monitor(
    dias: int | None,
    desde: date | None,
    hasta: date | None,
    hoy: date,
) -> tuple[int | None, date | None, date | None]:
    if dias is None and desde is None and hasta is None:
        return 1, None, None
    if dias is not None and (desde is not None or hasta is not None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elegí un período o un rango de fechas.",
        )
    if (desde is None) != (hasta is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elegí las dos fechas.",
        )
    if desde is not None and hasta is not None:
        if desde > hasta:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La fecha de inicio tiene que ser anterior a la de fin.",
            )
        if hasta > hoy:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La fecha de fin no puede ser posterior a hoy.",
            )
        if (hasta - desde).days > 89:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Elegí un rango de hasta 90 días.",
            )
        return None, desde, hasta

    if dias not in (1, 7, 30):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elegí 1, 7 o 30 días.",
        )
    return dias, None, None


@router.get("/monitor-wpp/resumen", response_model=MonitorWppResumenResponse)
def monitor_wpp_resumen(
    dias: int | None = Query(None, description="Ventana de días (1, 7 o 30)"),
    desde: date | None = Query(None, description="Fecha inicial del rango (YYYY-MM-DD)"),
    hasta: date | None = Query(None, description="Fecha final del rango (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Devuelve métricas agregadas de calidad, costos de IA, topes y salud para el monitor de WhatsApp."""
    hoy = hoy_argentina()
    dias_res, desde_res, hasta_res = _resolver_periodo_monitor(dias, desde, hasta, hoy)
    return monitor_wpp_service.obtener_resumen_monitor(
        db,
        dias=dias_res,
        hoy=hoy,
        desde=desde_res,
        hasta=hasta_res,
        ahora=ahora_utc(),
    )


@router.get("/monitor-wpp/conversaciones", response_model=PaginatedConversacionesMonitorResponse)
def monitor_wpp_conversaciones(
    dias: int | None = Query(None, description="Ventana de días (1, 7 o 30)"),
    desde: date | None = Query(None, description="Fecha inicial del rango (YYYY-MM-DD)"),
    hasta: date | None = Query(None, description="Fecha final del rango (YYYY-MM-DD)"),
    categoria: str = Query("", description="Filtro opcional por categoría"),
    pagina: int = Query(1, ge=1, description="Número de página"),
    db: Session = Depends(get_db),
    admin: Usuario = Depends(get_current_admin_user),
):
    """Lista conversaciones paginadas de a 50 con filtro opcional de categoría y protección de privacidad."""
    hoy = hoy_argentina()
    dias_res, desde_res, hasta_res = _resolver_periodo_monitor(dias, desde, hasta, hoy)
    return monitor_wpp_service.obtener_conversaciones_monitor(
        db,
        dias=dias_res,
        categoria=categoria,
        pagina=pagina,
        desde=desde_res,
        hasta=hasta_res,
    )



