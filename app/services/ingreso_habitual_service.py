"""
Módulo de Ingreso Habitual de Argentum.
Ubicación: app/services/ingreso_habitual_service.py

PROPÓSITO Y REGLAS DE NEGOCIO:
Establece UN SOLO criterio determinístico y universal para calcular el
"ingreso habitual" de un usuario en una moneda determinada, y el "ingreso esperado"
de un ciclo financiero.

REGLAS DE DECISIÓN (Fase 2b):
1. Concepto:
   Es un solo monto por usuario y por moneda: lo que cobra normalmente en un ciclo,
   sin aguinaldo ni extras sueltos.
2. Fuentes:
   - Se agrupan los ingresos (según es_ingreso de definiciones_service) por categoría y subcategoría.
   - Aguinaldo: los movimientos de la subcategoría "Aguinaldo" nunca son fuente; se tratan aparte.
   - Una fuente es habitual si aparece en al menos el 80% de los ciclos completos de la ventana.
     * La ventana son los últimos 6 ciclos completos o, si hay menos historia, los ciclos completos
       desde el primer ingreso.
     * Hacen falta mínimo 3 ciclos completos.
   - Si una fuente tiene 2 o más cobros por ciclo y, en la mediana, el segundo cobro es menos de
     la mitad del primero, se separa en dos series (el cobro mayor de cada ciclo y el siguiente).
     Ejemplo: jubilación + bono.
3. Tipo de cada fuente habitual y su monto:
   - Regular: un cobro por ciclo y variación chica (coeficiente de variación de los totales de la
     ventana de hasta 0,10). Monto = el último cobro, contando el ciclo actual.
   - Intermitente: varios cobros por ciclo con total estable (coeficiente de variación de hasta 0,15).
     Monto = mediana de los totales de los últimos 6 ciclos completos.
   - Variable: cualquier otra fuente habitual.
     Monto = promedio de los 3 totales más bajos de los últimos 12 ciclos completos (con menos de 4
     ciclos, el más bajo).
4. Ingreso habitual del usuario:
   - Es la suma de sus fuentes habituales.
   - El tipo informado es el más prudente entre sus fuentes: variable, después intermitente, después regular.
5. Sin datos:
   - Si no hay fuentes habituales, el ingreso habitual es "sin datos" y el monto queda vacío (None).
   - Se informa el motivo: "sin_ingresos", "pocos_ciclos" o "irregular".
   - NO se inventa ningún número.
6. Aguinaldo:
   - Solo para quien cobró un aguinaldo en los últimos 12 meses.
   - Monto esperado = 50% del ingreso regular actual de su fuente "Empleo / Sueldo".
   - Fecha esperada = el mismo día y mes de su último aguinaldo de ese semestre; si no hay, 30/06 o 18/12.
7. Ingreso esperado de un ciclo:
   - Lo ya cobrado en el ciclo (todo es_ingreso), más, por cada fuente habitual, lo que falte cobrar
     (su monto menos lo ya cobrado de esa fuente en el ciclo, si da positivo), más el aguinaldo pendiente
     si su fecha esperada cae en el ciclo y todavía no se cobró.
   - Si el usuario está "sin datos", el ingreso esperado es None.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.transaccion import Transaccion
from app.models.usuario import Moneda, Usuario
from app.services.definiciones_service import (
    ContextoDefiniciones,
    cargar_contexto,
    es_ingreso,
)
from app.utils.fecha import hoy_argentina


# ==============================================================================
# ESTRUCTURAS DE DATOS
# ==============================================================================

@dataclass
class FuenteIngreso:
    """Detalle de una fuente de ingreso habitual identificada."""
    categoria_id: Optional[str]
    subcategoria_id: Optional[str]
    categoria_nombre: str
    subcategoria_nombre: str
    tipo: str  # "regular", "intermitente", "variable"
    monto: Decimal
    ciclos_presentes: int
    es_desdoblada: bool = False
    orden_desdoble: int = 1  # 1: cobro principal / mayor, 2: cobro secundario / siguiente


@dataclass
class ResultadoIngresoHabitual:
    """Resultado consolidado del cálculo de ingreso habitual para una moneda."""
    moneda: str
    tipo: Optional[str]  # "regular", "intermitente", "variable", "sin_datos"
    monto: Optional[Decimal]
    motivo_sin_datos: Optional[str]  # "sin_ingresos", "pocos_ciclos", "irregular", None
    fuentes: List[FuenteIngreso] = field(default_factory=list)
    tiene_aguinaldo: bool = False
    proximo_aguinaldo_fecha: Optional[date] = None
    proximo_aguinaldo_monto: Optional[Decimal] = None

    def a_dict(self) -> Dict[str, Any]:
        return {
            "moneda": self.moneda,
            "tipo": self.tipo,
            "monto": float(self.monto) if self.monto is not None else None,
            "motivo_sin_datos": self.motivo_sin_datos,
            "fuentes": [
                {
                    "categoria_id": f.categoria_id,
                    "subcategoria_id": f.subcategoria_id,
                    "categoria_nombre": f.categoria_nombre,
                    "subcategoria_nombre": f.subcategoria_nombre,
                    "tipo": f.tipo,
                    "monto": float(f.monto),
                    "ciclos_presentes": f.ciclos_presentes,
                    "es_desdoblada": f.es_desdoblada,
                    "orden_desdoble": f.orden_desdoble,
                }
                for f in self.fuentes
            ],
            "tiene_aguinaldo": self.tiene_aguinaldo,
            "proximo_aguinaldo_fecha": self.proximo_aguinaldo_fecha.isoformat() if self.proximo_aguinaldo_fecha else None,
            "proximo_aguinaldo_monto": float(self.proximo_aguinaldo_monto) if self.proximo_aguinaldo_monto is not None else None,
        }


# ==============================================================================
# FUNCIONES AUXILIARES ESTADÍSTICAS
# ==============================================================================

def calcular_estadisticas_serie(valores: List[Decimal]) -> Tuple[Decimal, Decimal, Decimal]:
    """Calcula promedio, desviación estándar poblacional y coeficiente de variación (CV)."""
    if not valores:
        return Decimal("0"), Decimal("0"), Decimal("0")
    n = len(valores)
    promedio = sum(valores) / Decimal(str(n))
    if n == 1 or promedio == Decimal("0"):
        return promedio, Decimal("0"), Decimal("0")
    varianza = sum((v - promedio) ** 2 for v in valores) / Decimal(str(n))
    desv = Decimal(str(math.sqrt(float(varianza)))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    cv = (desv / promedio) if promedio > Decimal("0") else Decimal("0")
    return promedio, desv, cv


def calcular_mediana(valores: List[Decimal]) -> Decimal:
    """Calcula la mediana de una lista de números Decimal."""
    if not valores:
        return Decimal("0")
    s = sorted(valores)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return ((s[n // 2 - 1] + s[n // 2]) / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def es_movimiento_aguinaldo(tx: Any) -> bool:
    """Verifica si un movimiento pertenece a la subcategoría Aguinaldo."""
    subcat = getattr(tx, "subcategoria", None)
    nombre = getattr(subcat, "nombre", "") or ""
    return nombre.strip().lower() == "aguinaldo"


# ==============================================================================
# 4.1 FUNCIÓN PURA EN MEMORIA
# ==============================================================================

def calcular_ingreso_habitual_en_memoria(
    movimientos: List[Any],
    ciclos: List[Tuple[date, date]],
    hoy: date,
    ctx: ContextoDefiniciones,
    moneda: Optional[Moneda | str] = None,
) -> Dict[str, ResultadoIngresoHabitual] | ResultadoIngresoHabitual:
    """
    Función pura en memoria que evalúa el ingreso habitual según las reglas unificadas.
    Si se especifica `moneda`, retorna directamente el `ResultadoIngresoHabitual` para dicha moneda.
    Si `moneda` es None, retorna un diccionario con claves {"ars": ..., "usd": ...}.
    """
    if moneda is not None:
        m_str = moneda.value if hasattr(moneda, "value") else str(moneda)
        return _calcular_ingreso_habitual_moneda_pura(movimientos, ciclos, hoy, ctx, m_str)

    res_ars = _calcular_ingreso_habitual_moneda_pura(movimientos, ciclos, hoy, ctx, "ARS")
    res_usd = _calcular_ingreso_habitual_moneda_pura(movimientos, ciclos, hoy, ctx, "USD")
    return {
        "ars": res_ars,
        "usd": res_usd,
    }


def _calcular_ingreso_habitual_moneda_pura(
    movimientos: List[Any],
    ciclos: List[Tuple[date, date]],
    hoy: date,
    ctx: ContextoDefiniciones,
    moneda_str: str,
) -> ResultadoIngresoHabitual:
    """Lógica determinística pura para una moneda dada."""
    # 1. Filtrar ingresos válidos de esta moneda según es_ingreso(tx, ctx)
    txs_ingreso = [
        tx for tx in movimientos
        if (getattr(tx, "moneda", None).value if hasattr(getattr(tx, "moneda", None), "value") else str(getattr(tx, "moneda", None))) == moneda_str
        and es_ingreso(tx, ctx)
    ]

    if not txs_ingreso:
        return ResultadoIngresoHabitual(
            moneda=moneda_str,
            tipo="sin_datos",
            monto=None,
            motivo_sin_datos="sin_ingresos",
            fuentes=[],
            tiene_aguinaldo=False,
            proximo_aguinaldo_fecha=None,
            proximo_aguinaldo_monto=None,
        )

    # 2. Separar movimientos de aguinaldo
    txs_aguinaldo = [tx for tx in txs_ingreso if es_movimiento_aguinaldo(tx)]
    txs_base = [tx for tx in txs_ingreso if not es_movimiento_aguinaldo(tx)]

    # 3. Determinar ciclos completos y ciclo actual
    # Un ciclo es completo si ya finalizó respecto a hoy (fecha_fin <= hoy)
    ciclos_completos = [c for c in ciclos if c[1] <= hoy]
    ciclo_actual = next((c for c in ciclos if c[0] <= hoy <= c[1]), None)

    # Ventana de ciclos completos:
    # "La ventana son los últimos 6 ciclos completos o, si hay menos historia, los ciclos completos desde el primer ingreso. Hacen falta mínimo 3 ciclos completos."
    fecha_primer_ingreso = min(tx.fecha for tx in txs_ingreso)
    ciclos_desde_primer_ingreso = [
        c for c in ciclos_completos
        if c[1] >= fecha_primer_ingreso or (c[0] <= fecha_primer_ingreso <= c[1])
    ]

    if len(ciclos_desde_primer_ingreso) < 3:
        return ResultadoIngresoHabitual(
            moneda=moneda_str,
            tipo="sin_datos",
            monto=None,
            motivo_sin_datos="pocos_ciclos",
            fuentes=[],
            tiene_aguinaldo=False,
            proximo_aguinaldo_fecha=None,
            proximo_aguinaldo_monto=None,
        )

    # Tomar hasta los últimos 6 ciclos completos de la historia disponible
    ventana_ciclos = ciclos_completos[-6:] if len(ciclos_completos) >= 6 else ciclos_desde_primer_ingreso
    n_ventana = len(ventana_ciclos)

    # 4. Agrupar transacciones base por categoría y subcategoría
    grupos_txs: Dict[Tuple[str, str], List[Any]] = {}
    for tx in txs_base:
        cat_nom = getattr(getattr(tx, "categoria", None), "nombre", None) or "Sin Categoría"
        subcat_nom = getattr(getattr(tx, "subcategoria", None), "nombre", None) or "General"
        clave = (cat_nom, subcat_nom)
        grupos_txs.setdefault(clave, []).append(tx)

    fuentes_candidatas_series: List[Dict[str, Any]] = []

    for (cat_nom, subcat_nom), txs_fuente in grupos_txs.items():
        # Evaluar si la fuente tiene 2 o más cobros por ciclo y se debe desdoblar
        cobros_por_ciclo: Dict[int, List[Decimal]] = {}
        for idx, (c_ini, c_fin) in enumerate(ventana_ciclos):
            txs_c = [t for t in txs_fuente if c_ini <= t.fecha <= c_fin]
            if txs_c:
                cobros_por_ciclo[idx] = sorted([t.monto for t in txs_c], reverse=True)

        ciclos_con_multiples = [m for m in cobros_por_ciclo.values() if len(m) >= 2]
        desdoblar = False
        if len(ciclos_con_multiples) >= 2:
            ratios = [m[1] / m[0] for m in ciclos_con_multiples if m[0] > 0]
            if ratios and calcular_mediana(ratios) < Decimal("0.50"):
                desdoblar = True

        cat_id = getattr(txs_fuente[0], "categoria_id", None)
        subcat_id = getattr(txs_fuente[0], "subcategoria_id", None)

        if desdoblar:
            # Serie 1: cobro mayor de cada ciclo
            fuentes_candidatas_series.append({
                "categoria_nombre": cat_nom,
                "subcategoria_nombre": subcat_nom,
                "categoria_id": cat_id,
                "subcategoria_id": subcat_id,
                "es_desdoblada": True,
                "orden_desdoble": 1,
                "selector": lambda txs_en_c: [sorted(txs_en_c, key=lambda x: x.monto, reverse=True)[0]] if txs_en_c else [],
                "txs": txs_fuente,
            })
            # Serie 2: segundo cobro de cada ciclo
            fuentes_candidatas_series.append({
                "categoria_nombre": cat_nom,
                "subcategoria_nombre": subcat_nom,
                "categoria_id": cat_id,
                "subcategoria_id": subcat_id,
                "es_desdoblada": True,
                "orden_desdoble": 2,
                "selector": lambda txs_en_c: [sorted(txs_en_c, key=lambda x: x.monto, reverse=True)[1]] if len(txs_en_c) >= 2 else [],
                "txs": txs_fuente,
            })
        else:
            fuentes_candidatas_series.append({
                "categoria_nombre": cat_nom,
                "subcategoria_nombre": subcat_nom,
                "categoria_id": cat_id,
                "subcategoria_id": subcat_id,
                "es_desdoblada": False,
                "orden_desdoble": 1,
                "selector": lambda txs_en_c: txs_en_c,
                "txs": txs_fuente,
            })

    # 5. Evaluar habitualidad y tipo de cada serie en la ventana
    fuentes_habituales: List[FuenteIngreso] = []

    for cand in fuentes_candidatas_series:
        totales_ventana: List[Decimal] = []
        cant_cobros_ventana: List[int] = []
        ciclos_presente = 0

        for (c_ini, c_fin) in ventana_ciclos:
            txs_c = [t for t in cand["txs"] if c_ini <= t.fecha <= c_fin]
            txs_sel = cand["selector"](txs_c)
            if txs_sel:
                ciclos_presente += 1
                tot = sum(t.monto for t in txs_sel)
                totales_ventana.append(tot)
                cant_cobros_ventana.append(len(txs_sel))

        # Habitual si aparece en al menos el 80% de los ciclos completos de la ventana
        ratio_presencia = Decimal(str(ciclos_presente)) / Decimal(str(n_ventana))
        if ratio_presencia < Decimal("0.80"):
            continue

        # Clasificar tipo y monto
        _, _, cv_vent = calcular_estadisticas_serie(totales_ventana)
        max_cobros_ciclo = max(cant_cobros_ventana) if cant_cobros_ventana else 0

        # Obtener el último cobro contando el ciclo actual
        txs_todos = cand["txs"]
        txs_act_sel = []
        if ciclo_actual:
            txs_actual = [t for t in txs_todos if ciclo_actual[0] <= t.fecha <= ciclo_actual[1]]
            txs_act_sel = cand["selector"](txs_actual)

        if txs_act_sel:
            ultimo_cobro = sorted(txs_act_sel, key=lambda x: x.fecha)[-1].monto
        else:
            txs_pasadas_sel: List[Any] = []
            for (c_ini, c_fin) in ventana_ciclos:
                txs_c = [t for t in txs_todos if c_ini <= t.fecha <= c_fin]
                txs_pasadas_sel.extend(cand["selector"](txs_c))
            ultimo_cobro = sorted(txs_pasadas_sel, key=lambda x: x.fecha)[-1].monto if txs_pasadas_sel else Decimal("0")

        if max_cobros_ciclo <= 1 and cv_vent <= Decimal("0.10"):
            tipo_fuente = "regular"
            monto_fuente = ultimo_cobro
        elif cv_vent <= Decimal("0.15"):
            tipo_fuente = "intermitente"
            monto_fuente = calcular_mediana(totales_ventana)
        else:
            tipo_fuente = "variable"
            ultimos_12 = ciclos_completos[-12:]
            totales_12: List[Decimal] = []
            for (c_ini, c_fin) in ultimos_12:
                txs_c = [t for t in cand["txs"] if c_ini <= t.fecha <= c_fin]
                txs_sel = cand["selector"](txs_c)
                if txs_sel:
                    totales_12.append(sum(t.monto for t in txs_sel))
            if len(totales_12) >= 4:
                bajos = sorted(totales_12)[:3]
                monto_fuente = (sum(bajos) / Decimal("3")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            elif totales_12:
                monto_fuente = min(totales_12)
            else:
                monto_fuente = Decimal("0")

        fuentes_habituales.append(FuenteIngreso(
            categoria_id=str(cand["categoria_id"]) if cand["categoria_id"] else None,
            subcategoria_id=str(cand["subcategoria_id"]) if cand["subcategoria_id"] else None,
            categoria_nombre=cand["categoria_nombre"],
            subcategoria_nombre=cand["subcategoria_nombre"],
            tipo=tipo_fuente,
            monto=monto_fuente,
            ciclos_presentes=ciclos_presente,
            es_desdoblada=cand["es_desdoblada"],
            orden_desdoble=cand["orden_desdoble"],
        ))

    if not fuentes_habituales:
        return ResultadoIngresoHabitual(
            moneda=moneda_str,
            tipo="sin_datos",
            monto=None,
            motivo_sin_datos="irregular",
            fuentes=[],
            tiene_aguinaldo=False,
            proximo_aguinaldo_fecha=None,
            proximo_aguinaldo_monto=None,
        )

    # 6. Ingreso habitual del usuario
    monto_usuario = sum((f.monto for f in fuentes_habituales), Decimal("0"))
    tipos_fuentes = {f.tipo for f in fuentes_habituales}
    if "variable" in tipos_fuentes:
        tipo_usuario = "variable"
    elif "intermitente" in tipos_fuentes:
        tipo_usuario = "intermitente"
    else:
        tipo_usuario = "regular"

    # 7. Aguinaldo
    limite_12m = hoy - timedelta(days=365)
    aguinaldos_12m = [t for t in txs_aguinaldo if t.fecha >= limite_12m]
    tiene_aguinaldo = len(aguinaldos_12m) > 0

    proximo_aguinaldo_fecha = None
    proximo_aguinaldo_monto = None

    if tiene_aguinaldo:
        # 50% del ingreso regular actual de su fuente "Empleo / Sueldo"
        fuente_sueldo = next(
            (f for f in fuentes_habituales if f.tipo == "regular" and (
                "sueldo" in f.subcategoria_nombre.lower() or "empleo" in f.categoria_nombre.lower()
            )),
            None
        )
        if fuente_sueldo:
            monto_base_sac = fuente_sueldo.monto
        else:
            fuente_reg = next((f for f in fuentes_habituales if f.tipo == "regular"), None)
            monto_base_sac = fuente_reg.monto if fuente_reg else Decimal("0")

        proximo_aguinaldo_monto = (monto_base_sac * Decimal("0.50")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Semestre próximo y fecha esperada
        if hoy.month <= 6:
            semestre_objetivo = 1
            ag_sem = [t for t in txs_aguinaldo if t.fecha.month in (5, 6, 7) and t.fecha <= hoy]
            if ag_sem:
                ultimo_ag = sorted(ag_sem, key=lambda x: x.fecha)[-1]
                dia, mes = ultimo_ag.fecha.day, ultimo_ag.fecha.month
            else:
                dia, mes = 30, 6
            proximo_aguinaldo_fecha = date(hoy.year, mes, dia)
            if proximo_aguinaldo_fecha < hoy:
                semestre_objetivo = 2
        else:
            semestre_objetivo = 2

        if semestre_objetivo == 2:
            ag_sem = [t for t in txs_aguinaldo if t.fecha.month in (11, 12, 1) and t.fecha <= hoy]
            if ag_sem:
                ultimo_ag = sorted(ag_sem, key=lambda x: x.fecha)[-1]
                dia, mes = ultimo_ag.fecha.day, ultimo_ag.fecha.month
            else:
                dia, mes = 18, 12
            proximo_aguinaldo_fecha = date(hoy.year, mes, dia)
            if proximo_aguinaldo_fecha < hoy:
                ag_sem1 = [t for t in txs_aguinaldo if t.fecha.month in (5, 6, 7) and t.fecha <= hoy]
                if ag_sem1:
                    u = sorted(ag_sem1, key=lambda x: x.fecha)[-1]
                    dia1, mes1 = u.fecha.day, u.fecha.month
                else:
                    dia1, mes1 = 30, 6
                proximo_aguinaldo_fecha = date(hoy.year + 1, mes1, dia1)

    return ResultadoIngresoHabitual(
        moneda=moneda_str,
        tipo=tipo_usuario,
        monto=monto_usuario,
        motivo_sin_datos=None,
        fuentes=fuentes_habituales,
        tiene_aguinaldo=tiene_aguinaldo,
        proximo_aguinaldo_fecha=proximo_aguinaldo_fecha,
        proximo_aguinaldo_monto=proximo_aguinaldo_monto,
    )


# ==============================================================================
# 4.2 FUNCIÓN ADAPTADORA CON BASE DE DATOS
# ==============================================================================

def obtener_ingreso_habitual(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
    ctx: Optional[ContextoDefiniciones] = None,
    txs_previa: Optional[List[Transaccion]] = None,
    moneda: Optional[Moneda | str] = None,
) -> Dict[str, ResultadoIngresoHabitual] | ResultadoIngresoHabitual:
    """
    Carga los datos necesarios desde la base de datos (o reutiliza `txs_previa` y `ctx` si se proveen)
    y evalúa el ingreso habitual mediante la función pura.
    """
    from app.services.dashboard_service import get_ciclo_fechas

    ref_hoy = hoy or hoy_argentina()
    ref_ctx = ctx or cargar_contexto(db, usuario.id, ref_hoy)

    if txs_previa is not None:
        txs = txs_previa
    else:
        txs = db.execute(
            select(Transaccion)
            .options(
                joinedload(Transaccion.categoria),
                joinedload(Transaccion.subcategoria),
                joinedload(Transaccion.billetera),
            )
            .where(Transaccion.usuario_id == usuario.id)
            .order_by(Transaccion.fecha.asc())
        ).scalars().all()

    # Construir historial de ciclos hacia atrás (14 ciclos para cubrir 12 completos)
    ciclos: List[Tuple[date, date]] = []
    puntero_fecha = ref_hoy
    for _ in range(14):
        c_ini, c_fin = get_ciclo_fechas(usuario, puntero_fecha)
        ciclos.append((c_ini, c_fin))
        puntero_fecha = c_ini - timedelta(days=1)
    # Orden ascendente de ciclos
    ciclos = sorted(list(set(ciclos)), key=lambda x: x[0])

    return calcular_ingreso_habitual_en_memoria(
        movimientos=txs,
        ciclos=ciclos,
        hoy=ref_hoy,
        ctx=ref_ctx,
        moneda=moneda,
    )


# ==============================================================================
# 4.3 FUNCIÓN DE INGRESO ESPERADO DE UN CICLO
# ==============================================================================

def calcular_ingreso_esperado_ciclo(
    db: Session,
    usuario: Usuario,
    fecha_inicio: date,
    fecha_fin: date,
    moneda: Moneda | str,
    hoy: Optional[date] = None,
    ctx: Optional[ContextoDefiniciones] = None,
    txs_previa: Optional[List[Transaccion]] = None,
    ingreso_habitual_previo: Optional[ResultadoIngresoHabitual] = None,
) -> Optional[Decimal]:
    """
    Calcula el ingreso esperado de un ciclo financiero específico según las DECISIONES:
    - Lo ya cobrado en el ciclo (todo es_ingreso).
    - Más, por cada fuente habitual, lo que falte cobrar (su monto menos lo ya cobrado de esa fuente, si > 0).
    - Más el aguinaldo pendiente si su fecha esperada cae en el ciclo y todavía no se cobró.
    - Si el usuario está "sin datos", retorna None.
    """
    ref_hoy = hoy or hoy_argentina()
    ref_ctx = ctx or cargar_contexto(db, usuario.id, ref_hoy)
    moneda_str = moneda.value if hasattr(moneda, "value") else str(moneda)

    # 1. Obtener o usar ingreso habitual
    if ingreso_habitual_previo is not None:
        ing_hab = ingreso_habitual_previo
    else:
        resultado = obtener_ingreso_habitual(
            db=db,
            usuario=usuario,
            hoy=ref_hoy,
            ctx=ref_ctx,
            txs_previa=txs_previa,
            moneda=moneda,
        )
        ing_hab = resultado if isinstance(resultado, ResultadoIngresoHabitual) else resultado[moneda_str.lower()]

    if ing_hab.monto is None or ing_hab.tipo == "sin_datos":
        return None

    # 2. Transacciones del ciclo evaluado
    if txs_previa is not None:
        txs_ciclo = [
            t for t in txs_previa
            if fecha_inicio <= t.fecha <= fecha_fin
        ]
    else:
        txs_ciclo = db.execute(
            select(Transaccion)
            .options(
                joinedload(Transaccion.categoria),
                joinedload(Transaccion.subcategoria),
                joinedload(Transaccion.billetera),
            )
            .where(
                Transaccion.usuario_id == usuario.id,
                Transaccion.fecha >= fecha_inicio,
                Transaccion.fecha <= fecha_fin,
            )
        ).scalars().all()

    # Transacciones válidas de ingreso en este ciclo
    txs_ing_ciclo = [
        t for t in txs_ciclo
        if (getattr(t, "moneda", None).value if hasattr(getattr(t, "moneda", None), "value") else str(getattr(t, "moneda", None))) == moneda_str
        and es_ingreso(t, ref_ctx)
    ]

    # Lo ya cobrado en el ciclo (todo es_ingreso)
    ya_cobrado_total = sum((t.monto for t in txs_ing_ciclo), Decimal("0"))

    # Lo que falte cobrar de cada fuente habitual
    pendiente_fuentes = Decimal("0")
    for f in ing_hab.fuentes:
        # Filtrar movimientos de esta fuente en el ciclo
        cobrado_f = Decimal("0")
        for t in txs_ing_ciclo:
            if es_movimiento_aguinaldo(t):
                continue
            c_nom = getattr(getattr(t, "categoria", None), "nombre", None) or "Sin Categoría"
            sc_nom = getattr(getattr(t, "subcategoria", None), "nombre", None) or "General"
            if c_nom.lower() == f.categoria_nombre.lower() and sc_nom.lower() == f.subcategoria_nombre.lower():
                cobrado_f += t.monto

        # Si no está desdoblada, compara el total cobrado contra el monto de la fuente
        # Si está desdoblada y hay múltiples cobros, cobrado_f contiene ambos; pero al sumar ambas fuentes
        # la suma de montos cubre el total esperado.
        # Para ser precisos con fuentes desdobladas:
        if not f.es_desdoblada:
            falta_f = max(Decimal("0"), f.monto - cobrado_f)
            pendiente_fuentes += falta_f

    # Si hay fuentes desdobladas, calculamos su pendiente conjunto por categoría/subcategoría
    fuentes_desdobladas = [f for f in ing_hab.fuentes if f.es_desdoblada]
    if fuentes_desdobladas:
        agrup_desdoble: Dict[Tuple[str, str], Decimal] = {}
        for f in fuentes_desdobladas:
            clave = (f.categoria_nombre.lower(), f.subcategoria_nombre.lower())
            agrup_desdoble[clave] = agrup_desdoble.get(clave, Decimal("0")) + f.monto

        for (c_nom_l, sc_nom_l), monto_total_f in agrup_desdoble.items():
            cobrado_conjunto = Decimal("0")
            for t in txs_ing_ciclo:
                if es_movimiento_aguinaldo(t):
                    continue
                c_nom = (getattr(getattr(t, "categoria", None), "nombre", None) or "Sin Categoría").lower()
                sc_nom = (getattr(getattr(t, "subcategoria", None), "nombre", None) or "General").lower()
                if c_nom == c_nom_l and sc_nom == sc_nom_l:
                    cobrado_conjunto += t.monto
            falta_conjunta = max(Decimal("0"), monto_total_f - cobrado_conjunto)
            pendiente_fuentes += falta_conjunta

    # Aguinaldo pendiente
    aguinaldo_pendiente = Decimal("0")
    if ing_hab.tiene_aguinaldo and ing_hab.proximo_aguinaldo_fecha and ing_hab.proximo_aguinaldo_monto:
        f_aguinaldo = ing_hab.proximo_aguinaldo_fecha
        if fecha_inicio <= f_aguinaldo <= fecha_fin:
            # Verificar si ya se cobró en el ciclo
            ya_cobro_aguinaldo = any(es_movimiento_aguinaldo(t) for t in txs_ing_ciclo)
            if not ya_cobro_aguinaldo:
                aguinaldo_pendiente = ing_hab.proximo_aguinaldo_monto

    ingreso_esperado = ya_cobrado_total + pendiente_fuentes + aguinaldo_pendiente
    return ingreso_esperado.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calcular_ingreso_esperado_ciclo_en_memoria(
    movimientos_ciclo: List[Any],
    fecha_inicio: date,
    fecha_fin: date,
    ingreso_habitual: ResultadoIngresoHabitual,
    hoy: date,
    ctx: ContextoDefiniciones,
) -> Optional[Decimal]:
    """Versión pura en memoria para calcular el ingreso esperado de un ciclo."""
    if ingreso_habitual.monto is None or ingreso_habitual.tipo == "sin_datos":
        return None

    moneda_str = ingreso_habitual.moneda

    txs_ing_ciclo = [
        t for t in movimientos_ciclo
        if (getattr(t, "moneda", None).value if hasattr(getattr(t, "moneda", None), "value") else str(getattr(t, "moneda", None))) == moneda_str
        and es_ingreso(t, ctx)
    ]

    ya_cobrado_total = sum((t.monto for t in txs_ing_ciclo), Decimal("0"))

    pendiente_fuentes = Decimal("0")
    for f in ingreso_habitual.fuentes:
        if not f.es_desdoblada:
            cobrado_f = Decimal("0")
            for t in txs_ing_ciclo:
                if es_movimiento_aguinaldo(t):
                    continue
                c_nom = getattr(getattr(t, "categoria", None), "nombre", None) or "Sin Categoría"
                sc_nom = getattr(getattr(t, "subcategoria", None), "nombre", None) or "General"
                if c_nom.lower() == f.categoria_nombre.lower() and sc_nom.lower() == f.subcategoria_nombre.lower():
                    cobrado_f += t.monto
            pendiente_fuentes += max(Decimal("0"), f.monto - cobrado_f)

    fuentes_desdobladas = [f for f in ingreso_habitual.fuentes if f.es_desdoblada]
    if fuentes_desdobladas:
        agrup_desdoble: Dict[Tuple[str, str], Decimal] = {}
        for f in fuentes_desdobladas:
            clave = (f.categoria_nombre.lower(), f.subcategoria_nombre.lower())
            agrup_desdoble[clave] = agrup_desdoble.get(clave, Decimal("0")) + f.monto

        for (c_nom_l, sc_nom_l), monto_total_f in agrup_desdoble.items():
            cobrado_conjunto = Decimal("0")
            for t in txs_ing_ciclo:
                if es_movimiento_aguinaldo(t):
                    continue
                c_nom = (getattr(getattr(t, "categoria", None), "nombre", None) or "Sin Categoría").lower()
                sc_nom = (getattr(getattr(t, "subcategoria", None), "nombre", None) or "General").lower()
                if c_nom == c_nom_l and sc_nom == sc_nom_l:
                    cobrado_conjunto += t.monto
            falta_conjunta = max(Decimal("0"), monto_total_f - cobrado_conjunto)
            pendiente_fuentes += falta_conjunta

    aguinaldo_pendiente = Decimal("0")
    if ingreso_habitual.tiene_aguinaldo and ingreso_habitual.proximo_aguinaldo_fecha and ingreso_habitual.proximo_aguinaldo_monto:
        f_aguinaldo = ingreso_habitual.proximo_aguinaldo_fecha
        if fecha_inicio <= f_aguinaldo <= fecha_fin:
            ya_cobro_aguinaldo = any(es_movimiento_aguinaldo(t) for t in txs_ing_ciclo)
            if not ya_cobro_aguinaldo:
                aguinaldo_pendiente = ingreso_habitual.proximo_aguinaldo_monto

    ingreso_esperado = ya_cobrado_total + pendiente_fuentes + aguinaldo_pendiente
    return ingreso_esperado.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
