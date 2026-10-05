"""
Servicio para actualización y consulta de tasas de entidades financieras públicas (ArgentinaDatos)
y cálculo del rendimiento devengado por saldo diario.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
from typing import Any, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entidades import ENTIDADES, entidad_de_billetera
from app.models.tasa_entidad import TasaEntidad

logger = logging.getLogger(__name__)

URL_FCI_OTROS = "https://api.argentinadatos.com/v1/finanzas/fci/otros/ultimo"
URL_FCI_MM_ULTIMO = "https://api.argentinadatos.com/v1/finanzas/fci/mercadoDinero/ultimo"
URL_FCI_MM_PENULTIMO = "https://api.argentinadatos.com/v1/finanzas/fci/mercadoDinero/penultimo"


@dataclass
class TasaEfectiva:
    tna: Optional[Decimal] = None
    origen: Optional[str] = None  # "manual", "automatica" o None
    clave: Optional[str] = None
    fecha_dato: Optional[date] = None
    vieja: bool = False
    tope: Optional[Decimal] = None
    entidad_id: Optional[str] = None


def _fetch_url_con_reintento(url: str, cliente: httpx.Client) -> Optional[list[dict]]:
    """Consulta una URL con timeout de 20s y 1 reintento. Devuelve la lista JSON o None."""
    for intento in range(2):
        try:
            resp = cliente.get(url, timeout=20.0)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return None
        except Exception as e:
            if intento == 0:
                logger.warning("Reintentando consulta a %s tras error: %s", url, e)
                continue
            logger.warning("Fallo definitivo al consultar %s: %s", url, e)
            return None
    return None


def actualizar_tasas(
    db: Session,
    cliente: Optional[httpx.Client] = None,
    commit: bool = True,
) -> dict:
    """
    Descarga y persiste las tasas públicas diarias de ArgentinaDatos:
    1. Cuentas remuneradas (fci/otros/ultimo).
    2. Fondos money market del catálogo (mercadoDinero/ultimo y /penultimo).
    Aplica upsert select + update/insert por (fuente, clave, fecha_dato).
    """
    cliente_creado = False
    if cliente is None:
        cliente = httpx.Client(timeout=20.0)
        cliente_creado = True

    try:
        ahora_utc = datetime.now(timezone.utc)
        fallos = 0
        total_urls = 3

        # 1. Cuentas remuneradas
        data_otros = _fetch_url_con_reintento(URL_FCI_OTROS, cliente)
        if data_otros is None:
            fallos += 1

        # 2. Money market último y penúltimo
        data_mm_u = _fetch_url_con_reintento(URL_FCI_MM_ULTIMO, cliente)
        if data_mm_u is None:
            fallos += 1

        data_mm_p = _fetch_url_con_reintento(URL_FCI_MM_PENULTIMO, cliente)
        if data_mm_p is None:
            fallos += 1

        if fallos == total_urls:
            raise RuntimeError("No se pudo obtener datos de ninguna URL de ArgentinaDatos.")

        res_cuentas = {"nuevas": 0, "actualizadas": 0}
        res_fci = {"nuevas": 0, "actualizadas": 0}

        # Procesar cuentas remuneradas
        if data_otros is not None:
            for item in data_otros:
                fondo = item.get("fondo")
                tna_raw = item.get("tna")
                fecha_raw = item.get("fecha")
                if not fondo or tna_raw is None or not fecha_raw:
                    continue

                try:
                    fecha_dato = date.fromisoformat(str(fecha_raw))
                    tna_val = (Decimal(str(tna_raw)) * Decimal("100")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                except Exception:
                    continue

                tope_raw = item.get("tope")
                if tope_raw is not None:
                    tope_dec = Decimal(str(tope_raw))
                    tope = None if tope_dec == Decimal("0") else tope_dec
                else:
                    tope = None

                cond_raw = item.get("condicionesCorto") or item.get("condiciones")
                condiciones = str(cond_raw)[:300] if cond_raw else None

                # Upsert
                fuente = "argentinadatos_cuentas"
                clave = str(fondo)
                stmt = select(TasaEntidad).where(
                    TasaEntidad.fuente == fuente,
                    TasaEntidad.clave == clave,
                    TasaEntidad.fecha_dato == fecha_dato,
                )
                existente = db.execute(stmt).scalar_one_or_none()
                if existente:
                    existente.tna = tna_val
                    existente.tope = tope
                    existente.condiciones = condiciones
                    existente.fecha_consulta = ahora_utc
                    res_cuentas["actualizadas"] += 1
                else:
                    nueva_fila = TasaEntidad(
                        fuente=fuente,
                        clave=clave,
                        tna=tna_val,
                        tope=tope,
                        condiciones=condiciones,
                        fecha_dato=fecha_dato,
                        vcp=None,
                        vcp_anterior=None,
                        fecha_dato_anterior=None,
                        fecha_consulta=ahora_utc,
                    )
                    db.add(nueva_fila)
                    res_cuentas["nuevas"] += 1

        # Procesar fondos FCI Money Market del catálogo
        if data_mm_u is not None and data_mm_p is not None:
            mm_u_map = {item.get("fondo"): item for item in data_mm_u if item.get("fondo")}
            mm_p_map = {item.get("fondo"): item for item in data_mm_p if item.get("fondo")}

            # Obtener fondos FCI configurados en el catálogo
            fondos_fci = set()
            for ent_info in ENTIDADES.values():
                fuente_info = ent_info.get("fuente")
                if fuente_info and isinstance(fuente_info, dict):
                    for opt in fuente_info.get("opciones", []):
                        if opt.get("fuente") == "argentinadatos_fci":
                            fondo_nombre = opt.get("clave")
                            if fondo_nombre:
                                fondos_fci.add(fondo_nombre)

            for fondo in fondos_fci:
                item_u = mm_u_map.get(fondo)
                item_p = mm_p_map.get(fondo)
                if not item_u or not item_p:
                    continue

                try:
                    fecha_u = date.fromisoformat(str(item_u.get("fecha")))
                    fecha_p = date.fromisoformat(str(item_p.get("fecha")))
                    vcp_u = Decimal(str(item_u.get("vcp")))
                    vcp_p = Decimal(str(item_p.get("vcp")))
                except Exception:
                    continue

                dias = (fecha_u - fecha_p).days
                if dias <= 0 or vcp_p <= Decimal("0"):
                    continue

                tna_fci = (
                    ((vcp_u - vcp_p) / vcp_p) / Decimal(str(dias)) * Decimal("365") * Decimal("100")
                ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

                fuente = "argentinadatos_fci"
                clave = fondo
                stmt = select(TasaEntidad).where(
                    TasaEntidad.fuente == fuente,
                    TasaEntidad.clave == clave,
                    TasaEntidad.fecha_dato == fecha_u,
                )
                existente = db.execute(stmt).scalar_one_or_none()
                if existente:
                    existente.tna = tna_fci
                    existente.tope = None
                    existente.condiciones = None
                    existente.vcp = vcp_u
                    existente.vcp_anterior = vcp_p
                    existente.fecha_dato_anterior = fecha_p
                    existente.fecha_consulta = ahora_utc
                    res_fci["actualizadas"] += 1
                else:
                    nueva_fila = TasaEntidad(
                        fuente=fuente,
                        clave=clave,
                        tna=tna_fci,
                        tope=None,
                        condiciones=None,
                        fecha_dato=fecha_u,
                        vcp=vcp_u,
                        vcp_anterior=vcp_p,
                        fecha_dato_anterior=fecha_p,
                        fecha_consulta=ahora_utc,
                    )
                    db.add(nueva_fila)
                    res_fci["nuevas"] += 1

        if commit:
            db.commit()
        else:
            db.flush()

        return {
            "argentinadatos_cuentas": res_cuentas,
            "argentinadatos_fci": res_fci,
        }
    finally:
        if cliente_creado:
            cliente.close()


def ultimas_tasas(db: Session, claves: list[str] | set[str]) -> dict[str, TasaEntidad]:
    """
    Retorna la fila de TasaEntidad más reciente (por fecha_dato) para cada clave solicitada.
    """
    if not claves:
        return {}
    claves_list = list(claves)
    stmt = (
        select(TasaEntidad)
        .where(TasaEntidad.clave.in_(claves_list))
        .order_by(TasaEntidad.clave, TasaEntidad.fecha_dato.desc(), TasaEntidad.fecha_consulta.desc())
    )
    filas = db.execute(stmt).scalars().all()
    resultado = {}
    for f in filas:
        if f.clave not in resultado:
            resultado[f.clave] = f
    return resultado


def tasa_efectiva(
    billetera: Any,
    tasas_por_clave: dict[str, TasaEntidad],
    saldo: Decimal,
    hoy: date,
) -> TasaEfectiva:
    """
    Determina la tasa efectiva aplicable a una billetera (función pura).
    - Si es efectivo: todo None.
    - Si tiene tasa manual: origen='manual', tna manual, sin tope.
    - Si no, resuelve clave según el catálogo y nivel:
      - nivel_tasa si está entre las opciones; si no, la base.
    - El tope es el de la opción si tiene uno; si no, el de la fila.
    - Si la tasa tiene más de 7 días de antigüedad: tna=None, vieja=True.
    """
    if getattr(billetera, "es_efectivo", False):
        return TasaEfectiva(
            tna=None, origen=None, clave=None, fecha_dato=None, vieja=False, tope=None, entidad_id=None
        )

    ent_id = entidad_de_billetera(billetera)

    # 1. Tasa manual establecida
    tna_manual = getattr(billetera, "tna", None)
    if tna_manual is not None:
        return TasaEfectiva(
            tna=tna_manual,
            origen="manual",
            clave=None,
            fecha_dato=None,
            vieja=False,
            tope=None,
            entidad_id=ent_id,
        )

    # 2. Tasa automática según catálogo
    if not ent_id or ent_id not in ENTIDADES:
        return TasaEfectiva(
            tna=None, origen=None, clave=None, fecha_dato=None, vieja=False, tope=None, entidad_id=ent_id
        )

    info_ent = ENTIDADES[ent_id]
    fuente_info = info_ent.get("fuente")
    if not fuente_info or not isinstance(fuente_info, dict):
        return TasaEfectiva(
            tna=None, origen=None, clave=None, fecha_dato=None, vieja=False, tope=None, entidad_id=ent_id
        )

    base = fuente_info.get("base")
    opciones = fuente_info.get("opciones", [])
    opciones_validas = [opt["clave"] for opt in opciones]
    nivel_billetera = getattr(billetera, "nivel_tasa", None)

    if nivel_billetera and nivel_billetera in opciones_validas:
        clave_elegida = nivel_billetera
    else:
        clave_elegida = base

    if not clave_elegida:
        return TasaEfectiva(
            tna=None, origen=None, clave=None, fecha_dato=None, vieja=False, tope=None, entidad_id=ent_id
        )

    opt_elegida = next((o for o in opciones if o["clave"] == clave_elegida), None)
    tope_fijo = opt_elegida.get("tope") if opt_elegida else None

    fila = tasas_por_clave.get(clave_elegida)
    if not fila:
        tope_inicial = Decimal(str(tope_fijo)) if tope_fijo is not None else None
        return TasaEfectiva(
            tna=None, origen=None, clave=clave_elegida, fecha_dato=None, vieja=False, tope=tope_inicial, entidad_id=ent_id
        )

    if tope_fijo is not None:
        tope = Decimal(str(tope_fijo))
    else:
        tope = fila.tope

    es_vieja = (hoy - fila.fecha_dato).days > 7
    if es_vieja:
        return TasaEfectiva(
            tna=None,
            origen="automatica",
            clave=clave_elegida,
            fecha_dato=fila.fecha_dato,
            vieja=True,
            tope=tope,
            entidad_id=ent_id,
        )

    return TasaEfectiva(
        tna=fila.tna,
        origen="automatica",
        clave=clave_elegida,
        fecha_dato=fila.fecha_dato,
        vieja=False,
        tope=tope,
        entidad_id=ent_id,
    )


def saldos_diarios(
    saldo_hoy: Decimal,
    netos_por_dia: dict[date, Decimal],
    desde: date,
    hoy: date,
) -> dict[date, Decimal]:
    """
    Calcula el saldo al cierre de cada día d en [desde, hoy − 1], caminando hacia atrás.
    Regla: saldo(d − 1) = saldo(d) − neto(d), con saldo(hoy) = saldo_hoy
    y neto = entradas − salidas + ajustes del día.
    """
    if desde >= hoy:
        return {}

    # Caminamos hacia atrás desde hoy
    saldos_al_cierre: dict[date, Decimal] = {}
    saldo_referencia = saldo_hoy

    # d_cursor representa el día del que conocemos o calculamos el saldo antes de sus movimientos
    # Para el cierre de hoy - 1, necesitamos saldo_hoy - neto(hoy)
    d = hoy
    while d > desde:
        neto_d = netos_por_dia.get(d, Decimal("0.00"))
        saldo_cierre_anterior = saldo_referencia - neto_d
        saldos_al_cierre[d - timedelta(days=1)] = saldo_cierre_anterior
        saldo_referencia = saldo_cierre_anterior
        d -= timedelta(days=1)

    return saldos_al_cierre


def rendimiento_por_saldos(
    saldos: dict[date, Decimal],
    tna: Decimal,
    tope: Optional[Decimal] = None,
) -> Decimal:
    """
    Calcula la suma de los rendimientos diarios devengados:
    suma de max(0, min(saldo_d, tope)) × tna / 100 / 365 de cada día.
    Redondea a 0.01 (ROUND_HALF_UP) solo al final.
    """
    if not saldos or tna <= Decimal("0"):
        return Decimal("0.00")

    tna_factor = tna / Decimal("100") / Decimal("365")
    acumulador = Decimal("0")

    for d in sorted(saldos.keys()):
        saldo_d = saldos[d]
        if saldo_d <= Decimal("0"):
            continue

        base_remunerable = min(saldo_d, tope) if tope is not None else saldo_d
        if base_remunerable > Decimal("0"):
            acumulador += base_remunerable * tna_factor

    return acumulador.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
