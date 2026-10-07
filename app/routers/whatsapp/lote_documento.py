"""
app/routers/whatsapp/lote_documento.py
Separación de movimientos duplicados y construcción de propuestas de transacciones a partir de documentos e imágenes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID
import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.ajuste_saldo import AjusteSaldo
from app.models.billetera import Billetera, EstadoBilletera
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.usuario import Moneda
from app.routers.whatsapp.extraccion_documento import (
    ResultadoExtraccion,
    parsear_fecha_documento,
)
from app.routers.whatsapp.parsers import (
    _formatear_fecha_natural,
    _resolver_y_validar_fecha,
)
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.services import duplicados_service
from app.utils.fecha import TZ_ARGENTINA, hoy_argentina
from app.utils.formato import formatear_monto
from app.utils.texto import normalizar_texto

logger = structlog.get_logger(__name__)


def asignar_billeteras(
    resultado: ResultadoExtraccion,
    billeteras_pesos: list[Billetera],
    billetera_defecto_nombre: str,
    se_asumio_defecto: bool = True,
) -> bool:
    """
    Asigna billetera_nombre a cada movimiento en resultado.movimientos:
    - Si medio_pago normalizado contiene "dinero disponible" y el usuario tiene EXACTAMENTE
      una billetera ARS activa (no inversión) con entidad_efectiva "mercadopago", ese movimiento
      va a esa billetera.
    - Todos los demás van a billetera_defecto_nombre.
    Retorna True si algún movimiento común queda en la principal asumida.
    """
    mp_billeteras = [
        b for b in billeteras_pesos
        if not getattr(b, "es_inversion", False)
        and getattr(b, "estado", EstadoBilletera.ACTIVA) == EstadoBilletera.ACTIVA
        and getattr(b, "entidad_efectiva", None) == "mercadopago"
    ]
    tiene_exactamente_un_mp = len(mp_billeteras) == 1
    billetera_mp_nombre = mp_billeteras[0].nombre if tiene_exactamente_un_mp else None

    b_ppal = next((b for b in billeteras_pesos if getattr(b, "es_principal", False)), None)
    if not b_ppal and billeteras_pesos:
        b_ppal = billeteras_pesos[0]
    b_ppal_nombre = b_ppal.nombre if b_ppal else None

    defecto_es_principal_asumida = se_asumio_defecto and (billetera_defecto_nombre == b_ppal_nombre)
    alguno_en_principal_asumida = False

    for m in resultado.movimientos:
        medio_norm = normalizar_texto(m.medio_pago)
        if "dinero disponible" in medio_norm and tiene_exactamente_un_mp:
            m.billetera_nombre = billetera_mp_nombre
        else:
            m.billetera_nombre = billetera_defecto_nombre

        if defecto_es_principal_asumida and m.billetera_nombre == billetera_defecto_nombre:
            alguno_en_principal_asumida = True

    return alguno_en_principal_asumida


def separar_duplicados(
    db: Session,
    usuario_id: UUID,
    billetera: Billetera | None,
    entidades: dict[str, Any],
    billeteras_usuario: list[Billetera] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """
    Verifica contra la base de datos si alguno de los movimientos propuestos ya existe
    utilizando duplicados_service.buscar_coincidencias (máximo 10 consultas, 1 por ítem).
    Compara cada movimiento con la billetera de ese movimiento (se resuelve por nombre entre
    las billeteras del usuario) y usa la billetera recibida solo como respaldo.
    Devuelve (entidades_sin_duplicados, lista_duplicados).
    """
    if not entidades or entidades.get("monto") is None:
        return {}, []

    if billeteras_usuario is None and db is not None and usuario_id is not None:
        try:
            from app.routers.whatsapp.db_lookups import _obtener_billeteras_activas
            billeteras_usuario = _obtener_billeteras_activas(usuario_id, db)
        except Exception:
            billeteras_usuario = [billetera] if billetera else []
    elif billeteras_usuario is None:
        billeteras_usuario = [billetera] if billetera else []

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

    for it in items_candidatos:
        monto_it = it["monto"]
        moneda_it = it["moneda"]
        tipo_it = it["tipo"]
        fecha_res, _ = _resolver_y_validar_fecha(it.get("fecha"))
        desc_it = it.get("descripcion")

        nom_b = it.get("billetera") or it.get("billetera_origen") or it.get("billetera_destino")
        b_item: Billetera | None = None
        if nom_b and billeteras_usuario:
            b_item = next((b for b in billeteras_usuario if b.nombre.lower() == nom_b.lower()), None)
            if not b_item:
                b_item, _ = resolver_billetera_cascada(nom_b, billeteras_usuario)

        b_efectiva = b_item if b_item is not None else billetera
        billetera_id = b_efectiva.id if b_efectiva else None

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

    # Camino B: billeteras que rinden (activas con es_inversion=True O tna no nula)
    billeteras_rinden = db.execute(
        select(Billetera).where(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
            or_(
                Billetera.es_inversion == True,
                Billetera.tna.isnot(None),
            ),
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
    billeteras_aviso_cobertura: set[UUID] = set()

    ancla: date | None = None
    if billetera_elegida:
        # Replicación del cálculo del ancla según rendimiento_billetera_service.calcular_rendimiento_estimado
        fur = getattr(billetera_elegida, "fecha_ultimo_rendimiento", None)
        if fur is not None and isinstance(fur, datetime):
            dt_rend = fur
            if dt_rend.tzinfo is None:
                dt_rend = dt_rend.replace(tzinfo=timezone.utc)
            ancla = dt_rend.astimezone(TZ_ARGENTINA).date()
        else:
            dt_creacion = getattr(billetera_elegida, "fecha_creacion", None)
            if isinstance(dt_creacion, datetime):
                if dt_creacion.tzinfo is None:
                    dt_creacion = dt_creacion.replace(tzinfo=timezone.utc)
                fecha_creacion_date = dt_creacion.astimezone(TZ_ARGENTINA).date()
            else:
                fecha_creacion_date = date(2000, 1, 1)

            ultimo_ajuste_res = db.execute(
                select(func.max(AjusteSaldo.fecha)).where(AjusteSaldo.billetera_id == billetera_elegida.id)
            ).scalar_one_or_none()
            ultimo_ajuste_fecha = ultimo_ajuste_res if isinstance(ultimo_ajuste_res, (date, datetime)) else None

            if ultimo_ajuste_fecha is not None:
                ajuste_date = ultimo_ajuste_fecha if isinstance(ultimo_ajuste_fecha, date) else ultimo_ajuste_fecha.date()
                ancla = max(fecha_creacion_date, ajuste_date)
            else:
                ancla = fecha_creacion_date

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
        fecha_obj = parsear_fecha_documento(fecha_raw) if fecha_raw else None
        if not fecha_obj or fecha_obj > hoy or (hoy - fecha_obj).days > 60:
            avisos.append(
                f"Vi un rendimiento de {monto_fmt} que no pude anotar (billetera o fecha dudosa). Cargalo desde Billeteras."
            )
            continue

        if ancla is not None and fecha_obj <= ancla:
            if billetera_elegida.id not in billeteras_aviso_cobertura:
                billeteras_aviso_cobertura.add(billetera_elegida.id)
                f_nat_ancla = _formatear_fecha_natural(ancla)
                hasta_str = f"hasta {f_nat_ancla}" if f_nat_ancla else "hasta hoy"
                avisos.append(
                    f"Ya tenías cargados los rendimientos de {billetera_elegida.nombre} {hasta_str}."
                )
            continue

        existe_db = db.execute(
            select(RendimientoBilletera.id).where(
                RendimientoBilletera.billetera_id == billetera_elegida.id,
                func.date(func.timezone("America/Argentina/Buenos_Aires", RendimientoBilletera.fecha)) == fecha_obj,
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


def formatear_lineas_omitidos(omitidos: list[dict[str, Any]] | None) -> list[str]:
    """
    Genera las líneas de texto fijo para movimientos omitidos según Decisión 5:
    - pase_propio: "Salteé {n} pase entre tus cuentas ({montos separados por coma}). Si querés registrarlos, mandame cada uno como una transferencia entre tus cuentas." (plural: "pases entre tus cuentas"). Sin nombres.
    - credito: "Salteé {n} pago con tarjeta de crédito ({descripcion monto, separados por coma}): necesito la tarjeta y las cuotas. Mandámelo escrito." (plural: "pagos con tarjeta de crédito").
    - no_aprobado: "Salteé {n} movimiento que no figura como aprobado." (plural: "movimientos que no figuran como aprobados").
    """
    if not omitidos:
        return []

    lineas: list[str] = []

    # 1. pase_propio
    pases = [o for o in omitidos if (o.get("motivo") if isinstance(o, dict) else getattr(o, "motivo", None)) == "pase_propio"]
    if pases:
        n = len(pases)
        montos_str = []
        for p in pases:
            m_val = p.get("monto") if isinstance(p, dict) else getattr(p, "monto", None)
            m_mon = p.get("moneda", "ARS") if isinstance(p, dict) else getattr(p, "moneda", "ARS")
            montos_str.append(formatear_monto(m_val, m_mon))
        montos_unidos = ", ".join(montos_str)
        if n == 1:
            lineas.append(f"Salteé 1 pase entre tus cuentas ({montos_unidos}). Si querés registrarlos, mandame cada uno como una transferencia entre tus cuentas.")
        else:
            lineas.append(f"Salteé {n} pases entre tus cuentas ({montos_unidos}). Si querés registrarlos, mandame cada uno como una transferencia entre tus cuentas.")

    # 2. credito
    creditos = [o for o in omitidos if (o.get("motivo") if isinstance(o, dict) else getattr(o, "motivo", None)) == "credito"]
    if creditos:
        n = len(creditos)
        items_str = []
        for c in creditos:
            desc = (c.get("descripcion") if isinstance(c, dict) else getattr(c, "descripcion", None)) or "Gasto"
            m_val = c.get("monto") if isinstance(c, dict) else getattr(c, "monto", None)
            m_mon = c.get("moneda", "ARS") if isinstance(c, dict) else getattr(c, "moneda", "ARS")
            m_fmt = formatear_monto(m_val, m_mon)
            items_str.append(f"{desc} {m_fmt}")
        items_unidos = ", ".join(items_str)
        if n == 1:
            lineas.append(f"Salteé 1 pago con tarjeta de crédito ({items_unidos}): necesito la tarjeta y las cuotas. Mandámelo escrito.")
        else:
            lineas.append(f"Salteé {n} pagos con tarjeta de crédito ({items_unidos}): necesito la tarjeta y las cuotas. Mandámelo escrito.")

    # 3. no_aprobado
    no_aprobados = [o for o in omitidos if (o.get("motivo") if isinstance(o, dict) else getattr(o, "motivo", None)) == "no_aprobado"]
    if no_aprobados:
        n = len(no_aprobados)
        if n == 1:
            lineas.append("Salteé 1 movimiento que no figura como aprobado.")
        else:
            lineas.append(f"Salteé {n} movimientos que no figuran como aprobados.")

    return lineas


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
    omitidos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Construye el dict resultado_ia para un documento o imagen con extracción estructurada.
    Aplica las reglas de decisiones 3 (tope 10), 5 (avisos de duplicados y omitidos),
    7 (factura de servicios) y rendimientos (decisiones 4, 7 y 9).
    """
    lineas_omitidos = formatear_lineas_omitidos(omitidos)

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

    tiene_comunes = bool(entidades and entidades.get("monto") is not None)
    tiene_rendimientos = bool(rendimientos_a_anotar)

    # Caso: Solo hay omitidos (sin comunes ni rendimientos a anotar)
    if not tiene_comunes and not tiene_rendimientos and omitidos:
        msg_solo = "No encontré movimientos para anotar en la imagen."
        if lineas_omitidos:
            msg_solo += "\n" + "\n".join(lineas_omitidos)
        return {
            "intent": "duplicado",
            "confianza": 0.90,
            "slot_filling": False,
            "entidades": {},
            "respuesta_usuario": msg_solo,
        }

    if not tiene_comunes and not tiene_rendimientos:
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

    # Líneas de omitidos (Decisión 5: se agregan antes del aviso del tope de 10)
    if lineas_omitidos:
        for om_line in lineas_omitidos:
            texto_propuesta += f"\n{om_line}"

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
