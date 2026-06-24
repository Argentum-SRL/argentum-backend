import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.analisis_ia import AnalisisIA
from app.services.payload_builder import construir_payload
from app.services.openai_client import llamar_openai, MODULE_MODEL
from app.schemas.analisis_ia import ExportacionResponse, SeccionesAnalisis

logger = logging.getLogger(__name__)


def generar_analisis(
    db: Session,
    usuario_id: str | UUID,
    tipo_analisis: str = "completo",
    ciclos: int = 3
) -> AnalisisIA:
    """
    Orquesta el flujo completo de Análisis IA:
    1. Llama a construir_payload. Si falla, crea un registro de error y relanza.
    2. Crea un registro de análisis en estado 'pendiente'.
    3. Llama a OpenAI. Si falla, registra el error y relanza.
    4. Intenta parsear la respuesta como JSON. Si falla, guarda el texto crudo.
    5. Calcula el costo en base a tokens consumidos.
    6. Actualiza y retorna el registro.
    """
    u_id = UUID(str(usuario_id))

    # 1. Construir el payload de datos financieros
    try:
        payload, perfil_detectado = construir_payload(db, u_id, ciclos)
    except ValueError as e:
        logger.error(f"Error al construir payload financiero para usuario {u_id}: {e}")
        # Crear registro con estado='error'
        nuevo_analisis = AnalisisIA(
            usuario_id=u_id,
            tipo_analisis=tipo_analisis,
            ciclos_analizados=ciclos,
            periodo_inicio=date.today(),
            periodo_fin=date.today(),
            estado="error",
            error_detalle=str(e),
            modelo_usado=MODULE_MODEL,
            generado_por="manual"
        )
        db.add(nuevo_analisis)
        db.commit()
        raise e

    # 2. Registrar análisis como pendiente
    periodo_inicio = date.fromisoformat(payload["contexto_usuario"]["periodo_analizado"]["inicio"])
    periodo_fin = date.fromisoformat(payload["contexto_usuario"]["periodo_analizado"]["fin"])

    nuevo_analisis = AnalisisIA(
        usuario_id=u_id,
        tipo_analisis=tipo_analisis,
        ciclos_analizados=len(payload["ingresos"]["por_ciclo"]),
        periodo_inicio=periodo_inicio,
        periodo_fin=periodo_fin,
        perfil_detectado=perfil_detectado,
        payload_enviado=payload,
        estado="pendiente",
        modelo_usado=MODULE_MODEL,
        generado_por="manual"
    )
    db.add(nuevo_analisis)
    db.commit()
    db.refresh(nuevo_analisis)

    # 3. Llamar a OpenAI
    try:
        res_openai = llamar_openai(payload)
    except Exception as exc:
        logger.error(f"Error al llamar a la API de OpenAI para el análisis {nuevo_analisis.id}: {exc}")
        nuevo_analisis.estado = "error"
        nuevo_analisis.error_detalle = str(exc)
        db.commit()
        raise exc

    # 4. Parsear respuesta
    contenido = res_openai["contenido"]
    in_tokens = res_openai["input_tokens"]
    out_tokens = res_openai["output_tokens"]

    # Calcular costo financiero de la consulta (según precios por token del prompt)
    costo_usd = Decimal(str(in_tokens * 0.00000015 + out_tokens * 0.00000060))

    # Limpiar posibles delimitadores de markdown en caso de que la IA responda con bloques de código JSON
    clean_content = contenido.strip()
    if clean_content.startswith("```"):
        lines = clean_content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        clean_content = "\n".join(lines).strip()

    try:
        secciones_dict = json.loads(clean_content)
        resultado_secciones = secciones_dict
    except Exception as parse_err:
        logger.warning(
            f"Fallo al parsear respuesta JSON de la IA para el análisis {nuevo_analisis.id}: {parse_err}. "
            "Se almacenará como texto crudo."
        )
        resultado_secciones = {
            "texto_crudo": contenido,
            "error_parseo": "La IA no devolvió JSON válido."
        }

    # 5. Persistir resultado final
    nuevo_analisis.resultado = contenido
    nuevo_analisis.resultado_secciones = resultado_secciones
    nuevo_analisis.estado = "completado"
    nuevo_analisis.input_tokens = in_tokens
    nuevo_analisis.output_tokens = out_tokens
    nuevo_analisis.costo_usd = costo_usd

    db.commit()
    db.refresh(nuevo_analisis)

    logger.info(f"Análisis {nuevo_analisis.id} completado y guardado con éxito.")
    return nuevo_analisis


def obtener_historial(
    db: Session,
    usuario_id: str | UUID,
    limit: int = 10
) -> List[AnalisisIA]:
    """
    Retorna el historial de análisis realizados por el usuario, ordenados por fecha descendente.
    Garantiza la seguridad mediante la validación del usuario_id.
    """
    u_id = UUID(str(usuario_id))
    return db.query(AnalisisIA).filter(
        AnalisisIA.usuario_id == u_id
    ).order_by(
        AnalisisIA.creado_en.desc()
    ).limit(limit).all()


def obtener_por_id(
    db: Session,
    usuario_id: str | UUID,
    analisis_id: str | UUID
) -> Optional[AnalisisIA]:
    """
    Busca un análisis por ID validando la pertenencia al usuario (Ownership Validation).
    """
    u_id = UUID(str(usuario_id))
    a_id = UUID(str(analisis_id))
    return db.query(AnalisisIA).filter(
        AnalisisIA.id == a_id,
        AnalisisIA.usuario_id == u_id
    ).first()


def generar_texto_exportacion(
    db: Session,
    usuario_id: str | UUID,
    ciclos: int = 3
) -> ExportacionResponse:
    """
    Construye los datos en formato legible de texto plano junto con un prompt simplificado
    para permitir la copia y pega a sistemas externos.
    """
    u_id = UUID(str(usuario_id))

    # Obtener el payload
    payload, _ = construir_payload(db, u_id, ciclos)

    contexto = payload["contexto_usuario"]
    ingresos = payload["ingresos"]
    compromisos = payload["compromisos_fijos"]
    liquidez = payload["liquidez"]

    datos_txt = []
    datos_txt.append(f"DATOS FINANCIEROS PERSONALES DE {contexto['nombre'].upper()}")
    datos_txt.append(
        f"Período analizado: {contexto['periodo_analizado']['inicio']} al {contexto['periodo_analizado']['fin']} "
        f"({contexto['periodo_analizado']['ciclos_completos']} ciclos)"
    )
    datos_txt.append("")
    datos_txt.append("=== INGRESOS ===")
    datos_txt.append(f"Ingreso promedio mensual: ${ingresos['promedio_mensual_ars']:,.2f} ARS")
    datos_txt.append(
        f"Estabilidad de ingresos: {ingresos['estabilidad']} "
        f"(Coeficiente de variación: {ingresos['coeficiente_variacion']:.4f})"
    )
    datos_txt.append("")
    datos_txt.append("=== COMPROMISOS FIJOS ===")
    datos_txt.append(f"Carga mensual de cuotas: ${compromisos['cuotas']['carga_mensual_ars']:,.2f} ARS")
    datos_txt.append(
        f"Total pendiente en cuotas: ${compromisos['cuotas']['total_pendiente_ars']:,.2f} ARS "
        f"(hasta liberación en {compromisos['cuotas']['meses_hasta_liberacion']} meses)"
    )
    datos_txt.append(f"Suscripciones mensuales: ${compromisos['suscripciones_total_mensual_ars']:,.2f} ARS")
    datos_txt.append(f"Gastos recurrentes mensuales: ${compromisos['recurrentes_total_mensual_ars']:,.2f} ARS")
    datos_txt.append(
        f"Total compromisos fijos: ${compromisos['total_compromisos_mensual_ars']:,.2f} ARS "
        f"({compromisos['ratio_compromisos_sobre_ingreso_pct']:.1f}% de tus ingresos)"
    )
    datos_txt.append("")
    datos_txt.append("=== LIQUIDEZ Y BALANCE ===")
    datos_txt.append(f"Balance total ARS: ${liquidez['balance_total_ars']:,.2f} ARS")
    if "balance_usd" in liquidez and liquidez["balance_usd"] > 0:
        datos_txt.append(f"Balance total USD: ${liquidez['balance_usd']:,.2f} USD")
    datos_txt.append(f"Ahorro disponible estimado: ${liquidez['ahorro_disponible_ars']:,.2f} ARS")
    datos_txt.append(f"Meses de fondo de emergencia disponibles: {liquidez['meses_fondo_disponible']:.2f}")
    datos_txt.append("")

    datos_txt.append("=== EGRESOS POR CATEGORÍA ===")
    for cat in payload["gastos_por_categoria"]:
        datos_txt.append(
            f"- {cat['categoria']}: promedio ${cat['promedio_mensual_ars']:,.2f} ARS/mes "
            f"(Variación: {cat['variacion_pct']:.1f}%)"
        )
        for sub in cat.get("subcategorias", []):
            datos_txt.append(
                f"  * {sub['nombre']}: promedio ${sub['promedio_mensual_ars']:,.2f} ARS/mes "
                f"({sub['ocurrencias']} ocurrencias)"
            )

    datos_txt.append("")
    datos_txt.append("=== GASTOS HORMIGA DETECTADOS ===")
    for gh in payload["gastos_hormiga"]:
        datos_txt.append(
            f"- {gh['subcategoria']}: {gh['ocurrencias_promedio_por_ciclo']:.1f} veces/ciclo. "
            f"Promedio mensual: ${gh['total_mensual_ars']:,.2f} ARS. "
            f"Impacto anual: ${gh['impacto_anual_ars']:,.2f} ARS "
            f"(monto unitario promedio: ${gh['monto_unitario_promedio_ars']:,.2f} ARS)"
        )

    datos_txt.append("")
    datos_txt.append("=== ADVERTENCIAS DE DATOS ===")
    for adv in payload["advertencias_datos"]:
        datos_txt.append(f"- {adv}")

    datos_txt.append("\n=======================================================\n")

    # Prompt para IA externa
    prompt_txt = (
        "Por favor, realizá un análisis exhaustivo de mis finanzas personales basándote en los datos provistos arriba. "
        "Actuá como un analista financiero con conocimiento del contexto argentino (inflación, cuotas, ciclo del 21 al 20). "
        "Escribí tu respuesta en castellano rioplatense, de forma directa y sin rodeos, en las siguientes secciones:\n"
        "1. Resumen ejecutivo del estado financiero.\n"
        "2. Evaluación de la salud financiera y capacidad de ahorro.\n"
        "3. Análisis de gastos hormiga y oportunidades de recorte.\n"
        "4. Evaluación del fondo de emergencia y cobertura.\n"
        "5. Oportunidades generales identificadas.\n"
        "6. Limitaciones del análisis basándote en las advertencias de datos.\n"
        "No me recomendés productos de inversión específicos por nombre, solo indicá mi capacidad de ahorro o qué áreas optimizar."
    )

    texto_completo = "\n".join(datos_txt) + prompt_txt

    return ExportacionResponse(
        texto=texto_completo,
        instrucciones="Copiá este bloque completo y pegalo en ChatGPT, Claude o Gemini.",
        advertencias=payload["advertencias_datos"]
    )
