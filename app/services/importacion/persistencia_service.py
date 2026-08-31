import re
import hashlib
from decimal import Decimal
from datetime import date
from dateutil.relativedelta import relativedelta
from sqlalchemy import select
from sqlalchemy.orm import Session

from uuid import UUID as PyUUID

from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    MetodoPago,
    OrigenTransaccion,
    EstadoVerificacionTransaccion
)
from app.models.grupo_cuotas import GrupoCuotas, EstadoGrupoCuotas
from app.models.cuota import Cuota
from app.models.importacion import (
    ImportacionResumen,
    EstadoImportacion,
    CorreccionImportacion,
    TipoCorreccion
)
from app.services import cuotas_service
from app.services.importacion.schemas import TransaccionCruda


def normalizar_descripcion(descripcion: str) -> str:
    """
    Normaliza la descripción de una transacción para facilitar su comparación y cálculo de hash.
    
    Pasa el texto a minúsculas, colapsa los espacios en blanco múltiples y remueve
    sufijos de cuotas del estilo ' 3/12' o ' 3 de 12' al final de la cadena.
    
    Parámetros:
        descripcion (str): La descripción original del comercio o movimiento.
        
    Retorna:
        str: La descripción normalizada y limpia.
    """
    if not descripcion:
        return ""
    
    # Convertir a minúsculas
    desc = descripcion.lower()
    
    # Reemplazar múltiples espacios o tabulaciones por uno solo y limpiar extremos
    desc = re.sub(r"\s+", " ", desc).strip()
    
    # Remover patrones de cuota al final de la descripción
    # Ejemplos: " 3/12", " 03/12", " 3 de 12", " 03 de 12"
    desc = re.sub(r"\s+\d+/\d+$", "", desc)
    desc = re.sub(r"\s+\d+\s+de\s+\d+$", "", desc)
    
    return desc.strip()


def calcular_import_hash(
    usuario_id: any,
    tarjeta_id: any,
    fecha: date,
    monto: Decimal,
    descripcion: str,
    cuota_numero: int | None
) -> str:
    """
    Calcula un código único (hash SHA-256) para identificar y evitar la duplicación de una transacción.
    
    Este código se genera combinando información clave del movimiento. Si se vuelve a importar
    una transacción con los mismos datos básicos, generará el mismo código, permitiendo detectarla.
    Usa el valor absoluto del monto para mantener la consistencia en caso de reversiones o devoluciones.
    
    Parámetros:
        usuario_id (any): El identificador único del usuario dueño del movimiento.
        tarjeta_id (any): El identificador de la tarjeta de crédito usada (puede ser None).
        fecha (date): La fecha de realización de la transacción.
        monto (Decimal): El monto del movimiento (con signo positivo o negativo).
        descripcion (str): La descripción original de la compra.
        cuota_numero (int | None): El número actual de cuota que se está cobrando, o None si no es en cuotas.
        
    Retorna:
        str: Un hash único de 64 caracteres representados en hexadecimal.
    """
    usuario_str = str(usuario_id)
    tarjeta_str = str(tarjeta_id) if tarjeta_id else ""
    fecha_str = fecha.isoformat() if hasattr(fecha, "isoformat") else str(fecha)
    
    # Usar el valor absoluto del monto y redondear siempre a 2 decimales para consistencia
    monto_abs = abs(monto)
    monto_str = f"{monto_abs:.2f}"
    
    # Normalizar la descripción
    descripcion_norm = normalizar_descripcion(descripcion)
    
    # Asegurar que el número de cuota se represente como string ("0" si es None o 0)
    cuota_str = str(cuota_numero) if cuota_numero not in (None, 0, "0") else "0"
    
    # Componer la cadena base separada por pipes
    cadena_base = f"{usuario_str}|{tarjeta_str}|{fecha_str}|{monto_str}|{descripcion_norm}|{cuota_str}"
    
    # Generar el hash SHA-256
    return hashlib.sha256(cadena_base.encode("utf-8")).hexdigest()


def existe_transaccion_duplicada(db: Session, usuario_id: any, import_hash: str) -> bool:
    """
    Comprueba si ya existe guardada en la base de datos una transacción idéntica previamente importada.
    
    Esta función busca en la base de datos si ya existe una transacción para el usuario
    especificado que posea exactamente el mismo código identificador (hash).
    
    Parámetros:
        db (Session): La sesión activa de conexión a la base de datos.
        usuario_id (any): El identificador único del usuario.
        import_hash (str): El código identificador único generado para la transacción.
        
    Retorna:
        bool: True si la transacción ya está registrada, False en caso contrario.
    """
    if not import_hash:
        return False
    
    stmt = select(Transaccion).where(
        Transaccion.usuario_id == usuario_id,
        Transaccion.import_hash == import_hash
    )
    result = db.execute(stmt).first()
    return result is not None


def registrar_correccion(
    db: Session,
    importacion_id: any,
    banco: str,
    capa_parser_usada: str,
    tipo_correccion: TipoCorreccion
) -> None:
    """
    Registra en la base de datos una corrección manual que el usuario realizó sobre los datos parseados.
    
    Esta información funciona como telemetría anonimizada para mejorar la precisión
    de los motores de inteligencia artificial del sistema en futuras lecturas. Por motivos de
    privacidad y seguridad financiera, no se almacena ningún dato que identifique la transacción, 
    como su descripción original, monto o la persona titular.
    
    Parámetros:
        db (Session): La sesión activa de conexión a la base de datos.
        importacion_id (any): El identificador de la sesión de importación actual.
        banco (str): El nombre del banco detectado en el resumen.
        capa_parser_usada (str): El motor o capa técnica con la que se leyó el PDF.
        tipo_correccion (TipoCorreccion): La categoría o tipo de ajuste realizado por el usuario.
    """
    imp_id = PyUUID(importacion_id) if isinstance(importacion_id, str) else importacion_id
    correccion = CorreccionImportacion(
        importacion_id=imp_id,
        banco=banco,
        capa_parser_usada=capa_parser_usada,
        tipo_correccion=tipo_correccion
    )
    db.add(correccion)
    db.flush()


def _buscar_grupo_cuotas(
    db: Session,
    usuario_id: any,
    tarjeta_id: any,
    descripcion: str,
    cantidad_cuotas: int
) -> GrupoCuotas | None:
    """
    Busca un grupo de cuotas existente en la base de datos que coincida con las características indicadas.
    
    Busca grupos activos asociados al usuario, tarjeta e igual cantidad de cuotas. Luego, aplica
    una comparación en Python sobre las descripciones normalizadas para encontrar una coincidencia.
    Esta búsqueda heurística asume que compras con descripciones similares y misma cantidad de cuotas
    pertenecen al mismo plan de financiamiento.
    
    Parámetros:
        db (Session): La sesión activa de conexión a la base de datos.
        usuario_id (any): El identificador único del usuario.
        tarjeta_id (any): El identificador de la tarjeta de crédito.
        descripcion (str): La descripción de la compra que se quiere vincular.
        cantidad_cuotas (int): La cantidad total de cuotas del plan de financiamiento.
        
    Retorna:
        GrupoCuotas | None: El grupo de cuotas encontrado o None si no existe correspondencia.
    """
    u_id = PyUUID(usuario_id) if isinstance(usuario_id, str) else usuario_id
    t_id = PyUUID(tarjeta_id) if isinstance(tarjeta_id, str) else tarjeta_id
    
    # Buscamos grupos de cuotas activos de este usuario para esta tarjeta y cantidad total de cuotas
    stmt = select(GrupoCuotas).where(
        GrupoCuotas.usuario_id == u_id,
        GrupoCuotas.tarjeta_id == t_id,
        GrupoCuotas.cantidad_cuotas == cantidad_cuotas,
        GrupoCuotas.estado == EstadoGrupoCuotas.ACTIVO
    )
    candidatos = db.execute(stmt).scalars().all()
    
    descripcion_nueva_norm = normalizar_descripcion(descripcion)
    
    for grupo in candidatos:
        # Normalizamos la descripción del grupo para compararlas
        descripcion_grupo_norm = normalizar_descripcion(grupo.descripcion)
        if descripcion_grupo_norm == descripcion_nueva_norm:
            return grupo
            
    return None


def importar_transacciones_resumen(
    db: Session,
    usuario_id: any,
    tarjeta_id: any,
    importacion_id: any,
    billetera_id: any,
    billetera_usd_id: any,
    transacciones_crudas: list[TransaccionCruda]
) -> dict:
    """
    Persiste en la base de datos la lista de transacciones curadas de un resumen en una sola operación.
    
    Procesa un lote completo de movimientos, vinculando compras en cuotas a planes de financiamiento
    existentes (o creando nuevos si corresponden). Para garantizar la integridad y evitar datos rotos, 
    toda la importación se ejecuta dentro de una única transacción bancaria: si alguna falla,
    se descartan automáticamente todos los cambios en lote y se marca la importación como fallida.
    
    Parámetros:
        db (Session): La sesión activa de conexión a la base de datos.
        usuario_id (any): El dueño de los registros de transacciones.
        tarjeta_id (any): La tarjeta de crédito asociada a la importación.
        importacion_id (any): El registro resumen de la importación que se actualizará.
        billetera_id (any): La billetera en pesos (ARS) que recibirá los cargos locales.
        billetera_usd_id (any): La billetera en dólares (USD) que recibirá los cargos en moneda extranjera.
        transacciones_crudas (list[TransaccionCruda]): Lista de registros extraídos y aprobados para su guardado.
        
    Retorna:
        dict: Un diccionario con contadores del resultado de la importación.
            Ej: {"importadas": 5, "duplicadas": 2, "sin_billetera_usd": 0, "total_procesadas": 7}
    """
    importadas_count = 0
    duplicadas_count = 0
    sin_billetera_usd_count = 0
    
    # Normalizar UUIDs a objetos UUID de Python para evitar incompatibilidades de dialectos (ej. SQLite)
    u_id = PyUUID(usuario_id) if isinstance(usuario_id, str) else usuario_id
    t_id = PyUUID(tarjeta_id) if isinstance(tarjeta_id, str) else tarjeta_id
    imp_id = PyUUID(importacion_id) if isinstance(importacion_id, str) else importacion_id
    b_id = PyUUID(billetera_id) if isinstance(billetera_id, str) else billetera_id
    b_usd_id = PyUUID(billetera_usd_id) if isinstance(billetera_usd_id, str) else billetera_usd_id
    
    # Recuperamos el registro de control de la importación
    importacion = db.query(ImportacionResumen).filter(ImportacionResumen.id == imp_id).first()
    
    tarjeta = None
    if t_id:
        from app.models.tarjeta_credito import TarjetaCredito
        tarjeta = db.query(TarjetaCredito).filter(TarjetaCredito.id == t_id).first()
    
    try:
        # Abrimos un bloque de savepoint anidado para poder hacer rollback total del lote de inserciones
        with db.begin_nested():
            for cruda in transacciones_crudas:
                # Calcular primer vencimiento si es tarjeta de crédito
                primer_venc_calc = cruda.fecha
                if t_id and tarjeta:
                    from app.services.tarjeta_service import calcular_primer_vencimiento
                    primer_venc_calc = calcular_primer_vencimiento(
                        cruda.fecha, tarjeta.dia_cierre, tarjeta.dia_vencimiento
                    )

                # 1. Calcular hash unívoco para control de duplicados
                hash_val = calcular_import_hash(
                    usuario_id=u_id,
                    tarjeta_id=t_id,
                    fecha=cruda.fecha,
                    monto=cruda.monto,
                    descripcion=cruda.descripcion,
                    cuota_numero=cruda.cuota_actual
                )
                
                # 2. Verificar duplicado en la base de datos
                if existe_transaccion_duplicada(db, u_id, hash_val):
                    duplicadas_count += 1
                    continue
                
                # 3. Determinar billetera según la moneda de la transacción
                if cruda.moneda == "USD":
                    if b_usd_id:
                        billetera_actual_id = b_usd_id
                    else:
                        sin_billetera_usd_count += 1
                        continue
                else:
                    billetera_actual_id = b_id
                
                # Normalizar monto (siempre absoluto en la base de datos de Transaccion)
                monto_abs = abs(cruda.monto)
                monto_round = round(Decimal(str(monto_abs)), 2)
                
                # Si el monto crudo original era negativo, representa un reembolso/reversión
                # lo cual es un ingreso para el usuario; de lo contrario es un egreso
                if cruda.monto < 0:
                    tipo_tx = TipoTransaccion.INGRESO
                else:
                    tipo_tx = TipoTransaccion.EGRESO
                
                # Caso A: Transacción simple sin cuotas
                if cruda.cuota_actual is None:
                    tx = Transaccion(
                        usuario_id=u_id,
                        tipo=tipo_tx,
                        monto=monto_round,
                        moneda=cruda.moneda,
                        fecha=cruda.fecha,
                        descripcion=cruda.descripcion,
                        metodo_pago=MetodoPago.CREDITO if t_id else MetodoPago.DEBITO,
                        billetera_id=billetera_actual_id,
                        tarjeta_id=t_id,
                        categoria_id=cruda.categoria_id,
                        es_cuota_hija=False,
                        es_padre_cuotas=False,
                        origen=OrigenTransaccion.IA_PDF,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                        import_hash=hash_val,
                        importacion_id=imp_id,
                        titular_pdf=cruda.titular_seccion
                    )
                    db.add(tx)
                    db.flush()
                    importadas_count += 1
                
                # Caso B: Transacción de cuota inicial (cuota 1)
                elif cruda.cuota_actual == 1:
                    total_cuotas = cruda.cuota_total if cruda.cuota_total else 1
                    monto_total = monto_round * total_cuotas
                    
                    # 1. Crear transacción padre (funciona como agrupador general)
                    tx_padre = Transaccion(
                        usuario_id=u_id,
                        tipo=tipo_tx,
                        monto=monto_total,
                        moneda=cruda.moneda,
                        fecha=cruda.fecha,
                        descripcion=cruda.descripcion,
                        categoria_id=cruda.categoria_id,
                        metodo_pago=MetodoPago.CREDITO,
                        billetera_id=billetera_actual_id,
                        tarjeta_id=t_id,
                        es_cuota_hija=False,
                        es_padre_cuotas=True,
                        origen=OrigenTransaccion.IA_PDF,
                        estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                        importacion_id=imp_id,
                        titular_pdf=cruda.titular_seccion
                    )
                    db.add(tx_padre)
                    db.flush()
                    
                    # 2. Crear Grupo de Cuotas vinculante
                    grupo = GrupoCuotas(
                        usuario_id=u_id,
                        transaccion_padre_id=tx_padre.id,
                        tarjeta_id=t_id,
                        descripcion=normalizar_descripcion(cruda.descripcion),
                        monto_total=monto_total,
                        cantidad_cuotas=total_cuotas,
                        tiene_interes=False,
                        tasa_interes=None,
                        total_financiado=monto_total,
                        moneda=cruda.moneda,
                        estado=EstadoGrupoCuotas.ACTIVO,
                        primer_vencimiento=primer_venc_calc
                    )
                    db.add(grupo)
                    db.flush()
                    
                    tx_padre.grupo_cuotas_id = grupo.id
                    
                    # 3. Invocar al servicio para generar el plan completo de cuotas
                    # Pasamos u_id como objeto UUID directamente a crear_cuotas para compatibilidad con SQLite
                    cuotas_service.crear_cuotas(
                        db=db,
                        transaccion_padre=tx_padre,
                        grupo=grupo,
                        cantidad_cuotas=total_cuotas,
                        primer_vencimiento=primer_venc_calc,
                        monto_cuota=monto_round,
                        usuario_id=u_id,
                        cuota_inicial=1
                    )
                    
                    # 4. Confirmar y asignar metadatos reales a la cuota 1
                    for cuota in grupo.cuotas:
                        if cuota.numero_cuota == 1:
                            cuota.monto_real = monto_round
                            cuota.pagada = True
                            tx_hija = cuota.transaccion
                            if tx_hija:
                                tx_hija.categoria_id = cruda.categoria_id
                                tx_hija.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                                tx_hija.import_hash = hash_val
                                tx_hija.importacion_id = imp_id
                                tx_hija.origen = OrigenTransaccion.IA_PDF
                                tx_hija.titular_pdf = cruda.titular_seccion
                            break
                    
                    importadas_count += 1
                
                # Caso C: Transacción de cuota intermedia o posterior (> 1)
                else:
                    total_cuotas = cruda.cuota_total if cruda.cuota_total else cruda.cuota_actual
                    
                    # Buscar si ya existe el plan de financiamiento cargado anteriormente
                    grupo_existente = _buscar_grupo_cuotas(
                        db=db,
                        usuario_id=u_id,
                        tarjeta_id=t_id,
                        descripcion=cruda.descripcion,
                        cantidad_cuotas=total_cuotas
                    )
                    
                    if grupo_existente:
                        # Si existe, buscamos la cuota que corresponde a la cuota actual en el resumen
                        cuota_encontrada = False
                        for cuota in grupo_existente.cuotas:
                            if cuota.numero_cuota == cruda.cuota_actual:
                                cuota.monto_real = monto_round
                                cuota.pagada = True
                                tx_hija = cuota.transaccion
                                if tx_hija:
                                    tx_hija.monto = monto_round
                                    if cruda.categoria_id:
                                        tx_hija.categoria_id = cruda.categoria_id
                                    tx_hija.origen = OrigenTransaccion.IA_PDF
                                    tx_hija.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                                    tx_hija.import_hash = hash_val
                                    tx_hija.importacion_id = imp_id
                                    tx_hija.titular_pdf = cruda.titular_seccion
                                cuota_encontrada = True
                                break
                        
                        # Si por algún motivo la cuota actual no estaba pre-generada en el grupo,
                        # la creamos dinámicamente y la vinculamos
                        if not cuota_encontrada:
                            venc_cuota = grupo_existente.primer_vencimiento + relativedelta(months=cruda.cuota_actual - 1)
                            tx_hija = Transaccion(
                                usuario_id=u_id,
                                tipo=tipo_tx,
                                monto=monto_round,
                                moneda=cruda.moneda,
                                fecha=venc_cuota,
                                descripcion=f"{grupo_existente.descripcion} (Cuota {cruda.cuota_actual}/{total_cuotas})",
                                categoria_id=cruda.categoria_id,
                                metodo_pago=MetodoPago.CREDITO,
                                billetera_id=billetera_actual_id,
                                tarjeta_id=t_id,
                                es_cuota_hija=True,
                                grupo_cuotas_id=grupo_existente.id,
                                origen=OrigenTransaccion.IA_PDF,
                                estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                                import_hash=hash_val,
                                importacion_id=imp_id,
                                titular_pdf=cruda.titular_seccion
                            )
                            db.add(tx_hija)
                            db.flush()
                            
                            cuota_reg = Cuota(
                                grupo_id=grupo_existente.id,
                                transaccion_id=tx_hija.id,
                                numero_cuota=cruda.cuota_actual,
                                monto_proyectado=monto_round,
                                monto_real=monto_round,
                                fecha_vencimiento=venc_cuota,
                                pagada=True
                            )
                            db.add(cuota_reg)
                            db.flush()
                        
                        importadas_count += 1
                        
                    else:
                        # Si no existe un grupo registrado, lo creamos de cero inicializándolo en la cuota_actual
                        monto_total = monto_round * total_cuotas
                        
                        # 1. Crear transacción padre virtual
                        tx_padre = Transaccion(
                            usuario_id=u_id,
                            tipo=tipo_tx,
                            monto=monto_total,
                            moneda=cruda.moneda,
                            fecha=cruda.fecha,
                            descripcion=cruda.descripcion,
                            categoria_id=cruda.categoria_id,
                            metodo_pago=MetodoPago.CREDITO,
                            billetera_id=billetera_actual_id,
                            tarjeta_id=t_id,
                            es_cuota_hija=False,
                            es_padre_cuotas=True,
                            origen=OrigenTransaccion.IA_PDF,
                            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
                            importacion_id=imp_id,
                            titular_pdf=cruda.titular_seccion
                        )
                        db.add(tx_padre)
                        db.flush()
                        
                        # 2. Crear Grupo de Cuotas
                        grupo = GrupoCuotas(
                            usuario_id=u_id,
                            transaccion_padre_id=tx_padre.id,
                            tarjeta_id=t_id,
                            descripcion=normalizar_descripcion(cruda.descripcion),
                            monto_total=monto_total,
                            cantidad_cuotas=total_cuotas,
                            tiene_interes=False,
                            tasa_interes=None,
                            total_financiado=monto_total,
                            moneda=cruda.moneda,
                            estado=EstadoGrupoCuotas.ACTIVO,
                            primer_vencimiento=primer_venc_calc
                        )
                        db.add(grupo)
                        db.flush()
                        
                        tx_padre.grupo_cuotas_id = grupo.id
                        
                        # 3. Crear cuotas en la base indicando que la cuota inicial es la cuota actual importada
                        cuotas_service.crear_cuotas(
                            db=db,
                            transaccion_padre=tx_padre,
                            grupo=grupo,
                            cantidad_cuotas=total_cuotas,
                            primer_vencimiento=primer_venc_calc,
                            monto_cuota=monto_round,
                            usuario_id=u_id, # Pasamos UUID directamente
                            cuota_inicial=cruda.cuota_actual
                        )
                        
                        # 4. Confirmar y mapear metadatos del resumen a la transacción de la cuota actual
                        for cuota in grupo.cuotas:
                            if cuota.numero_cuota == cruda.cuota_actual:
                                cuota.monto_real = monto_round
                                cuota.pagada = True
                                tx_hija = cuota.transaccion
                                if tx_hija:
                                    tx_hija.categoria_id = cruda.categoria_id
                                    tx_hija.estado_verificacion = EstadoVerificacionTransaccion.CONFIRMADA
                                    tx_hija.import_hash = hash_val
                                    tx_hija.importacion_id = imp_id
                                    tx_hija.origen = OrigenTransaccion.IA_PDF
                                    tx_hija.titular_pdf = cruda.titular_seccion
                                break
                        
                        importadas_count += 1
            
            # En caso de éxito, actualizamos el control de la importación
            if importacion:
                importacion.estado = EstadoImportacion.IMPORTADO
                importacion.total_importadas = importadas_count
                importacion.total_duplicadas = duplicadas_count
                
        # Commit definitivo de los cambios
        db.commit()
        
    except Exception as e:
        # En caso de cualquier error, SQLAlchemy descarta automáticamente el savepoint del lote
        db.rollback()
        
        # Guardamos el estado de error de forma aislada para que quede registro en el sistema
        try:
            # Volvemos a obtener el objeto ya que la sesión se reinició tras el rollback
            importacion_error = db.query(ImportacionResumen).filter(ImportacionResumen.id == imp_id).first()
            if importacion_error:
                importacion_error.estado = EstadoImportacion.ERROR
                # Mensaje de error legible sin detalles técnicos de stacktrace
                importacion_error.mensaje_error = f"Error en la persistencia del lote: {str(e)}"
                db.commit()
        except Exception:
            db.rollback()
            
        raise e
        
    return {
        "importadas": importadas_count,
        "duplicadas": duplicadas_count,
        "sin_billetera_usd": sin_billetera_usd_count,
        "total_procesadas": importadas_count + duplicadas_count + sin_billetera_usd_count
    }
