from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, status
from sqlalchemy.orm import Session
from uuid import UUID
import os
import logging
from decimal import Decimal

from app.core.database import get_db
from app.core.auth import get_current_admin_user
from app.models.usuario import Usuario
from app.models.importacion import ImportacionResumen, EstadoImportacion, TipoCorreccion
from app.services.importacion import procesar_resumen
from app.services.importacion.persistencia_service import (
    importar_transacciones_resumen,
    registrar_correccion,
    existe_transaccion_duplicada,
    calcular_import_hash
)
from app.services.importacion.schemas import TransaccionCruda
from app.schemas.importacion import (
    ProcesarResumenResponse,
    PreviewImportacionResponse,
    ConfirmarImportacionRequest,
    ConfirmarImportacionResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/importacion",
    tags=["importacion"],
    # Nota: El gate admin-only es temporal (candado de rollout, no un mecanismo de "importar para otro usuario")
    # y se espera relajar a cualquier usuario autenticado (Depends(get_current_user)) en el futuro.
    dependencies=[Depends(get_current_admin_user)],
)


@router.post("/procesar", response_model=dict)
def procesar_archivo(
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
):
    """
    Recibe el archivo PDF del resumen de tarjeta de crédito, lo procesa en memoria y guarda el resultado preliminar.
    
    El usuario logueado que sube el archivo es el dueño (usuario_id = current_user.id).
    """
    extension = archivo.filename.split(".")[-1].lower() if archivo.filename else ""
    if extension != "pdf":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "INVALID_FILE_TYPE",
                    "message": "El formato del archivo debe ser PDF."
                }
            }
        )
    
    # Validar tamaño máximo de 50MB (52428800 bytes)
    archivo.file.seek(0, os.SEEK_END)
    size = archivo.file.tell()
    archivo.file.seek(0)
    if size > 50 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": "El tamaño del archivo no puede superar los 50MB."
                }
            }
        )
    
    # Leer en memoria
    pdf_bytes = archivo.file.read()
    
    # Procesar usando el orquestador ya construido
    resultado = procesar_resumen(pdf_bytes)
    
    # Recolectar titulares detectados (tanto principal como adicionales)
    titulares = set()
    if resultado.titular_detectado:
        titulares.add(resultado.titular_detectado)
    for t in resultado.transacciones:
        if t.titular_seccion:
            titulares.add(t.titular_seccion)
    titulares_detectados = sorted(list(titulares))
    
    # Guardar en base de datos
    importacion = ImportacionResumen(
        usuario_id=current_user.id,
        admin_id=current_user.id,
        tarjeta_id=None,
        banco_detectado=resultado.banco,
        nombre_archivo=archivo.filename or "resumen.pdf",
        estado=EstadoImportacion.PENDIENTE_REVISION,
        capa_parser_usada=resultado.capa_usada,
        confianza_extraccion=Decimal(str(resultado.confianza)),
        periodo_desde=resultado.periodo_desde,
        periodo_hasta=resultado.periodo_hasta,
        titulares_detectados=titulares_detectados,
        titulares_seleccionados=None,
        transacciones_parseadas=[t.model_dump(mode="json") for t in resultado.transacciones],
        total_detectadas=len(resultado.transacciones),
        total_importadas=0,
        total_duplicadas=0,
        total_excluidas=0,
        mensaje_error=None
    )
    
    db.add(importacion)
    db.commit()
    db.refresh(importacion)
    
    data = {
        "importacion_id": importacion.id,
        "banco_detectado": importacion.banco_detectado,
        "estado": importacion.estado.value,
        "titulares_detectados": importacion.titulares_detectados,
        "total_detectadas": importacion.total_detectadas,
        "confianza": float(importacion.confianza_extraccion) if importacion.confianza_extraccion is not None else 0.0,
        "escalado": resultado.escalado
    }
    
    return {
        "success": True,
        "data": data,
        "message": "Archivo de resumen procesado con éxito."
    }


@router.get("/{importacion_id}/preview", response_model=dict)
def preview_importacion(
    importacion_id: UUID,
    tarjeta_id: UUID | None = None,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
):
    """
    Muestra la previsualización de las transacciones detectadas junto con indicación de posibles duplicados.
    
    Valida que la importación exista y pertenezca al usuario autenticado.
    """
    importacion = db.query(ImportacionResumen).filter(
        ImportacionResumen.id == importacion_id,
        ImportacionResumen.usuario_id == current_user.id
    ).first()
    
    if not importacion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "IMPORTACION_NOT_FOUND",
                    "message": "La importación no existe o no pertenece al usuario autenticado."
                }
            }
        )

    # Si se seleccionó una tarjeta en este paso, persistirla en la importación
    tarjeta_efectiva_id = tarjeta_id or importacion.tarjeta_id
    if tarjeta_id and importacion.tarjeta_id != tarjeta_id:
        importacion.tarjeta_id = tarjeta_id
        db.commit()
        db.refresh(importacion)
    
    transacciones_preview = []
    if importacion.transacciones_parseadas:
        for t_dict in importacion.transacciones_parseadas:
            t_obj = TransaccionCruda.model_validate(t_dict)
            
            # Calcular hash para detectar si es duplicada con la tarjeta real
            hash_val = calcular_import_hash(
                usuario_id=current_user.id,
                tarjeta_id=tarjeta_efectiva_id,
                fecha=t_obj.fecha,
                monto=t_obj.monto,
                descripcion=t_obj.descripcion,
                cuota_numero=t_obj.cuota_actual
            )
            
            posible_duplicado = existe_transaccion_duplicada(db, current_user.id, hash_val)
            
            transacciones_preview.append({
                "fecha": t_obj.fecha,
                "descripcion": t_obj.descripcion,
                "monto": t_obj.monto,
                "moneda": t_obj.moneda,
                "cuota_actual": t_obj.cuota_actual,
                "cuota_total": t_obj.cuota_total,
                "es_cargo_bancario": t_obj.es_cargo_bancario,
                "titular_seccion": t_obj.titular_seccion,
                "posible_duplicado": posible_duplicado
            })
            
    data = {
        "id": importacion.id,
        "usuario_id": importacion.usuario_id,
        "banco_detectado": importacion.banco_detectado,
        "estado": importacion.estado.value,
        "total_detectadas": importacion.total_detectadas,
        "periodo_desde": importacion.periodo_desde,
        "periodo_hasta": importacion.periodo_hasta,
        "titulares_detectados": importacion.titulares_detectados,
        "titulares_seleccionados": importacion.titulares_seleccionados,
        "transacciones": transacciones_preview
    }
    
    return {
        "success": True,
        "data": data,
        "message": "Previsualización de importación obtenida."
    }


@router.post("/{importacion_id}/confirmar", response_model=dict)
def confirmar_importacion(
    importacion_id: UUID,
    body: ConfirmarImportacionRequest,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_admin_user),
):
    """
    Confirma e importa en lote las transacciones seleccionadas aplicando categorías de forma personalizada.
    
    Evita doble confirmación y valida la propiedad de los datos.
    """
    importacion = db.query(ImportacionResumen).filter(
        ImportacionResumen.id == importacion_id,
        ImportacionResumen.usuario_id == current_user.id
    ).first()
    
    if not importacion:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "success": False,
                "error": {
                    "code": "IMPORTACION_NOT_FOUND",
                    "message": "La importación no existe o no pertenece al usuario autenticado."
                }
            }
        )
        
    if importacion.estado == EstadoImportacion.IMPORTADO:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "success": False,
                "error": {
                    "code": "IMPORTACION_YA_CONFIRMADA",
                    "message": "Esta importación ya ha sido confirmada previamente."
                }
            }
        )

    # Validar que la tarjeta pertenezca al usuario autenticado
    from app.models.tarjeta_credito import TarjetaCredito
    tarjeta = db.query(TarjetaCredito).filter(
        TarjetaCredito.id == body.tarjeta_id,
        TarjetaCredito.usuario_id == current_user.id
    ).first()
    if not tarjeta:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "TARJETA_INVALIDA",
                    "message": "La tarjeta seleccionada no existe o no pertenece a tu cuenta."
                }
            }
        )

    # Validar que la billetera en pesos pertenezca al usuario y sea de moneda ARS
    from app.models.billetera import Billetera, Moneda
    billetera_ars = db.query(Billetera).filter(
        Billetera.id == body.billetera_id,
        Billetera.usuario_id == current_user.id
    ).first()
    if not billetera_ars:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "BILLETERA_INVALIDA",
                    "message": "La billetera en pesos seleccionada no existe o no pertenece a tu cuenta."
                }
            }
        )
    if billetera_ars.moneda != Moneda.ARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "success": False,
                "error": {
                    "code": "MONEDA_BILLETERA_INVALIDA",
                    "message": "La billetera principal debe estar configurada en Pesos Argentinos (ARS)."
                }
            }
        )

    # Validar la billetera en dólares si fue proporcionada
    if body.billetera_usd_id:
        billetera_usd = db.query(Billetera).filter(
            Billetera.id == body.billetera_usd_id,
            Billetera.usuario_id == current_user.id
        ).first()
        if not billetera_usd:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "success": False,
                    "error": {
                        "code": "BILLETERA_USD_INVALIDA",
                        "message": "La billetera en dólares seleccionada no existe o no pertenece a tu cuenta."
                    }
                }
            )
        if billetera_usd.moneda != Moneda.USD:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "success": False,
                    "error": {
                        "code": "MONEDA_BILLETERA_USD_INVALIDA",
                        "message": "La billetera secundaria para gastos internacionales debe estar configurada en Dólares (USD)."
                    }
                }
            )

    # Comparar transacciones y registrar correcciones (best-effort)
    for idx, final_item in enumerate(body.transacciones_finales):
        if idx >= len(importacion.transacciones_parseadas):
            break
        
        # 1. Excluida
        if not final_item.incluir:
            try:
                registrar_correccion(
                    db=db,
                    importacion_id=importacion.id,
                    banco=importacion.banco_detectado,
                    capa_parser_usada=importacion.capa_parser_usada or "deterministic",
                    tipo_correccion=TipoCorreccion.TRANSACCION_EXCLUIDA
                )
            except Exception as e:
                logger.warning(f"Error registrando corrección TRANSACCION_EXCLUIDA: {e}")
        
        # 2. Categoría cambiada
        if final_item.incluir and final_item.categoria_id is not None:
            try:
                registrar_correccion(
                    db=db,
                    importacion_id=importacion.id,
                    banco=importacion.banco_detectado,
                    capa_parser_usada=importacion.capa_parser_usada or "deterministic",
                    tipo_correccion=TipoCorreccion.CATEGORIA_CAMBIADA
                )
            except Exception as e:
                logger.warning(f"Error registrando corrección CATEGORIA_CAMBIADA: {e}")

    # Filtrar transacciones a importar e inyectar categoria_id seleccionada
    transacciones_a_importar = []
    for idx, final_item in enumerate(body.transacciones_finales):
        if idx >= len(importacion.transacciones_parseadas):
            break
        if final_item.incluir:
            t_dict = importacion.transacciones_parseadas[idx]
            t_cruda = TransaccionCruda.model_validate(t_dict)
            t_cruda.categoria_id = final_item.categoria_id
            transacciones_a_importar.append(t_cruda)
            
    # Ejecutar importación real llamando al servicio existente
    resultado_importacion = importar_transacciones_resumen(
        db=db,
        usuario_id=current_user.id,
        tarjeta_id=body.tarjeta_id,
        importacion_id=importacion.id,
        billetera_id=body.billetera_id,
        billetera_usd_id=body.billetera_usd_id,
        transacciones_crudas=transacciones_a_importar
    )
                
    # Actualizar campos adicionales en importación
    total_excluidas = sum(1 for item in body.transacciones_finales if not item.incluir)
    
    # Volver a cargar para actualizar metadatos finales
    importacion = db.query(ImportacionResumen).filter(ImportacionResumen.id == importacion_id).first()
    if importacion:
        importacion.total_excluidas = total_excluidas
        if body.titulares_seleccionados is not None:
            importacion.titulares_seleccionados = body.titulares_seleccionados
        importacion.tarjeta_id = body.tarjeta_id
        db.commit()
        
    data = {
        "importadas": resultado_importacion["importadas"],
        "duplicadas": resultado_importacion["duplicadas"],
        "sin_billetera_usd": resultado_importacion["sin_billetera_usd"],
        "descartadas_manual": total_excluidas,
        "total_procesadas": resultado_importacion["total_procesadas"]
    }
    
    return {
        "success": True,
        "data": data,
        "message": "Importación finalizada con éxito."
    }
