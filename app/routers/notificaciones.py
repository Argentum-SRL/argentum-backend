import json
import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.core.auth import get_current_user, decodificar_access_token
from app.models.usuario import Usuario
from app.models.notificacion import Notificacion
from app.models.evento_actualizacion import EventoActualizacion
from app.schemas.notificacion import NotificacionRead
from app.schemas.configuracion_notificacion import ConfiguracionNotificacionRead, ConfiguracionNotificacionUpdate
from app.services import notificacion_service
from app.utils.texto import limpiar_mensaje_web

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])


@router.get("", response_model=List[NotificacionRead])
def listar_notificaciones(
    solo_no_leidas: bool = Query(default=False),
    limite: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    return notificacion_service.obtener_notificaciones(
        db=db,
        usuario_id=usuario.id,
        solo_no_leidas=solo_no_leidas,
        limite=limite,
        offset=offset,
    )


@router.get("/contador")
def obtener_contador_no_leidas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    count = notificacion_service.contar_no_leidas(db, usuario.id)
    return {"contador": count}


@router.put("/{id}/leer", response_model=NotificacionRead)
def marcar_notificacion_leida(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    try:
        return notificacion_service.marcar_leida(db, usuario.id, id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{id}/desleer", response_model=NotificacionRead)
def marcar_notificacion_no_leida(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    try:
        return notificacion_service.marcar_no_leida(db, usuario.id, id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{id}/silenciar", response_model=NotificacionRead)
def silenciar_notificacion(
    id: UUID,
    horas: int = Query(default=24, ge=1, le=168),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    try:
        return notificacion_service.silenciar_notificacion(db, usuario.id, id, horas)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{id}/archivar", response_model=NotificacionRead)
def archivar_notificacion(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    try:
        return notificacion_service.archivar_notificacion(db, usuario.id, id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{id}")
def eliminar_notificacion(
    id: UUID,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    try:
        notificacion_service.eliminar_notificacion(db, usuario.id, id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/leer-todas")
def marcar_todas_leidas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    notificacion_service.marcar_todas_leidas(db, usuario.id)
    return {"success": True}


@router.get("/configuracion", response_model=ConfiguracionNotificacionRead)
def obtener_configuracion(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    from app.core.politica_notificaciones import calcular_whatsapp_tipos_activos
    cfg = notificacion_service.obtener_configuracion(db, usuario.id)
    cfg_read = ConfiguracionNotificacionRead.model_validate(cfg)
    cfg_read.whatsapp_tipos_activos = calcular_whatsapp_tipos_activos()
    return cfg_read


@router.put("/configuracion", response_model=ConfiguracionNotificacionRead)
def actualizar_configuracion(
    data: ConfiguracionNotificacionUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    from app.core.politica_notificaciones import calcular_whatsapp_tipos_activos
    update_data = data.model_dump(exclude_unset=True)
    cfg = notificacion_service.actualizar_configuracion(db, usuario.id, update_data)
    cfg_read = ConfiguracionNotificacionRead.model_validate(cfg)
    cfg_read.whatsapp_tipos_activos = calcular_whatsapp_tipos_activos()
    return cfg_read


def autenticar_sse(request: Request) -> tuple[UUID, datetime | None]:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso requerido",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decodificar_access_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not isinstance(payload, dict) or payload.get("type") != "access" or not payload.get("sub"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        usuario_id = UUID(str(payload["sub"]))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso inválido o expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )

    iat_val = payload.get("iat")
    iat_dt = datetime.fromtimestamp(iat_val, tz=timezone.utc) if iat_val is not None else None

    db = SessionLocal()
    try:
        usuario = db.get(Usuario, usuario_id)
        if not usuario:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No encontramos una cuenta con esos datos.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        rev_at = usuario.tokens_revocados_at
        if rev_at and rev_at.tzinfo is None:
            rev_at = rev_at.replace(tzinfo=timezone.utc)
        if rev_at and iat_dt and iat_dt < rev_at:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sesión revocada. Por favor iniciá sesión nuevamente.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    finally:
        db.close()

    return usuario_id, iat_dt


@router.get("/sse")
async def sse_notificaciones(
    request: Request,
):
    """
    Endpoint SSE para streaming en vivo de notificaciones nuevas sin leer.
    Se autentica solo con la cookie httpOnly access_token.
    """
    usuario_id, iat_dt = autenticar_sse(request)

    async def event_generator():
        # Enviar primer mensaje de conexión exitosa
        yield "data: {\"event\": \"connected\"}\n\n"

        ultimo_check = datetime.now(timezone.utc)
        ultimo_check_eventos = datetime.now(timezone.utc)
        while True:
            await asyncio.sleep(5)

            # Creamos una sesión nueva para cada iteración para evitar cruces
            from app.core.database import SessionLocal
            db_session = SessionLocal()
            try:
                # Verificar si la sesión fue revocada durante el stream abierto
                if iat_dt:
                    u_live = db_session.get(Usuario, usuario_id)
                    if not u_live or (u_live.tokens_revocados_at and iat_dt < u_live.tokens_revocados_at):
                        yield "data: {\"event\": \"revoked\"}\n\n"
                        break

                # Buscar notificaciones nuevas creadas después de ultimo_check
                nuevas = (
                    db_session.query(Notificacion)
                    .filter(
                        Notificacion.usuario_id == usuario_id,
                        Notificacion.created_at > ultimo_check,
                        Notificacion.leida == False,
                        Notificacion.archivada == False,
                        (Notificacion.silenciada_hasta == None) | (Notificacion.silenciada_hasta < datetime.now(timezone.utc))
                    )
                    .all()
                )

                if nuevas:
                    ultimo_check = datetime.now(timezone.utc)
                    for n in nuevas:
                        data_json = {
                            "id": str(n.id),
                            "tipo": n.tipo.value,
                            "nivel": n.nivel.value,
                            "mensaje": limpiar_mensaje_web(n.mensaje),
                            "leida": n.leida,
                            "archivada": n.archivada,
                            "deep_link": n.deep_link,
                            "created_at": n.created_at.isoformat(),
                        }
                        yield f"data: {json.dumps(data_json)}\n\n"

                # Buscar eventos de actualización de datos creados después de ultimo_check_eventos
                nuevos_eventos = (
                    db_session.query(EventoActualizacion)
                    .filter(
                        EventoActualizacion.usuario_id == usuario_id,
                        EventoActualizacion.created_at > ultimo_check_eventos,
                    )
                    .order_by(EventoActualizacion.created_at.asc())
                    .all()
                )

                if nuevos_eventos:
                    ultimo_check_eventos = datetime.now(timezone.utc)
                    for ev in nuevos_eventos:
                        data_json = {
                            "event": "data_update",
                            "entidad": ev.entidad,
                        }
                        yield f"data: {json.dumps(data_json)}\n\n"

                if not nuevas and not nuevos_eventos:
                    # Heartbeat
                    yield ": ping\n\n"
            except Exception as e:
                logger.error("Error en generador SSE de notificaciones: %s", e)
            finally:
                db_session.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
