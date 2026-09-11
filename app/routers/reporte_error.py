import structlog
from fastapi import APIRouter, Depends, Request, Response, status

from app.core.auth import get_optional_user
from app.core.limiter import limiter
from app.models.usuario import Usuario
from app.schemas.reporte_error import ErrorFrontendCreate

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/reporte-error", tags=["reporte-error"])


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def reportar_error_frontend(
    request: Request,
    reporte: ErrorFrontendCreate,
    current_user: Usuario | None = Depends(get_optional_user),
):
    """
    Recibe reportes de errores atrapados en el frontend (límites de error).
    Registra una línea estructurada con el evento error_frontend sin persistir en la base de datos.
    """
    logger.error(
        "error_frontend",
        mensaje=reporte.mensaje,
        stack=reporte.stack,
        ruta=reporte.ruta,
        componente=reporte.componente,
        user_agent=reporte.user_agent or request.headers.get("user-agent"),
        usuario_id=str(current_user.id) if current_user else None,
        client_ip=request.client.host if request.client else None,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
