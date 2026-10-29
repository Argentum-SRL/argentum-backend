import os
from decimal import Decimal
from pydantic import model_validator
from pydantic_settings import BaseSettings

from app.core.entorno import es_servidor_produccion, es_base_local


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str

    # Supabase (API / client)
    SUPABASE_URL: str = ""
    SUPABASE_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str = ""

    # Auth
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    TURNSTILE_SECRET_KEY: str = ""

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini-2024-07-18"
    IA_PRESUPUESTO_DIARIO_USD: float = 1.0
    IA_MAX_MENSAJES_DIA_USUARIO: int = 150
    IA_MAX_ARCHIVOS_DIA_USUARIO: int = 20

    # Dolar API
    DOLAR_API_BASE_URL: str = "https://dolarapi.com"
    DOLAR_API_TIMEOUT_SECONDS: int = 10
    PERCEPCION_EXTERIOR_PORCENTAJE: int = 30
    # Percepción sobre consumos en el exterior pagados en pesos; dólar tarjeta ≈ oficial × 1,3 (El Destape 04/08/2026, iProfesional 22/06/2026)

    # Meta WhatsApp Cloud API
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""
    WHATSAPP_APP_SECRET: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_BOT_NUMBER: str = ""
    WHATSAPP_PLANTILLAS_ACTIVAS: str = "resumen_ciclo,alerta_cambio_email,alerta_cambio_contrasena"
    WHATSAPP_MAX_SALIENTES_DIA: int = 150
    WHATSAPP_CUPO_GRATIS_MES: int = 1000
    WHATSAPP_TARIFA_SERVICIO_USD: Decimal = Decimal("0.023")  # tarifa por mensaje de servicio cobrado en Argentina; vista en la facturación de Meta el 09/10/2026

    # Google
    GOOGLE_CLIENT_ID: str = ""

    # URLs
    FRONTEND_URL: str = "http://localhost:5173"
    BACKEND_URL: str = "http://localhost:8000"

    # SMTP / Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "Argentum <srlargentum@gmail.com>"

    # Resend API
    RESEND_API_KEY: str = ""

    # Reporte de uso
    REPORTE_USO_EMAILS: str = ""

    # Environment
    ENVIRONMENT: str = "development"
    DEBUG: bool = False
    ENABLE_SCHEDULER: bool = True
    ES_MODO_PC: bool = True

    @model_validator(mode="after")
    def validate_modo_pc(self):
        if es_servidor_produccion():
            self.ES_MODO_PC = False
        else:
            self.ES_MODO_PC = True
            is_local = es_base_local(self.DATABASE_URL)
            produccion_confirmada = os.environ.get("ARGENTUM_PRODUCCION_CONFIRMADA") == "1"
            if not is_local and not produccion_confirmada:
                raise ValueError(
                    r"En esta PC la app usa solo la copia local. Para tocar producción usá scripts\local\con_produccion.py."
                )
            self.WHATSAPP_ACCESS_TOKEN = ""
            self.RESEND_API_KEY = ""
            self.SMTP_PASSWORD = ""
            self.ENABLE_SCHEDULER = False
        return self

    @model_validator(mode="after")
    def validate_urls(self):
        url = self.FRONTEND_URL.strip().rstrip("/") if self.FRONTEND_URL else ""
        if not url:
            raise ValueError("FRONTEND_URL no puede estar vacía. Debe configurarse una URL válida.")
        if self.ENVIRONMENT != "development" and ("localhost" in url or "127.0.0.1" in url):
            raise ValueError(
                f"FRONTEND_URL no puede apuntar a localhost/127.0.0.1 en entorno '{self.ENVIRONMENT}'. "
                "Configure la variable de entorno FRONTEND_URL con el dominio público del frontend."
            )
        self.FRONTEND_URL = url
        if self.BACKEND_URL:
            self.BACKEND_URL = self.BACKEND_URL.strip().rstrip("/")
        return self

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
