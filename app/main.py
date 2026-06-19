import logging
import asyncio
import os
import sys
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor

import anyio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

from app.core.config import settings

logger = logging.getLogger(__name__)
# Reducir ruido en la consola: ocultar mensajes informativos de APScheduler
# y de accesos HTTP para que veas solo lo esencial.
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# APScheduler — limpieza periódica de refresh tokens expirados/revocados
# ---------------------------------------------------------------------------
from app.core.database import SessionLocal
from app.core.auth import limpiar_tokens_expirados
from app.services.recurrente_service import procesar_recurrentes
from app.services.vencimiento_tarjeta_service import procesar_vencimientos_tarjetas
from app.services.presupuesto_service import renovar_presupuestos
from app.services.cobro_suscripcion_service import procesar_cobros_suscripciones
from app.services.notificacion_scheduler_service import (
    _job_notificaciones_cuotas,
    _job_notificaciones_presupuestos,
    _job_notificaciones_suscripciones,
    _job_notificaciones_inactividad,
    _job_entrega_whatsapp_batched,
    _job_resumen_cierre_ciclo,
    _job_resumen_semanal,
)

# ---------------------------------------------------------------------------
# Inicialización automática de Base de Datos
# ---------------------------------------------------------------------------
from scripts.init_full_db import init_full_db

class TimeoutMiddleware:
    """
    Middleware ASGI puro (no BaseHTTPMiddleware) para evitar el bug de Starlette
    donde BaseHTTPMiddleware interrumpe el pipeline antes de que CORSMiddleware
    pueda agregar los headers Access-Control-Allow-Origin.
    """
    def __init__(self, app: ASGIApp, timeout: float = 30.0):
        self.app = app
        self.timeout = timeout

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Excluir el endpoint SSE del timeout — es una conexión de larga duración
        path = scope.get("path", "")
        if path == "/notificaciones/sse":
            await self.app(scope, receive, send)
            return

        try:
            with anyio.fail_after(self.timeout):
                await self.app(scope, receive, send)
        except TimeoutError:
            if scope["type"] == "http":
                await send({
                    "type": "http.response.start",
                    "status": 504,
                    "headers": [
                        [b"content-type", b"application/json"],
                    ],
                })
                await send({
                    "type": "http.response.body",
                    "body": b'{"success":false,"error":{"code":"TIMEOUT","message":"La solicitud tard\xc3\xb3 demasiado. Intent\xc3\xa1 de nuevo."}}',
                })
        except asyncio.CancelledError:
            # El cliente se desconectó — salir limpiamente sin loguear como error
            raise

async def _job_limpiar_tokens():
    """Tarea programada: elimina refresh tokens viejos cada 6 horas."""
    db = SessionLocal()
    try:
        eliminados = limpiar_tokens_expirados(db)
        if eliminados:
            logger.info("Refresh tokens eliminados: %s", eliminados)
    except Exception:
        logger.exception("Error en job limpiar_tokens")
    finally:
        db.close()


async def _job_procesar_recurrentes():
    """Tarea programada: genera transacciones recurrentes una vez al día."""
    db = SessionLocal()
    try:
        generadas = procesar_recurrentes(db)
        if generadas:
            logger.info("Transacciones recurrentes generadas: %s", generadas)
    except Exception:
        logger.exception("Error en job procesar_recurrentes")
    finally:
        db.close()


async def _job_vencimientos_tarjetas():
    """Tarea programada: genera transacciones de vencimiento de tarjetas una vez al día."""
    db = SessionLocal()
    try:
        procesar_vencimientos_tarjetas(db)
        logger.info("Job de vencimientos de tarjetas ejecutado.")
    except Exception:
        logger.exception("Error en job vencimientos_tarjetas")
    finally:
        db.close()


async def _job_renovar_presupuestos():
    """Tarea programada: renueva presupuestos automáticamente una vez al día."""
    db = SessionLocal()
    try:
        renovar_presupuestos(db)
        logger.info("Job de renovación de presupuestos ejecutado.")
    except Exception:
        logger.exception("Error en job renovar_presupuestos")
    finally:
        db.close()


async def _job_cobros_suscripciones():
    """Tarea programada: procesa cobros de suscripciones automáticamente una vez al día."""
    db = SessionLocal()
    try:
        procesar_cobros_suscripciones(db)
        logger.info("Job de cobros de suscripciones ejecutado.")
    except Exception:
        logger.exception("Error en job cobros_suscripciones")
    finally:
        db.close()


async def job_notificaciones_cuotas():
    _job_notificaciones_cuotas(SessionLocal)


async def job_notificaciones_presupuestos():
    _job_notificaciones_presupuestos(SessionLocal)


async def job_notificaciones_suscripciones():
    _job_notificaciones_suscripciones(SessionLocal)


async def job_notificaciones_inactividad():
    _job_notificaciones_inactividad(SessionLocal)


async def job_entrega_whatsapp_batched():
    _job_entrega_whatsapp_batched(SessionLocal)


async def job_resumen_cierre_ciclo():
    _job_resumen_cierre_ciclo(SessionLocal)


async def job_resumen_semanal():
    _job_resumen_semanal(SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear el scheduler y registrar jobs aquí para evitar que se
    # añadan en tiempo de import (evita duplicados con --reload)
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        await loop.run_in_executor(pool, init_full_db)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _job_limpiar_tokens,
        "interval",
        hours=6,
        id="limpiar_refresh_tokens",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_procesar_recurrentes,
        "cron",
        hour=0,
        minute=5,
        id="procesar_recurrentes",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_vencimientos_tarjetas,
        "cron",
        hour=6,
        minute=0,
        id="vencimientos_tarjetas",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_renovar_presupuestos,
        "cron",
        hour=0,
        minute=15,
        id="renovar_presupuestos",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_cobros_suscripciones,
        "cron",
        hour=6,
        minute=30,
        id="cobros_suscripciones",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_notificaciones_cuotas,
        "cron",
        hour=7,
        minute=0,
        id="notificaciones_cuotas",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_notificaciones_presupuestos,
        "cron",
        hour=7,
        minute=5,
        id="notificaciones_presupuestos",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_notificaciones_suscripciones,
        "cron",
        hour=7,
        minute=10,
        id="notificaciones_suscripciones",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_notificaciones_inactividad,
        "cron",
        hour=7,
        minute=15,
        id="notificaciones_inactividad",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_resumen_cierre_ciclo,
        "cron",
        hour=7,
        minute=20,
        id="resumen_cierre_ciclo",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_resumen_semanal,
        "cron",
        day_of_week="mon",
        hour=8,
        minute=0,
        id="resumen_semanal",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        job_entrega_whatsapp_batched,
        "cron",
        minute="*",
        id="entrega_whatsapp_batched",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    # Pre-cargar feriados argentinos en cache para
    # que calcular_fecha_cobro_sync funcione sin async
    try:
        from app.services.dias_habiles_service import obtener_feriados_argentina
        from datetime import date as _date
        anio_actual = _date.today().year
        await obtener_feriados_argentina(anio_actual)
        await obtener_feriados_argentina(anio_actual + 1)
        logger.info("Feriados argentinos pre-cargados en cache.")
    except Exception:
        logger.warning("No se pudieron pre-cargar feriados. "
                      "El ciclo usará fechas nominales como fallback.")
    logger.info("Backend listo: servidor y tareas automáticas activas.")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Argentum API", version="1.0.0", lifespan=lifespan)

# Starlette apila middlewares en orden inverso: el último en add_middleware
# es el outermost y ejecuta PRIMERO para cada request.
# Orden correcto de registro (el último = el primero en ejecutar):
#   1. GZipMiddleware
#   2. TimeoutMiddleware
#   3. CORSMiddleware  ← último registrado = primero en ejecutar
_origins = [settings.FRONTEND_URL]
if settings.ENVIRONMENT == "development":
    _origins.extend(["http://localhost:5173", "http://localhost:3000", "http://localhost:5174"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(TimeoutMiddleware, timeout=30.0)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": "Hay campos con errores. Revisá los datos e intentá de nuevo."}
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Intercepta HTTPException de FastAPI/Starlette y las reformatea
    al contrato de error estandarizado del proyecto.
    Si el detail ya es un dict con el formato correcto, lo usa directamente.
    """
    # Si el detail ya tiene el formato correcto del proyecto, usarlo
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail
        )

    # Si es un string simple, formatearlo
    mensaje = exc.detail if isinstance(exc.detail, str) else "Error en la solicitud."
    codigo = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "UNPROCESSABLE",
    }.get(exc.status_code, "HTTP_ERROR")

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": codigo,
                "message": mensaje,
            }
        }
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception(
        "Error no manejado",
        extra={
            "path": request.url.path,
            "method": request.method,
        }
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Ocurrió un error inesperado. Intentá de nuevo."
            }
        }
    )



from app.routers import auth, onboarding, usuarios, billeteras, transacciones, transferencias, recurrentes, categorias, dashboard, tarjetas, presupuestos, suscripciones, metas, notificaciones, tools, grupos_cuotas, whatsapp_ia, admin

app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(usuarios.router)
app.include_router(billeteras.router)
app.include_router(tarjetas.router, prefix="/tarjetas", tags=["tarjetas"])
app.include_router(transacciones.router)
app.include_router(transferencias.router)
app.include_router(recurrentes.router)
app.include_router(categorias.router)
app.include_router(dashboard.router)
app.include_router(presupuestos.router, prefix="/presupuestos")
app.include_router(suscripciones.router)
app.include_router(metas.router, prefix="/goals", tags=["goals"])
app.include_router(notificaciones.router)
app.include_router(tools.router, prefix="/api/v1/tools")
app.include_router(grupos_cuotas.router)
app.include_router(whatsapp_ia.router, prefix="/api")
app.include_router(admin.router, prefix="/v1", tags=["admin"])

# Servir archivos estáticos de media (Ignorado por git)
os.makedirs("media/fotos", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")


@app.get("/")
def root():
    return {"message": "Argentum API funcionando"}
