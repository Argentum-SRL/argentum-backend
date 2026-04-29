import re
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import select
from pydantic import BaseModel, field_validator

import uuid
from app.core.database import get_db
from app.core.security import verify_password, get_password_hash, create_access_token, create_refresh_token
from app.models.usuario import Usuario, AuthProvider
from app.schemas.auth import GoogleLoginRequest, LoginResponse
from app.services.auth_service import verify_google_token

router = APIRouter(prefix="/auth", tags=["auth"])

class RegisterRequest(BaseModel):
    name: str
    apellido: str
    email: str
    telefono: str
    password: str

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('La contraseña debe tener al menos 8 caracteres.')
        if not re.search(r'[A-Z]', v):
            raise ValueError('La contraseña debe incluir al menos una letra mayúscula.')
        if not re.search(r'[a-z]', v):
            raise ValueError('La contraseña debe incluir al menos una letra minúscula.')
        if not re.search(r'[0-9]', v):
            raise ValueError('La contraseña debe incluir al menos un número.')
        return v

class LoginRequest(BaseModel):
    email: str
    password: str

@router.post("/register", response_model=dict)
def register(user_in: RegisterRequest, db: Session = Depends(get_db)):
    # Verificar email único
    existing_email = db.execute(select(Usuario).where(Usuario.email == user_in.email)).scalar_one_or_none()
    if existing_email:
        raise HTTPException(status_code=409, detail="Ese mail ya está registrado en otra cuenta.")

    # Verificar teléfono único
    existing_phone = db.execute(select(Usuario).where(Usuario.telefono == user_in.telefono)).scalar_one_or_none()
    if existing_phone:
        raise HTTPException(status_code=409, detail="Ese número de teléfono ya está registrado en otra cuenta.")

    new_user = Usuario(
        nombre=user_in.name,
        apellido=user_in.apellido,
        email=user_in.email,
        telefono=user_in.telefono,
        password_hash=get_password_hash(user_in.password),
        auth_provider=AuthProvider.EMAIL,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    access_token = create_access_token(data={"sub": str(new_user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/login", response_model=dict)
def login(user_in: LoginRequest, db: Session = Depends(get_db)):
    stmt = select(Usuario).where(Usuario.email == user_in.email)
    user = db.execute(stmt).scalar_one_or_none()
    
    if not user or not user.password_hash or not verify_password(user_in.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
        
    access_token = create_access_token(data={"sub": str(user.id)})
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/google", response_model=LoginResponse)
def login_google(request: GoogleLoginRequest, db: Session = Depends(get_db)):
    # 1 y 2. Verificar el token con la API de Google
    token_info = verify_google_token(request.token)
    email = token_info.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="El token de Google no contiene un email válido")
        
    # 3. Si el email ya existe en la BD
    user = db.execute(select(Usuario).where(Usuario.email == email)).scalar_one_or_none()
    
    if not user:
        # 4. Si no existe: crear usuario nuevo
        nombre = token_info.get("given_name", "")
        apellido = token_info.get("family_name", "")
        if not nombre and "name" in token_info:
            parts = token_info["name"].split(" ", 1)
            nombre = parts[0]
            apellido = parts[1] if len(parts) > 1 else ""
            
        foto_url = token_info.get("picture")
        
        # Telefono dummy requerido por la BD (max 20 chars)
        telefono_dummy = "g_" + str(uuid.uuid4())[:18]
        
        user = Usuario(
            nombre=nombre,
            apellido=apellido,
            email=email,
            telefono=telefono_dummy,
            foto_url=foto_url,
            auth_provider=AuthProvider.GOOGLE,
            onboarding_completo=False
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        
    # 5. Devolver access_token, refresh_token y el objeto usuario
    access_token = create_access_token(data={"sub": str(user.id)})
    refresh_token = create_refresh_token(data={"sub": str(user.id)})
    
    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        user=user
    )
