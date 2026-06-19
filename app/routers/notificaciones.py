import json
import asyncio
import logging
from datetime import datetime
from uuid import UUID
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.auth import get_current_user, verificar_access_token
from app.models.usuario import Usuario
from app.models.notificacion import Notificacion, NivelNotificacion
from app.schemas.notificacion import NotificacionRead, NotificacionUpdate
from app.schemas.configuracion_notificacion import ConfiguracionNotificacionRead, ConfiguracionNotificacionUpdate
from app.services import notificacion_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])


@router.get("", response_model=List[NotificacionRead])
def listar_notificaciones(
    solo_no_leidas: bool = Query(default=False),
    incluir_archivadas: bool = Query(default=False),
    limite: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    return notificacion_service.obtener_notificaciones(
        db=db,
        usuario_id=usuario.id,
        solo_no_leidas=solo_no_leidas,
        incluir_archivadas=incluir_archivadas,
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


@router.post("/archivar-todas")
def archivar_todas(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    notificacion_service.archivar_todas(db, usuario.id)
    return {"success": True}


@router.get("/configuracion", response_model=ConfiguracionNotificacionRead)
def obtener_configuracion(
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    return notificacion_service.obtener_configuracion(db, usuario.id)


@router.put("/configuracion", response_model=ConfiguracionNotificacionRead)
def actualizar_configuracion(
    data: ConfiguracionNotificacionUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_current_user),
):
    update_data = data.model_dump(exclude_unset=True)
    return notificacion_service.actualizar_configuracion(db, usuario.id, update_data)


@router.get("/sse")
async def sse_notificaciones(
    request: Request,
    token: Optional[str] = Query(default=None),
):
    """
    Endpoint SSE para streaming en vivo de notificaciones nuevas sin leer.
    El cliente se autentica via cookie httpOnly (access_token) o via query param token.
    El query param se mantiene por compatibilidad con clientes existentes.
    """
    # Leer token desde query param primero, luego desde cookie httpOnly
    access_token = token or request.cookies.get("access_token")

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso requerido"
        )

    try:
        usuario_id_str = verificar_access_token(access_token)
        usuario_id = UUID(usuario_id_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de acceso inválido o expirado"
        )

    async def event_generator():
        # Enviar primer mensaje de conexión exitosa
        yield "data: {\"event\": \"connected\"}\n\n"

        ultimo_check = datetime.utcnow()
        while True:
            await asyncio.sleep(5)

            # Creamos una sesión nueva para cada iteración para evitar cruces
            from app.core.database import SessionLocal
            db_session = SessionLocal()
            try:
                # Buscar notificaciones nuevas creadas después de ultimo_check
                nuevas = (
                    db_session.query(Notificacion)
                    .filter(
                        Notificacion.usuario_id == usuario_id,
                        Notificacion.created_at > ultimo_check,
                        Notificacion.leida == False,
                        Notificacion.archivada == False,
                        (Notificacion.silenciada_hasta == None) | (Notificacion.silenciada_hasta < datetime.utcnow())
                    )
                    .all()
                )

                if nuevas:
                    ultimo_check = datetime.utcnow()
                    for n in nuevas:
                        data_json = {
                            "id": str(n.id),
                            "tipo": n.tipo.value,
                            "nivel": n.nivel.value,
                            "mensaje": n.mensaje,
                            "leida": n.leida,
                            "archivada": n.archivada,
                            "deep_link": n.deep_link,
                            "created_at": n.created_at.isoformat(),
                        }
                        yield f"data: {json.dumps(data_json)}\n\n"
                else:
                    # Heartbeat
                    yield ": ping\n\n"
            except Exception as e:
                logger.error("Error en generador SSE de notificaciones: %s", e)
            finally:
                db_session.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
