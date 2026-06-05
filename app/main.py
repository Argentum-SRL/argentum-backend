from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
import logging

# Reducir ruido en la consola: ocultar mensajes informativos de APScheduler
# y de accesos HTTP para que veas solo lo esencial.
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('uvicorn.access').setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# APScheduler — limpieza periódica de refresh tokens expirados/revocados
# ---------------------------------------------------------------------------
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.database import SessionLocal
from app.core.auth import limpiar_tokens_expirados
from app.services.recurrente_service import procesar_recurrentes
from app.services.vencimiento_tarjeta_service import procesar_vencimientos_tarjetas
from app.services.presupuesto_service import renovar_presupuestos
from app.services.cobro_suscripcion_service import procesar_cobros_suscripciones

# ---------------------------------------------------------------------------
# Inicialización automática de Base de Datos
# ---------------------------------------------------------------------------
from scripts.init_full_db import init_full_db

def _job_limpiar_tokens():
    """Tarea programada: elimina refresh tokens viejos cada 6 horas."""
    db = SessionLocal()
    try:
        eliminados = limpiar_tokens_expirados(db)
        if eliminados:
            print(f"[scheduler] Refresh tokens eliminados: {eliminados}")
    finally:
        db.close()

def _job_procesar_recurrentes():
    """Tarea programada: genera transacciones recurrentes una vez al día."""
    db = SessionLocal()
    try:
        generadas = procesar_recurrentes(db)
        if generadas:
            print(f"[scheduler] Transacciones recurrentes generadas: {generadas}")
    finally:
        db.close()

def _job_vencimientos_tarjetas():
    """Tarea programada: genera transacciones de vencimiento de tarjetas una vez al día."""
    db = SessionLocal()
    try:
        procesar_vencimientos_tarjetas(db)
        print("[scheduler] Job de vencimientos de tarjetas ejecutado.")
    finally:
        db.close()

def _job_renovar_presupuestos():
    """Tarea programada: renueva presupuestos automáticamente una vez al día."""
    db = SessionLocal()
    try:
        renovar_presupuestos(db)
        print("[scheduler] Job de renovación de presupuestos ejecutado.")
    finally:
        db.close()

def _job_cobros_suscripciones():
    """Tarea programada: procesa cobros de suscripciones automáticamente una vez al día."""
    db = SessionLocal()
    try:
        procesar_cobros_suscripciones(db)
        print("[scheduler] Job de cobros de suscripciones ejecutado.")
    finally:
        db.close()

def _job_actualizar_ipc():
    """Tarea programada: actualiza el IPC automáticamente de forma diaria."""
    db = SessionLocal()
    try:
        from app.services.tools_service import get_current_ipc
        print("[scheduler] Iniciando actualización automática de IPC...")
        ipc_cache = get_current_ipc(db)
        print(f"[scheduler] IPC actualizado/verificado: {ipc_cache.valor_mensual}% ({ipc_cache.fecha_dato}) - Fuente: {ipc_cache.fuente}")
    except Exception as e:
        print(f"[scheduler] Error al actualizar IPC automáticamente: {str(e)}")
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Crear el scheduler y registrar jobs aquí para evitar que se
    # añadan en tiempo de import (evita duplicados con --reload)
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(_job_limpiar_tokens, "interval", hours=6, id="limpiar_refresh_tokens")
    scheduler.add_job(_job_procesar_recurrentes, "cron", hour=0, minute=5, id="procesar_recurrentes")
    scheduler.add_job(_job_vencimientos_tarjetas, "cron", hour=6, minute=0, id="vencimientos_tarjetas")
    scheduler.add_job(_job_renovar_presupuestos, "cron", hour=0, minute=5, id="renovar_presupuestos")
    scheduler.add_job(_job_cobros_suscripciones, "cron", hour=6, minute=5, id="cobros_suscripciones")
    scheduler.add_job(_job_actualizar_ipc, "cron", hour=13, minute=0, id="actualizar_ipc")
    scheduler.start()
    # Mensaje corto y claro para la consola
    print("Backend listo: servidor y tareas automáticas activas.")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Argentum API", version="1.0.0", lifespan=lifespan)


@app.on_event("startup")
def startup_init_db():
    """
    Inicializa automáticamente la base de datos al arrancar el servidor.
    Detecta modelos, crea tablas y ejecuta seeds iniciales.
    """
    init_full_db()

_origins = [settings.FRONTEND_URL]
if settings.ENVIRONMENT == "development":
    _origins.extend(["http://localhost:5173", "http://localhost:5174"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routers import auth, onboarding, usuarios, billeteras, transacciones, transferencias, recurrentes, categorias, dashboard, tarjetas, presupuestos, suscripciones, metas, tools
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
import os

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
app.include_router(tools.router, prefix="/api/v1/tools", tags=["tools"])

# Servir archivos estáticos de media con fallback para fotos de perfil no encontradas (evita 404s en consola)
os.makedirs("media/fotos", exist_ok=True)

@app.get("/media/fotos/{filename}")
async def get_foto_perfil(filename: str):
    filepath = os.path.join("media", "fotos", filename)
    if os.path.exists(filepath):
        return FileResponse(filepath)
    
    # SVG elegante de avatar por defecto en caso de no encontrarse el archivo localmente
    default_avatar_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="128" height="128">'
        '<rect width="24" height="24" fill="#0f172a"/>'
        '<circle cx="12" cy="8.5" r="4" fill="#64748b"/>'
        '<path d="M12 14c-4.42 0-8 2.24-8 5v1h16v-1c0-2.76-3.58-5-8-5z" fill="#64748b"/>'
        '</svg>'
    )
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0"
    }
    return Response(content=default_avatar_svg, media_type="image/svg+xml", headers=headers)

app.mount("/media", StaticFiles(directory="media"), name="media")


@app.get("/")
def root():
    return {"message": "Argentum API funcionando"}