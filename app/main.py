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
from slowapi.errors import RateLimitExceeded

from app.core.limiter import limiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

from app.core.logging_config import configurar_structlog
configurar_structlog()

from app.core.config import settings

logger = logging.getLogger(__name__)
# Reducir ruido en la consola: ocultar mensajes informativos de APScheduler
# y de accesos HTTP para que veas solo lo esencial.
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# APScheduler — limpieza periódica de refresh tokens expirados/revocados
# ---------------------------------------------------------------------------
import time
from app.core.database import SessionLocal, db_query_duration_var
from app.core.auth import limpiar_tokens_expirados
from app.core.job_lock import intentar_tomar_lock_job, liberar_lock_job
import structlog

struct_logger = structlog.get_logger(__name__)

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
    _job_proyeccion_negativa,
)

# ---------------------------------------------------------------------------
# Inicialización automática de Base de Datos
# ---------------------------------------------------------------------------
from scripts.init_full_db import init_full_db

class SecurityHeadersMiddleware:
    """
    Middleware ASGI puro para agregar headers de seguridad HTTP en todas las respuestas.
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                security_headers = [
                    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                ]
                existing_keys = {h[0].lower() for h in headers}
                for k, v in security_headers:
                    if k not in existing_keys:
                        headers.append((k, v))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


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

        # Para las rutas bajo /importacion, permitimos hasta 100 segundos
        timeout = self.timeout
        if path.startswith("/importacion") or "/importacion" in path:
            timeout = 100.0

        try:
            with anyio.fail_after(timeout):
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


class RequestTimingMiddleware:
    """
    Middleware ASGI puro para medir la duración total de cada solicitud HTTP
    y el tiempo total acumulado en queries de base de datos (db_duration_ms).
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")

        # Inicializar el acumulador mutable de tiempo de DB por request
        db_container = [0.0]
        db_query_duration_var.set(db_container)
        t_start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            # Excluir /notificaciones/sse de la métrica de duration_ms / slow_request (es un stream long-lived)
            if path != "/notificaciones/sse":
                duration_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                db_duration_ms = round(db_container[0], 2)

                if duration_ms > 800.0:
                    struct_logger.warning(
                        "slow_request",
                        method=method,
                        path=path,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        db_duration_ms=db_duration_ms,
                    )
                else:
                    struct_logger.info(
                        "request_completed",
                        method=method,
                        path=path,
                        status_code=status_code,
                        duration_ms=duration_ms,
                        db_duration_ms=db_duration_ms,
                    )

def _job_limpiar_tokens():
    """Tarea programada: elimina refresh tokens viejos cada 6 horas."""
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_limpiar_tokens"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_limpiar_tokens",
            )
            return
        lock_adquirido = True
        eliminados = limpiar_tokens_expirados(db)
        if eliminados:
            logger.info("Refresh tokens eliminados: %s", eliminados)
    except Exception:
        logger.exception("Error en job limpiar_tokens")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_limpiar_tokens")
        db.close()


def _job_procesar_recurrentes():
    """Tarea programada: genera transacciones recurrentes una vez al día."""
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_procesar_recurrentes"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_procesar_recurrentes",
            )
            return
        lock_adquirido = True
        generadas = procesar_recurrentes(db)
        if generadas:
            logger.info("Transacciones recurrentes generadas: %s", generadas)
    except Exception:
        logger.exception("Error en job procesar_recurrentes")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_procesar_recurrentes")
        db.close()


def _job_vencimientos_tarjetas():
    """Tarea programada: genera transacciones de vencimiento de tarjetas una vez al día."""
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_vencimientos_tarjetas"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_vencimientos_tarjetas",
            )
            return
        lock_adquirido = True
        procesar_vencimientos_tarjetas(db)
        logger.info("Job de vencimientos de tarjetas ejecutado.")
    except Exception:
        logger.exception("Error en job vencimientos_tarjetas")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_vencimientos_tarjetas")
        db.close()


def _job_renovar_presupuestos():
    """Tarea programada: renueva presupuestos automáticamente una vez al día."""
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_renovar_presupuestos"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_renovar_presupuestos",
            )
            return
        lock_adquirido = True
        renovar_presupuestos(db)
        logger.info("Job de renovación de presupuestos ejecutado.")
    except Exception:
        logger.exception("Error en job renovar_presupuestos")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_renovar_presupuestos")
        db.close()


def _job_cobros_suscripciones():
    """Tarea programada: procesa cobros de suscripciones automáticamente una vez al día."""
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_cobros_suscripciones"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_cobros_suscripciones",
            )
            return
        lock_adquirido = True
        procesar_cobros_suscripciones(db)
        logger.info("Job de cobros de suscripciones ejecutado.")
    except Exception:
        logger.exception("Error en job cobros_suscripciones")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_cobros_suscripciones")
        db.close()


def job_notificaciones_cuotas():
    _job_notificaciones_cuotas(SessionLocal)


def job_notificaciones_presupuestos():
    _job_notificaciones_presupuestos(SessionLocal)


def job_notificaciones_suscripciones():
    _job_notificaciones_suscripciones(SessionLocal)


def job_notificaciones_inactividad():
    _job_notificaciones_inactividad(SessionLocal)


def job_entrega_whatsapp_batched():
    _job_entrega_whatsapp_batched(SessionLocal)


def job_resumen_cierre_ciclo():
    _job_resumen_cierre_ciclo(SessionLocal)


def job_resumen_semanal():
    _job_resumen_semanal(SessionLocal)


def job_proyeccion_negativa():
    _job_proyeccion_negativa(SessionLocal)


def _job_actualizar_perfiles():
    """Tarea programada: Recalcula el perfil financiero de todos los usuarios activos a las 02:00 UTC."""
    from sqlalchemy import select
    from app.models.usuario import Usuario, EstadoUsuario
    from app.services.perfil_financiero_service import calcular_y_persistir_perfil

    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_actualizar_perfiles"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_actualizar_perfiles",
            )
            return
        lock_adquirido = True
        usuarios_activos = db.execute(
            select(Usuario.id).where(Usuario.estado == EstadoUsuario.ACTIVO)
        ).scalars().all()

        cant_actualizados = 0

        for uid in usuarios_activos:
            db_sub = SessionLocal()
            try:
                calcular_y_persistir_perfil(db_sub, uid)
                cant_actualizados += 1
            except Exception as e:
                logger.error(f"Error actualizando perfil {uid}: {e}")
            finally:
                db_sub.close()

        logger.info(f"Se actualizaron {cant_actualizados} perfiles financieros en el job programado.")
    except Exception:
        logger.exception("Error en job _job_actualizar_perfiles")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_actualizar_perfiles")
        db.close()


async def _job_refresh_feriados():
    """Tarea programada diaria: verifica y asegura que los feriados estén persistidos y cacheados."""
    from app.services.dias_habiles_service import asegurar_feriados_cargados
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_refresh_feriados"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_refresh_feriados",
            )
            return
        lock_adquirido = True
        await asegurar_feriados_cargados()
    except Exception:
        logger.exception("Error en job _job_refresh_feriados")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_refresh_feriados")
        db.close()


def _job_guardar_cotizaciones_diarias():
    """Tarea programada diaria: persiste cotizaciones de cierre de mercado a las 21:00 UTC (18:00 ART)."""
    from app.services.dolar_service import guardar_cotizaciones_del_dia
    db = SessionLocal()
    lock_adquirido = False
    try:
        if not intentar_tomar_lock_job(db, "_job_guardar_cotizaciones_diarias"):
            struct_logger.info(
                "Job omitido: ya se está ejecutando en otra instancia",
                job="_job_guardar_cotizaciones_diarias",
            )
            return
        lock_adquirido = True
        guardadas = guardar_cotizaciones_del_dia(db)
        logger.info("Job guardar_cotizaciones_diarias ejecutado exitosamente (%d guardadas).", len(guardadas))
    except Exception:
        logger.exception("Error en job _job_guardar_cotizaciones_diarias")
    finally:
        if lock_adquirido:
            liberar_lock_job(db, "_job_guardar_cotizaciones_diarias")
        db.close()


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
        hour=0,
        minute=20,
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
        job_proyeccion_negativa,
        "cron",
        hour=7,
        minute=25,
        id="proyeccion_negativa",
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
    scheduler.add_job(
        _job_actualizar_perfiles,
        "cron",
        hour=2,
        minute=0,
        id="actualizar_perfiles_financieros",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_refresh_feriados,
        "cron",
        hour=3,
        minute=0,
        id="refresh_feriados_argentina",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.add_job(
        _job_guardar_cotizaciones_diarias,
        "cron",
        hour=21,
        minute=0,
        id="guardar_cotizaciones_diarias",
        misfire_grace_time=300,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()

    # Pre-cargar feriados argentinos en cache y BD
    from app.services.dias_habiles_service import obtener_feriados_argentina
    from app.utils.fecha import hoy_argentina
    anio_actual = hoy_argentina().year
    for anio in (anio_actual, anio_actual + 1):
        try:
            feriados = await obtener_feriados_argentina(anio)
            if not feriados:
                logger.error(
                    f"Fallo en la precarga de feriados para el año {anio}: lista vacía o API no disponible. "
                    f"El ciclo usará fechas nominales como fallback para {anio}."
                )
            else:
                logger.info(f"Feriados argentinos precargados para el año {anio} ({len(feriados)} feriados).")
        except Exception as e:
            logger.error(f"Excepción al precargar feriados para el año {anio}: {e}")

    logger.info("Backend listo: servidor y tareas automáticas activas.")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Argentum API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if settings.ENVIRONMENT == "production" else "/docs",
    redoc_url=None if settings.ENVIRONMENT == "production" else "/redoc",
    openapi_url=None if settings.ENVIRONMENT == "production" else "/openapi.json",
)
app.state.limiter = limiter

# Starlette apila middlewares en orden inverso: el último en add_middleware
# es el outermost y ejecuta PRIMERO para cada request.
# Orden correcto de registro (el último = el primero en ejecutar):
#   1. GZipMiddleware
#   2. TimeoutMiddleware
#   3. RequestTimingMiddleware
#   4. SecurityHeadersMiddleware
#   5. CORSMiddleware  ← último registrado = primero en ejecutar
_origins = [settings.FRONTEND_URL]
if settings.ENVIRONMENT == "development":
    _origins.extend([
        "http://localhost:5173", "http://localhost:3000", "http://localhost:5174",
        "http://127.0.0.1:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5174"
    ])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestTimingMiddleware)
app.add_middleware(TimeoutMiddleware, timeout=30.0)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "success": False,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Demasiadas solicitudes. Por favor, esperá un momento antes de volver a intentar.",
            }
        }
    )


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



from app.routers import auth, onboarding, usuarios, billeteras, transacciones, transferencias, recurrentes, categorias, dashboard, tarjetas, presupuestos, suscripciones, metas, notificaciones, tools, grupos_cuotas, whatsapp_ia, admin, perfil_financiero, importacion

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
app.include_router(perfil_financiero.router, prefix="/api/v1", tags=["perfil"])
app.include_router(importacion.router)

# Servir archivos estáticos de media (Ignorado por git)
os.makedirs("media/fotos", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")
app.mount("/api/media", StaticFiles(directory="media"), name="api_media")


@app.get("/")
def root():
    return {"message": "Argentum API funcionando"}
