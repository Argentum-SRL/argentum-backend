from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.core.config import settings
from app.routers import auth

# ---------------------------------------------------------------------------
# APScheduler — limpieza periódica de refresh tokens expirados/revocados
# ---------------------------------------------------------------------------
from apscheduler.schedulers.background import BackgroundScheduler
from app.core.database import SessionLocal
from app.core.auth import limpiar_tokens_expirados

def _job_limpiar_tokens():
    """Tarea programada: elimina refresh tokens viejos cada 6 horas."""
    db = SessionLocal()
    try:
        eliminados = limpiar_tokens_expirados(db)
        if eliminados:
            print(f"[scheduler] Refresh tokens eliminados: {eliminados}")
    finally:
        db.close()

scheduler = BackgroundScheduler(timezone="UTC")
scheduler.add_job(_job_limpiar_tokens, "interval", hours=6, id="limpiar_refresh_tokens")


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Argentum API", version="1.0.0", lifespan=lifespan)

_origins = [settings.FRONTEND_URL]
if settings.ENVIRONMENT == "development":
    _origins.append("http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)


@app.get("/")
def root():
    return {"message": "Argentum API funcionando"}