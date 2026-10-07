"""
app/routers/whatsapp/lote_documento.py
Separación de movimientos duplicados y construcción de propuestas de transacciones a partir de documentos e imágenes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID
import structlog
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.usuario import Moneda
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.services import duplicados_service
from app.utils.fecha import hoy_argentina
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


def preparar_rendimientos(
    db: Session,
    usuario_id: UUID,
    entidades: dict[str, Any],
    billetera_texto: str | None,
    documento_tipo: str,
    camino: str = "B",
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Evalúa los rendimientos extraídos del documento (decisiones 4, 5, 6 y 9).
    En camino B: devuelve (rendimientos_a_anotar, lineas_aviso).
    En camino A: devuelve ([], ['Salteé {n} rendimiento(s): eso lo calculo yo.']).
    """
    rendimientos_raw = entidades.get("rendimientos") or []
    if not rendimientos_raw:
        return [], []

    if camino == "A":
        n = len(rendimientos_raw)
        palabra = "rendimiento" if n == 1 else "rendimientos"
        return [], [f"Salteé {n} {palabra}: eso lo calculo yo."]

    # Camino B: billeteras que rinden (billeteras de inversión)
    billeteras_rinden = db.execute(
        select(Billetera).where(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            Billetera.es_inversion == True,
        ).order_by(Billetera.nombre.asc(), Billetera.id.asc())
    ).scalars().all()

    billetera_elegida: Billetera | None = None
    if documento_tipo == "captura_actividad" and billetera_texto:
        b_match, _ = resolver_billetera_cascada(billetera_texto, billeteras_rinden)
        if b_match:
            billetera_elegida = b_match
        else:
            billetera_elegida = None
    elif not billetera_texto:
        if len(billeteras_rinden) == 1:
            billetera_elegida = billeteras_rinden[0]
        else:
            billetera_elegida = None
    else:
        # billetera_texto presente pero documento_tipo != captura_actividad
        if len(billeteras_rinden) == 1:
            billetera_elegida = billeteras_rinden[0]
        else:
            billetera_elegida = None

    hoy = hoy_argentina()
    rendimientos_a_anotar: list[dict[str, Any]] = []
    avisos: list[str] = []
    fechas_usadas: set[date] = set()

    for r in rendimientos_raw:
        monto_raw = r.get("monto")
        if monto_raw is None:
            continue
        try:
            monto_dec = Decimal(str(monto_raw))
        except Exception:
            continue

        monto_fmt = formatear_monto(float(monto_dec), Moneda.ARS)

        if not billetera_elegida:
            avisos.append(
                f"Vi un rendimiento de {monto_fmt} que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras."
            )
            continue

        fecha_raw = r.get("fecha")
        if not fecha_raw:
            avisos.append(
                f"Vi un rendimiento de {monto_fmt} que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras."
            )
            continue

        fecha_obj, _ = _resolver_y_validar_fecha(fecha_raw)
        if not fecha_obj or fecha_obj > hoy or (hoy - fecha_obj).days > 60:
            avisos.append(
                f"Vi un rendimiento de {monto_fmt} que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras."
            )
            continue

        existe_db = db.execute(
            select(RendimientoBilletera.id).where(
                RendimientoBilletera.billetera_id == billetera_elegida.id,
                func.date(RendimientoBilletera.fecha) == fecha_obj,
            )
        ).scalar_one_or_none()

        if existe_db or fecha_obj in fechas_usadas:
            f_nat = _formatear_fecha_natural(fecha_obj) or "hoy"
            fecha_existente = f_nat[3:] if f_nat.startswith("el ") else f_nat
            avisos.append(
                f"Ya tenías el rendimiento del {fecha_existente} en {billetera_elegida.nombre}."
            )
            continue

        fechas_usadas.add(fecha_obj)
        f_nat_prop = _formatear_fecha_natural(fecha_obj) or "hoy"
        avisos.append(
            f"Además anoto el rendimiento de {monto_fmt} en {billetera_elegida.nombre} ({f_nat_prop}); no cuenta como ingreso."
        )
        rendimientos_a_anotar.append({
            "billetera_id": str(billetera_elegida.id),
            "billetera_nombre": billetera_elegida.nombre,
            "fecha": fecha_obj.isoformat(),
            "monto": float(monto_dec),
        })

    return rendimientos_a_anotar, avisos


def armar_resultado_ia_documento(
    entidades: dict[str, Any],
    duplicados: list[dict[str, Any]],
    billetera_nombre: str | None,
    se_asumio_principal: bool,
    billeteras_usuario: list[Billetera] | None = None,
    documento_tipo: str = "otro",
    total_vistos: int = 0,
    rendimientos_a_anotar: list[dict[str, Any]] | None = None,
    avisos_rendimientos: list[str] | None = None,
    camino: str = "B",
    solo_rendimientos: bool = False,
) -> dict[str, Any]:
    """
    Construye el dict resultado_ia para un documento o imagen con extracción estructurada.
    Aplica las reglas de decisiones 3 (tope 10), 5 (avisos de duplicados), 7 (factura de servicios)
    y rendimientos (decisiones 4, 7 y 9).
    """
    if solo_rendimientos:
        if camino == "A":
            msg_solo = "Veo solo rendimientos y eso lo calculo yo. Mandame los gastos o ingresos que quieras anotar."
        else:
            msg_solo = "Veo solo rendimientos. Los cargás desde Billeteras o mandame los gastos o ingresos que quieras anotar."
        return {
            "intent": "duplicado",
            "confianza": 0.90,
            "slot_filling": False,
            "entidades": {},
            "respuesta_usuario": msg_solo,
        }

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

    # Avisos de rendimientos (Decisiones 4, 5, 6 y 9)
    if avisos_rendimientos:
        for av in avisos_rendimientos:
            texto_propuesta += f"\n{av}"

    # Decisión 3: Tope de 10 movimientos
    if total_vistos > 10:
        texto_propuesta += f"\nVi {total_vistos} movimientos y cargo los primeros 10. Mandame otra captura con el resto."

    if rendimientos_a_anotar:
        entidades["rendimientos"] = rendimientos_a_anotar
    elif "rendimientos" in entidades:
        entidades.pop("rendimientos", None)

    return {
        "intent": "registrar_transaccion",
        "confianza": 0.90,
        "slot_filling": False,
        "entidades": entidades,
        "_asumio_principal": se_asumio_principal,
        "respuesta_usuario": str(texto_propuesta),
    }
