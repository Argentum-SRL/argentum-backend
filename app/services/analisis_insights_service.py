from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from app.utils.formato import formatear_monto


def armar_insights(
    desglose_categorias: List[Dict[str, Any]],
    variacion_gasto_vs_ant: Optional[float],
    es_ult_en_curso: bool,
    ciclos_completos: List[Dict[str, Any]],
    tasa_ahorro_ventana: Optional[float],
    modo: str,
    solo_gastos: bool = False,
) -> List[Dict[str, Any]]:
    """
    Construye la lista de insights deterministas para la pantalla de Análisis.
    No realiza consultas a la base de datos.
    """
    insights: List[Dict[str, Any]] = []

    # 1. Categoría líder
    if desglose_categorias:
        top_cat = desglose_categorias[0]
        insights.append({
            "id": "top-categoria",
            "tipo": "info",
            "titulo": f"{top_cat['categoria_nombre']} lidera tus gastos ({top_cat['porcentaje_del_total']}%)",
            "descripcion": f"Son {top_cat['cantidad_movimientos']} movimientos en estos ciclos.",
        })

    # 2. Variación de gasto vs. ciclo anterior
    if variacion_gasto_vs_ant is not None:
        if variacion_gasto_vs_ant <= -5:
            if es_ult_en_curso:
                titulo_var = (
                    f"En lo que va del ciclo gastaste {abs(variacion_gasto_vs_ant):g}% "
                    f"menos que en los mismos días del anterior"
                )
            else:
                titulo_var = f"Gastaste {abs(variacion_gasto_vs_ant):g}% menos que el ciclo anterior"
            insights.append({
                "id": "variacion-gasto",
                "tipo": "positivo",
                "titulo": titulo_var,
                "descripcion": "Buen ritmo respecto al ciclo anterior.",
            })
        elif variacion_gasto_vs_ant >= 10:
            if es_ult_en_curso:
                titulo_var = (
                    f"En lo que va del ciclo gastaste {variacion_gasto_vs_ant:g}% "
                    f"más que en los mismos días del anterior"
                )
            else:
                titulo_var = f"Gastaste {variacion_gasto_vs_ant:g}% más que el ciclo anterior"
            insights.append({
                "id": "variacion-gasto",
                "tipo": "alerta",
                "titulo": titulo_var,
                "descripcion": "Revisá el desglose por categorías para ver en qué rubros aumentó el gasto.",
            })

    # 3. Tasa de ahorro en ciclos completos
    if ciclos_completos and not solo_gastos and tasa_ahorro_ventana is not None:
        if tasa_ahorro_ventana >= 20:
            insights.append({
                "id": "tasa-ahorro",
                "tipo": "positivo",
                "titulo": f"Te quedó el {tasa_ahorro_ventana:g}% de lo que entró",
                "descripcion": "Estás reteniendo más de una quinta parte de lo que entró.",
            })
        elif tasa_ahorro_ventana < 0:
            sum_g_c = sum(item["_gasto_dec"] for item in ciclos_completos)
            sum_i_c = sum(
                (item["_ingreso_dec"] for item in ciclos_completos if item["_ingreso_dec"] is not None),
                Decimal("0"),
            )
            dif_monto = abs(sum_i_c - sum_g_c)
            moneda_fmt = "ARS" if modo.endswith("ars") or modo == "solo_ars" else "USD"
            monto_str = formatear_monto(dif_monto, moneda_fmt)
            insights.append({
                "id": "tasa-ahorro",
                "tipo": "alerta",
                "titulo": "Gastaste más de lo que entró",
                "descripcion": f"En estos ciclos salieron {monto_str} más de lo que entró.",
            })

    return insights


def armar_frase_resumen(
    solo_gastos: bool,
    alcance: str,
    es_ult_en_curso: bool,
    dias_transcurridos: int,
    dias_totales: int,
    variacion_gasto_vs_anterior_pct: Optional[float],
) -> Optional[str]:
    """
    Arma la frase resumen determinística de Análisis según la decisión 4A.
    No toca la base de datos.
    """
    parte1: Optional[str] = None
    if solo_gastos:
        parte1 = "Con este filtro solo se ven gastos."
    elif alcance == "terminados" and es_ult_en_curso:
        parte1 = "Números de los ciclos terminados."
    elif alcance == "en_curso":
        parte1 = f"Números de lo que va del ciclo ({dias_transcurridos} de {dias_totales} días)."
    elif not es_ult_en_curso:
        parte1 = None

    parte2: Optional[str] = None
    if variacion_gasto_vs_anterior_pct is not None:
        p = int(
            Decimal(str(abs(variacion_gasto_vs_anterior_pct))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if es_ult_en_curso:
            if p == 0:
                parte2 = f"En estos {dias_transcurridos} días gastaste lo mismo que en los mismos días del ciclo anterior."
            elif variacion_gasto_vs_anterior_pct > 0:
                parte2 = f"En estos {dias_transcurridos} días gastaste {p}% más que en los mismos días del ciclo anterior."
            else:
                parte2 = f"En estos {dias_transcurridos} días gastaste {p}% menos que en los mismos días del ciclo anterior."
        else:
            if p == 0:
                parte2 = "En el último ciclo gastaste lo mismo que en el anterior."
            elif variacion_gasto_vs_anterior_pct > 0:
                parte2 = f"En el último ciclo gastaste {p}% más que en el anterior."
            else:
                parte2 = f"En el último ciclo gastaste {p}% menos que en el anterior."

    partes = [p_txt for p_txt in (parte1, parte2) if p_txt]
    if not partes:
        return None
    return " ".join(partes)

