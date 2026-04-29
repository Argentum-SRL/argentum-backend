from pydantic import BaseModel
from app.schemas.usuario import UsuarioRead

class GoogleLoginRequest(BaseModel):
    token: str

class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UsuarioRead
