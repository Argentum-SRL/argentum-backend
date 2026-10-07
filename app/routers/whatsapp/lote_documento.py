"""
app/routers/whatsapp/lote_documento.py
Separación de movimientos duplicados y construcción de propuestas de transacciones a partir de documentos e imágenes.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID
import structlog
from sqlalchemy.orm import Session

from app.models.billetera import Billetera
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.services import duplicados_service
from app.utils.formato import formatear_monto

logger = structlog.get_logger(__name__)


def separar_duplicados(
    db: Session,
    usuario_id: UUID,
    billetera: Billetera | None,
    entidades: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Verifica contra la base de datos si alguno de los movimientos propuestos ya existe
    utilizando duplicados_service.buscar_coincidencias (máximo 10 consultas, 1 por ítem).
    Devuelve (entidades_sin_duplicados, lista_duplicados).
    """
    if not entidades or entidades.get("monto") is None:
        return {}, []

    # Extraer todos los ítems candidatos
    items_candidatos: list[dict[str, Any]] = []
    item_ppal = {
        "monto": Decimal(str(entidades["monto"])),
        "descripcion": entidades.get("descripcion") or "",
        "categoria": entidades.get("categoria"),
        "tipo": entidades.get("tipo", "egreso"),
        "fecha": entidades.get("fecha"),
        "moneda": entidades.get("moneda", "ARS"),
        "billetera": entidades.get("billetera"),
        "billetera_origen": entidades.get("billetera_origen"),
        "billetera_destino": entidades.get("billetera_destino"),
    }
    items_candidatos.append(item_ppal)

    for ad in entidades.get("transacciones_adicionales", []):
        if isinstance(ad, dict) and ad.get("monto") is not None:
            item_ad = {
                "monto": Decimal(str(ad["monto"])),
                "descripcion": ad.get("descripcion") or "",
                "categoria": ad.get("categoria"),
                "tipo": ad.get("tipo", "egreso"),
                "fecha": ad.get("fecha"),
                "moneda": ad.get("moneda", "ARS"),
                "billetera": ad.get("billetera"),
                "billetera_origen": ad.get("billetera_origen"),
                "billetera_destino": ad.get("billetera_destino"),
            }
            items_candidatos.append(item_ad)

    no_duplicados: list[dict[str, Any]] = []
    duplicados: list[dict[str, Any]] = []

    billetera_id = billetera.id if billetera else None

    for it in items_candidatos:
        monto_it = it["monto"]
        moneda_it = it["moneda"]
        tipo_it = it["tipo"]
        fecha_res, _ = _resolver_y_validar_fecha(it.get("fecha"))
        desc_it = it.get("descripcion")

        coincs = duplicados_service.buscar_coincidencias(
            db=db,
            usuario_id=usuario_id,
            monto=monto_it,
            moneda=moneda_it,
            fecha=fecha_res,
            tipo=tipo_it,
            billetera_id=billetera_id,
            descripcion=desc_it,
        )

        if coincs:
            duplicados.append({
                "descripcion": desc_it,
                "fecha": fecha_res,
                "monto": monto_it,
                "moneda": moneda_it,
            })
        else:
            no_duplicados.append(it)

    if not no_duplicados:
        # Todos eran duplicados
        return {}, duplicados

    # Reconstruir entidades con los que no eran duplicados
    entidades_limpias = dict(entidades)
    m0 = no_duplicados[0]
    entidades_limpias["monto"] = m0["monto"]
    entidades_limpias["descripcion"] = m0["descripcion"]
    entidades_limpias["categoria"] = m0["categoria"]
    entidades_limpias["tipo"] = m0["tipo"]
    entidades_limpias["fecha"] = m0["fecha"]
    entidades_limpias["moneda"] = m0["moneda"]
    entidades_limpias["billetera"] = m0.get("billetera")
    entidades_limpias["billetera_origen"] = m0.get("billetera_origen")
    entidades_limpias["billetera_destino"] = m0.get("billetera_destino")
    entidades_limpias["transacciones_adicionales"] = no_duplicados[1:]

    return entidades_limpias, duplicados


def armar_resultado_ia_documento(
    entidades: dict[str, Any],
    duplicados: list[dict[str, Any]],
    billetera_nombre: str | None,
    se_asumio_principal: bool,
    billeteras_usuario: list[Billetera] | None = None,
    documento_tipo: str = "otro",
    total_vistos: int = 0,
) -> dict[str, Any]:
    """
    Construye el dict resultado_ia para un documento o imagen con extracción estructurada.
    Aplica las reglas de decisiones 3 (tope 10), 5 (avisos de duplicados) y 7 (factura de servicios).
    """
    if not entidades or entidades.get("monto") is None:
        # Todos son duplicados: no se propone nada ni queda confirmable
        return {
            "intent": "duplicado",
            "confianza": 0.90,
            "slot_filling": False,
            "entidades": {},
            "respuesta_usuario": "Ya tenías cargado todo lo que veo en la imagen.",
        }

    texto_propuesta = _construir_propuesta_transaccion(
        entidades,
        billetera_nombre=billetera_nombre,
        se_asumio_principal=se_asumio_principal,
        billeteras_usuario=billeteras_usuario,
    )

    # Decisión 7: Factura de servicios
    if documento_tipo == "factura_servicio":
        texto_propuesta += "\nSi todavía no la pagaste, respondé no y no la cargo."

    # Decisión 5: Avisos de movimientos duplicados descartados
    if duplicados:
        for d in duplicados:
            desc = d.get("descripcion") or "Gasto"
            f_nat = _formatear_fecha_natural(d["fecha"]) if d.get("fecha") else None
            fecha_str = f_nat if f_nat else "hoy"
            m_fmt = formatear_monto(float(d["monto"]), d.get("moneda", "ARS"))
            texto_propuesta += f"\nYa tenías cargado: {desc} ({fecha_str}, {m_fmt})."
        texto_propuesta += "\nSi es otro movimiento igual, mandámelo escrito."

    # Decisión 3: Tope de 10 movimientos
    if total_vistos > 10:
        texto_propuesta += f"\nVi {total_vistos} movimientos y cargo los primeros 10. Mandame otra captura con el resto."

    return {
        "intent": "registrar_transaccion",
        "confianza": 0.90,
        "slot_filling": False,
        "entidades": entidades,
        "_asumio_principal": se_asumio_principal,
        "respuesta_usuario": str(texto_propuesta),
    }
