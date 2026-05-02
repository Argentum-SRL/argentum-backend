from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings

# ---------------------------------------------------------------------------
# APScheduler — limpieza periódica de refresh tokens expirados/revocados
# ---------------------------------------------------------------------------
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.database import SessionLocal
from app.core.auth import limpiar_tokens_expirados
from app.services.recurrente_service import procesar_recurrentes

# ---------------------------------------------------------------------------
# Seed de categorías y subcategorías
# ---------------------------------------------------------------------------
from scripts.seed_categorias import seed_categorias_subcategorias

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

scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(_job_limpiar_tokens, "interval", hours=6, id="limpiar_refresh_tokens")
scheduler.add_job(_job_procesar_recurrentes, "cron", hour=0, minute=5, id="procesar_recurrentes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Argentum API", version="1.0.0", lifespan=lifespan)


@app.on_event("startup")
def startup_seed():
    """Ejecuta el seed de categorías al iniciar el servidor."""
    seed_categorias_subcategorias()

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

from app.routers import auth, onboarding, usuarios, billeteras, transacciones, transferencias, recurrentes, categorias
from fastapi.staticfiles import StaticFiles
import os

app.include_router(auth.router)
app.include_router(onboarding.router)
app.include_router(usuarios.router)
app.include_router(billeteras.router)
app.include_router(transacciones.router)
app.include_router(transferencias.router)
app.include_router(recurrentes.router)
app.include_router(categorias.router)

# Servir archivos estáticos de media (Ignorado por git)
os.makedirs("media/fotos", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")


@app.get("/")
def root():
    return {"message": "Argentum API funcionando"}