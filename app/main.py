from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Argentum API", version="1.0.0")

from app.core.config import settings

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

@app.get("/")
def root():
    return {"message": "Argentum API funcionando"}