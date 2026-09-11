from pydantic import BaseModel, Field


class ErrorFrontendCreate(BaseModel):
    """Esquema para recepción y validación de reportes de errores del frontend."""
    mensaje: str = Field(..., max_length=1000, description="Mensaje de error principal")
    stack: str | None = Field(default=None, max_length=5000, description="Stack trace del error")
    ruta: str | None = Field(default=None, max_length=500, description="Ruta/URL de la aplicación donde ocurrió")
    componente: str | None = Field(default=None, max_length=200, description="Nombre del componente o límite que atrapó el error")
    user_agent: str | None = Field(default=None, max_length=500, description="User Agent del navegador")
