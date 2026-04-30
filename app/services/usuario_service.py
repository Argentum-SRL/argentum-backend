import os
import shutil
from uuid import UUID
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.usuario import Usuario, AuthProvider, CicloTipo, Moneda
from app.models.billetera import Billetera
from app.models.transaccion import Transaccion
from app.models.cuota import Cuota
from app.models.meta import Meta
from app.models.presupuesto import Presupuesto
from app.models.suscripcion import Suscripcion
from app.models.notificacion import Notificacion
from app.models.conversacion_wpp import ConversacionWpp
from app.models.refresh_token import RefreshToken
from app.models.perfil_financiero import PerfilFinanciero
from app.core.security import get_password_hash, verify_password
from app.services import email_service, whatsapp_service
from app.schemas.usuario import (
    EditarDatosPersonales,
    EditarEmail,
    EditarPassword,
    EditarTelefono,
    EditarCicloFinanciero,
    EditarMoneda,
)

FOTOS_DIR = "media/fotos"

def obtener_usuario_me(db: Session, usuario_id: UUID) -> Usuario:
    usuario = db.execute(select(Usuario).where(Usuario.id == usuario_id)).scalar_one_or_none()
    if not usuario:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return usuario

def actualizar_datos_personales(
    db: Session, usuario: Usuario, datos: EditarDatosPersonales
) -> Usuario:
    if not datos.nombre.strip() or not datos.apellido.strip():
        raise HTTPException(status_code=400, detail="Nombre y apellido son obligatorios")
    
    usuario.nombre = datos.nombre
    usuario.apellido = datos.apellido
    db.commit()
    db.refresh(usuario)
    return usuario

def actualizar_email(
    db: Session, usuario: Usuario, datos: EditarEmail
) -> dict:
    if usuario.auth_provider == AuthProvider.GOOGLE:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta usa Google para autenticarse. El email lo gestiona Google directamente."
        )
    
    if not usuario.password_hash or not verify_password(datos.password_actual, usuario.password_hash):
        raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")
    
    result = db.execute(select(Usuario).where(Usuario.email == datos.email_nuevo))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El email ya está en uso")
    
    usuario.email = datos.email_nuevo
    usuario.email_verificado = False
    db.commit()
    
    email_service.generar_y_enviar_verificacion_email(datos.email_nuevo)
    
    return {"confirmacion": "Email actualizado. Se envió un código de verificación.", "requiere_verificacion_email": True}

def actualizar_password(
    db: Session, usuario: Usuario, datos: EditarPassword
) -> dict:
    if usuario.auth_provider == AuthProvider.GOOGLE:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta usa Google para autenticarse. Las contrasenas las gestiona Google directamente."
        )
    
    if usuario.password_hash:
        if not datos.password_actual:
            raise HTTPException(status_code=400, detail="La contraseña actual es obligatoria")
        if not verify_password(datos.password_actual, usuario.password_hash):
            raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")
    
    if datos.password_nueva != datos.password_nueva_confirmacion:
        raise HTTPException(status_code=400, detail="Las contraseñas no coinciden")
    
    pw = datos.password_nueva
    if len(pw) < 8 or not any(c.isupper() for c in pw) or not any(c.islower() for c in pw) or not any(c.isdigit() for c in pw):
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 8 caracteres, una mayúscula, una minúscula y un número"
        )
    
    usuario.password_hash = get_password_hash(pw)
    usuario.password_configurada = True
    db.commit()
    
    return {"confirmacion": "Contraseña actualizada exitosamente"}

def actualizar_telefono(
    db: Session, usuario: Usuario, datos: EditarTelefono
) -> dict:
    if usuario.auth_provider != AuthProvider.GOOGLE:
        if not usuario.password_hash or not verify_password(datos.password_actual, usuario.password_hash):
            raise HTTPException(status_code=400, detail="Contraseña actual incorrecta")
    
    result = db.execute(select(Usuario).where(Usuario.telefono == datos.telefono_nuevo))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="El teléfono ya está en uso")
    
    usuario.telefono = datos.telefono_nuevo
    usuario.telefono_verificado = False
    db.commit()
    
    codigo = whatsapp_service.generar_codigo()
    whatsapp_service.guardar_codigo(datos.telefono_nuevo, codigo)
    whatsapp_service.enviar_mensaje_whatsapp(
        datos.telefono_nuevo, 
        f"Tu código de verificación de Argentum es: {codigo}"
    )
    
    return {"confirmacion": "Teléfono actualizado. Se envió un código por WhatsApp.", "requiere_verificacion_telefono": True}

def actualizar_ciclo_financiero(
    db: Session, usuario: Usuario, datos: EditarCicloFinanciero
) -> Usuario:
    if datos.ciclo_tipo == CicloTipo.DIA_FIJO:
        try:
            dia = int(datos.ciclo_valor)
            if not (1 <= dia <= 28):
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=400, detail="El día fijo debe ser un número entre 1 y 28")
    elif datos.ciclo_tipo == CicloTipo.REGLA:
        reglas_validas = [
            'primer_lunes', 'primer_martes', 'primer_miercoles', 'primer_jueves', 'primer_viernes',
            'ultimo_lunes', 'ultimo_martes', 'ultimo_miercoles', 'ultimo_jueves', 'ultimo_viernes'
        ]
        if datos.ciclo_valor not in reglas_validas:
            raise HTTPException(status_code=400, detail="Regla de ciclo no válida")
    
    usuario.ciclo_tipo = datos.ciclo_tipo
    usuario.ciclo_valor = datos.ciclo_valor
    db.commit()
    db.refresh(usuario)
    return usuario

def actualizar_moneda(
    db: Session, usuario: Usuario, datos: EditarMoneda
) -> Usuario:
    usuario.moneda_principal = datos.moneda_principal
    usuario.moneda_secundaria_activa = datos.moneda_secundaria_activa
    if datos.tipo_dolar:
        usuario.tipo_dolar = datos.tipo_dolar
    
    db.commit()
    db.refresh(usuario)
    return usuario

def actualizar_foto(
    db: Session, usuario: Usuario, archivo: UploadFile
) -> str:
    extension = archivo.filename.split(".")[-1].lower()
    if extension not in ["jpg", "jpeg", "png", "webp"]:
        raise HTTPException(status_code=400, detail="Formato de imagen no permitido (jpg, jpeg, png, webp)")
    
    archivo.file.seek(0, os.SEEK_END)
    size = archivo.file.tell()
    archivo.file.seek(0)
    if size > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen no debe superar los 5MB")
    
    if not os.path.exists(FOTOS_DIR):
        os.makedirs(FOTOS_DIR, exist_ok=True)
    
    if usuario.foto_url and not usuario.foto_url.startswith("http"):
        old_path = os.path.join(os.getcwd(), usuario.foto_url.lstrip("/"))
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

    filename = f"{usuario.id}.{extension}"
    filepath = os.path.join(FOTOS_DIR, filename)
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(archivo.file, buffer)
    
    usuario.foto_url = f"/{FOTOS_DIR}/{filename}"
    db.commit()
    db.refresh(usuario)
    
    return usuario.foto_url

def eliminar_foto(db: Session, usuario: Usuario) -> dict:
    if usuario.foto_url and not usuario.foto_url.startswith("http"):
        path = os.path.join(os.getcwd(), usuario.foto_url.lstrip("/"))
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    
    usuario.foto_url = None
    db.commit()
    return {"confirmacion": "Foto eliminada correctamente"}

def eliminar_usuario(db: Session, usuario: Usuario) -> dict:
    usuario_id = usuario.id
    
    if usuario.foto_url and not usuario.foto_url.startswith("http"):
        path = os.path.join(os.getcwd(), usuario.foto_url.lstrip("/"))
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    modelos = [
        ConversacionWpp, Notificacion, RefreshToken, Suscripcion,
        Presupuesto, Meta, Cuota, Transaccion, Billetera, PerfilFinanciero
    ]
    
    for modelo in modelos:
        db.execute(delete(modelo).where(modelo.usuario_id == usuario_id))
    
    db.delete(usuario)
    db.commit()
    
    return {"confirmacion": "Usuario y todos sus datos eliminados correctamente"}
