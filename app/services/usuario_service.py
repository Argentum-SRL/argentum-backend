import logging
import os
import shutil
import time
from uuid import UUID
from fastapi import HTTPException, UploadFile
from sqlalchemy import delete, select, update
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
from app.models.historial_perfil_financiero import HistorialPerfilFinanciero
from app.models.grupo_cuotas import GrupoCuotas
from app.models.transaccion_recurrente import TransaccionRecurrente
from app.models.transferencia_interna import TransferenciaInterna
from app.models.categoria_excluida import CategoriaExcluida
from app.models.configuracion_notificacion import ConfiguracionNotificacion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.movimiento_meta import MovimientoMeta
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.presupuesto_categoria import PresupuestoCategoria
from app.models.tarjeta_credito import TarjetaCredito
from app.models.importacion import ImportacionResumen, CorreccionImportacion
from app.core.security import get_password_hash, verify_password
from app.services import email_service, whatsapp_service
from app.services.storage_service import storage_service
from app.utils.telefono import normalizar_telefono_ar
from app.schemas.usuario import (
    EditarDatosPersonales,
    EditarEmail,
    EditarPassword,
    EditarTelefono,
    EditarCicloFinanciero,
    EditarMoneda,
)

FOTOS_DIR = "media/fotos"
logger = logging.getLogger(__name__)

def obtener_usuario_me(db: Session, usuario_id: UUID) -> Usuario:
    usuario = db.execute(select(Usuario).where(Usuario.id == usuario_id)).scalar_one_or_none()
    if not usuario:
        raise HTTPException(status_code=404, detail="No encontramos al usuario.")
    return usuario

def actualizar_datos_personales(
    db: Session, usuario: Usuario, datos: EditarDatosPersonales
) -> Usuario:
    if not datos.nombre.strip() or not datos.apellido.strip():
        raise HTTPException(status_code=400, detail="Nombre y apellido son obligatorios")
    
    if datos.fecha_nacimiento:
        from app.utils.fecha import hoy_argentina
        hoy = hoy_argentina()
        if datos.fecha_nacimiento > hoy:
            raise HTTPException(status_code=400, detail="La fecha de nacimiento no puede ser futura.")
        
        # Decisión de diseño: fecha_nacimiento es obligatoria en el flujo de onboarding (datos personales),
        # por lo que se valida siempre allí. En la actualización del perfil, es opcional en el esquema para permitir
        # actualizaciones parciales, pero si se provee una fecha, se aplica la validación de manera estricta.
        # Esto garantiza el cumplimiento legal sin romper la flexibilidad de la API.
        edad = hoy.year - datos.fecha_nacimiento.year - ((hoy.month, hoy.day) < (datos.fecha_nacimiento.month, datos.fecha_nacimiento.day))
        if edad < 18:
            raise HTTPException(status_code=400, detail="Tenés que ser mayor de 18 años para crear una cuenta en Argentum")
    
    usuario.nombre = datos.nombre.strip()
    usuario.apellido = datos.apellido.strip()
    usuario.fecha_nacimiento = datos.fecha_nacimiento
    usuario.sexo = datos.sexo
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
    
    email_limpio = datos.email_nuevo.strip().lower()
    if not email_limpio or "@" not in email_limpio or "." not in email_limpio.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Ingresá un formato de email válido.")
    
    if usuario.email and email_limpio == usuario.email.lower():
        raise HTTPException(status_code=400, detail="El email ingresado es igual a tu email actual.")
    
    if usuario.password_configurada and usuario.password_hash:
        if not datos.password_actual or not verify_password(datos.password_actual, usuario.password_hash):
            raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    
    result = db.execute(select(Usuario).where(Usuario.email == email_limpio, Usuario.id != usuario.id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ese email ya está siendo usado por otra cuenta.")
    
    usuario.email = email_limpio
    usuario.email_verificado = False
    db.commit()

    try:
        from app.services.notificacion_service import crear_notificacion
        from app.models.notificacion import TipoNotificacion, NivelNotificacion
        crear_notificacion(
            db=db,
            usuario_id=usuario.id,
            tipo=TipoNotificacion.CAMBIO_EMAIL,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Tu email fue actualizado. Si no fuiste vos, contactanos de inmediato.",
            canal_web=True,
            canal_whatsapp=True,
            canal_email=False,
        )
    except Exception:
        pass
    
    email_service.generar_y_enviar_verificacion_email(email_limpio)
    
    return {"confirmacion": "Email actualizado exitosamente. Verificá tu nueva casilla.", "requiere_verificacion_email": True}

def actualizar_password(
    db: Session, usuario: Usuario, datos: EditarPassword
) -> dict:
    if usuario.auth_provider == AuthProvider.GOOGLE:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta utiliza Google OAuth. La contraseña es administrada directamente por Google."
        )
    
    if usuario.password_configurada and usuario.password_hash:
        if not datos.password_actual:
            raise HTTPException(status_code=400, detail="La contraseña actual es obligatoria.")
        if not verify_password(datos.password_actual, usuario.password_hash):
            raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
        if datos.password_actual == datos.password_nueva:
            raise HTTPException(status_code=400, detail="La nueva contraseña no puede ser igual a la actual.")
    
    if datos.password_nueva != datos.password_nueva_confirmacion:
        raise HTTPException(status_code=400, detail="Las contraseñas no coinciden.")
    
    pw = datos.password_nueva
    if len(pw) < 8 or not any(c.isupper() for c in pw) or not any(c.islower() for c in pw) or not any(c.isdigit() for c in pw):
        raise HTTPException(
            status_code=400,
            detail="La contraseña debe tener al menos 8 caracteres, una mayúscula, una minúscula y un número."
        )
    
    usuario.password_hash = get_password_hash(pw)
    usuario.password_configurada = True
    db.commit()

    try:
        from app.services.notificacion_service import crear_notificacion
        from app.models.notificacion import TipoNotificacion, NivelNotificacion
        crear_notificacion(
            db=db,
            usuario_id=usuario.id,
            tipo=TipoNotificacion.CAMBIO_CONTRASENA,
            nivel=NivelNotificacion.CRITICA,
            mensaje="Tu contraseña fue actualizada. Si no fuiste vos, contactanos de inmediato.",
            canal_web=True,
            canal_whatsapp=True,
            canal_email=False,
        )
    except Exception:
        pass
    
    try:
        from app.services.notificacion_email_service import (
            enviar_email_notificacion,
            generar_email_cambio_contrasena,
        )
        if usuario.email:
            asunto, html, texto = generar_email_cambio_contrasena(
                usuario_nombre=usuario.nombre or "Usuario",
                dispositivo="Web browser"
            )
            enviar_email_notificacion(usuario.email, asunto, html, texto)
    except Exception as e:
        logger.error("Error al enviar email de cambio de contraseña: %s", e)
    
    return {"confirmacion": "Contraseña actualizada exitosamente"}

def actualizar_telefono(
    db: Session, usuario: Usuario, datos: EditarTelefono
) -> dict:
    tel_limpio = datos.telefono_nuevo.strip() if datos.telefono_nuevo else ""
    if not tel_limpio:
        raise HTTPException(status_code=400, detail="El número de teléfono es obligatorio.")
    
    tel_norm = normalizar_telefono_ar(tel_limpio)
    if len(tel_norm) < 8:
        raise HTTPException(status_code=400, detail="Ingresá un número de teléfono válido.")
    
    if usuario.auth_provider != AuthProvider.GOOGLE and usuario.password_configurada and usuario.password_hash:
        if not datos.password_actual or not verify_password(datos.password_actual, usuario.password_hash):
            raise HTTPException(status_code=400, detail="La contraseña actual no es correcta.")
    
    # Verificar que no esté en uso por otra cuenta
    result = db.execute(
        select(Usuario).where(
            (Usuario.telefono == tel_limpio) | (Usuario.telefono_normalizado == tel_norm),
            Usuario.id != usuario.id
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ese número de teléfono ya está registrado en otra cuenta.")
    
    usuario.telefono = tel_limpio
    usuario.telefono_normalizado = tel_norm
    usuario.telefono_verificado = False
    db.commit()
    
    codigo = whatsapp_service.generar_codigo()
    whatsapp_service.guardar_codigo(tel_limpio, codigo)
    whatsapp_service.enviar_mensaje_whatsapp(
        tel_limpio, 
        f"Tu código de verificación de Argentum es *{codigo}*. Expira en 10 minutos."
    )
    
    return {"confirmacion": "Teléfono actualizado. Se envió un código por WhatsApp.", "requiere_verificacion_telefono": True}

def actualizar_ciclo_financiero(
    db: Session, usuario: Usuario, datos: EditarCicloFinanciero
) -> Usuario:
    if datos.ciclo_tipo == CicloTipo.DIA_FIJO:
        try:
            dia = int(datos.ciclo_valor)
            if not (1 <= dia <= 31):
                raise ValueError()
        except ValueError:
            raise HTTPException(status_code=400, detail="El día fijo debe ser un número entre 1 y 31")
    elif datos.ciclo_tipo == CicloTipo.REGLA:
        from app.models.usuario import CicloRegla
        reglas_validas = {e.value for e in CicloRegla}
        if datos.ciclo_valor not in reglas_validas:
            raise HTTPException(status_code=400, detail="Regla de ciclo no válida")
    
    usuario.ciclo_tipo = datos.ciclo_tipo
    usuario.ciclo_valor = datos.ciclo_valor
    if datos.ciclo_ajuste_direccion is not None:
        usuario.ciclo_ajuste_direccion = datos.ciclo_ajuste_direccion
    elif usuario.ciclo_ajuste_direccion is None:
        from app.models.usuario import CicloAjusteDireccion
        usuario.ciclo_ajuste_direccion = CicloAjusteDireccion.ANTERIOR
    db.commit()
    db.refresh(usuario)
    return usuario

def actualizar_moneda(
    db: Session, usuario: Usuario, datos: EditarMoneda
) -> Usuario:
    usuario.moneda_principal = datos.moneda_principal
    usuario.moneda_secundaria_activa = datos.moneda_secundaria_activa
    if datos.tipo_dolar:
        tipo = datos.tipo_dolar.lower().strip()
        if tipo == "bolsa":
            tipo = "mep"
        valid_dolares = {"oficial", "blue", "tarjeta", "mep"}
        if tipo not in valid_dolares:
            raise HTTPException(status_code=400, detail="Tipo de dólar no válido.")
        usuario.tipo_dolar = tipo
    
    db.commit()
    db.refresh(usuario)
    return usuario

def actualizar_foto(
    db: Session, usuario: Usuario, archivo: UploadFile
) -> str:
    if usuario.auth_provider == AuthProvider.GOOGLE:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta utiliza Google OAuth. La foto de perfil se sincroniza desde tu cuenta de Google."
        )

    extension = archivo.filename.split(".")[-1].lower()
    if extension not in ["jpg", "jpeg", "png", "webp"]:
        raise HTTPException(status_code=400, detail="Formato de imagen no permitido (jpg, jpeg, png, webp)")
    
    archivo.file.seek(0, os.SEEK_END)
    size = archivo.file.tell()
    archivo.file.seek(0)
    if size > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="La imagen no debe superar los 5MB")
    
    content_type_map = {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp"
    }
    content_type = content_type_map.get(extension, "image/jpeg")

    file_bytes = archivo.file.read()
    filename = f"{usuario.id}.{extension}"
    nueva_url = None

    # 1. Intentar subir a Supabase Storage (persistente en la nube / CDN)
    if storage_service.esta_disponible():
        try:
            url_supabase = storage_service.subir_archivo(file_bytes, filename, content_type=content_type)
            nueva_url = f"{url_supabase}?v={int(time.time())}"
        except Exception as e:
            logger.warning(f"Error al subir a Supabase Storage, aplicando fallback local: {e}")

    # 2. Fallback a almacenamiento local en disco
    if not nueva_url:
        if not os.path.exists(FOTOS_DIR):
            os.makedirs(FOTOS_DIR, exist_ok=True)
        filepath = os.path.join(FOTOS_DIR, filename)
        with open(filepath, "wb") as buffer:
            buffer.write(file_bytes)
        nueva_url = f"/{FOTOS_DIR}/{filename}?v={int(time.time())}"

    # 3. Limpiar foto anterior
    if usuario.foto_url:
        if "supabase.co" in usuario.foto_url:
            old_name = usuario.foto_url.split("/")[-1].split("?")[0]
            if old_name != filename:
                storage_service.eliminar_archivo(old_name)
        elif not usuario.foto_url.startswith("http"):
            clean_url = usuario.foto_url.split("?")[0]
            old_path = os.path.join(os.getcwd(), clean_url.lstrip("/"))
            if os.path.exists(old_path):
                try:
                    os.remove(old_path)
                except Exception:
                    logger.warning("Error al eliminar archivo de foto anterior", exc_info=True)

    usuario.foto_url = nueva_url
    db.commit()
    db.refresh(usuario)
    
    return usuario.foto_url

def eliminar_foto(db: Session, usuario: Usuario) -> dict:
    if usuario.auth_provider == AuthProvider.GOOGLE:
        raise HTTPException(
            status_code=400,
            detail="Tu cuenta utiliza Google OAuth. La foto de perfil se sincroniza desde tu cuenta de Google."
        )

    if usuario.foto_url:
        if "supabase.co" in usuario.foto_url:
            storage_service.eliminar_archivo(usuario.foto_url)
        elif not usuario.foto_url.startswith("http"):
            clean_url = usuario.foto_url.split("?")[0]
            path = os.path.join(os.getcwd(), clean_url.lstrip("/"))
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    logger.warning("Error al eliminar archivo de foto", exc_info=True)
    
    usuario.foto_url = None
    db.commit()
    return {"confirmacion": "Foto eliminada correctamente"}

def eliminar_usuario(db: Session, usuario: Usuario) -> dict:
    usuario_id = usuario.id
    
    if usuario.foto_url:
        if "supabase.co" in usuario.foto_url:
            storage_service.eliminar_archivo(usuario.foto_url)
        elif not usuario.foto_url.startswith("http"):
            clean_url = usuario.foto_url.split("?")[0]
            path = os.path.join(os.getcwd(), clean_url.lstrip("/"))
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    logger.warning("Error al eliminar archivo de foto del usuario", exc_info=True)

    try:
        # Reasignar admin_id de importaciones de otros usuarios donde este usuario sea admin para evitar violación de FK
        db.execute(
            update(ImportacionResumen)
            .where(ImportacionResumen.admin_id == usuario_id)
            .where(ImportacionResumen.usuario_id != usuario_id)
            .values(admin_id=ImportacionResumen.usuario_id)
        )

        # 0. Romper referencia circular de transacciones → grupos_cuotas para permitir el borrado
        db.execute(update(Transaccion).where(Transaccion.usuario_id == usuario_id).values(grupo_cuotas_id=None))
        db.flush()

        # 1. Eliminar hijos sin usuario_id directo (dependencias de segundo nivel)
        # Cuotas (vía GrupoCuotas)
        db.execute(delete(Cuota).where(
            Cuota.grupo_id.in_(select(GrupoCuotas.id).where(GrupoCuotas.usuario_id == usuario_id))
        ))
        
        # Movimientos de Meta (vía Meta)
        db.execute(delete(MovimientoMeta).where(
            MovimientoMeta.meta_id.in_(select(Meta.id).where(Meta.usuario_id == usuario_id))
        ))
        
        # Periodos de Presupuesto (vía Presupuesto)
        db.execute(delete(PeriodoPresupuesto).where(
            PeriodoPresupuesto.presupuesto_id.in_(select(Presupuesto.id).where(Presupuesto.usuario_id == usuario_id))
        ))
        
        # Categorías de Presupuesto (vía Presupuesto)
        db.execute(delete(PresupuestoCategoria).where(
            PresupuestoCategoria.presupuesto_id.in_(select(Presupuesto.id).where(Presupuesto.usuario_id == usuario_id))
        ))
        
        # Historial de Suscripciones (vía Suscripcion)
        db.execute(delete(HistorialSuscripcion).where(
            HistorialSuscripcion.suscripcion_id.in_(select(Suscripcion.id).where(Suscripcion.usuario_id == usuario_id))
        ))

        # Correcciones de Importación (vía ImportacionResumen)
        db.execute(delete(CorreccionImportacion).where(
            CorreccionImportacion.importacion_id.in_(select(ImportacionResumen.id).where(ImportacionResumen.usuario_id == usuario_id))
        ))

        # 2. Modelos con usuario_id
        modelos_usuario = [
            ConversacionWpp, Notificacion, RefreshToken, Suscripcion,
            Presupuesto, Meta, GrupoCuotas, TransaccionRecurrente, 
            TransferenciaInterna, CategoriaExcluida, ConfiguracionNotificacion,
            Transaccion, ImportacionResumen, TarjetaCredito, Billetera,
            PerfilFinanciero, HistorialPerfilFinanciero
        ]
        
        for modelo in modelos_usuario:
            db.execute(delete(modelo).where(modelo.usuario_id == usuario_id))
        
        # 3. Modelos con creador_id (Categorías y Subcategorías personalizadas)
        modelos_creador = [Subcategoria, Categoria]
        for modelo in modelos_creador:
            db.execute(delete(modelo).where(modelo.creador_id == usuario_id))
        
        # 4. Finalmente el usuario
        db.delete(usuario)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Error al eliminar usuario %s", usuario_id)
        raise
    
    return {"confirmacion": "Usuario y todos sus datos eliminados correctamente"}

def crear_billeteras_efectivo_default(db: Session, usuario_id: UUID) -> None:
    """
    Crea las 2 billeteras de efectivo default (ARS y USD) para un usuario si no existen.
    """
    # Verificar si ya existen para no duplicar
    existentes = db.execute(
        select(Billetera.moneda).where(
            Billetera.usuario_id == usuario_id, 
            Billetera.es_efectivo == True
        )
    ).scalars().all()

    if Moneda.ARS not in existentes:
        b_ars = Billetera(
            usuario_id=usuario_id,
            nombre="Efectivo ARS",
            moneda=Moneda.ARS,
            saldo_inicial=0,
            saldo_actual=0,
            es_principal=False,
            es_efectivo=True
        )
        db.add(b_ars)

    if Moneda.USD not in existentes:
        b_usd = Billetera(
            usuario_id=usuario_id,
            nombre="Efectivo USD",
            moneda=Moneda.USD,
            saldo_inicial=0,
            saldo_actual=0,
            es_principal=False,
            es_efectivo=True
        )
        db.add(b_usd)
    
    db.commit()
