# Evaluador de precisión y exhaustividad del clasificador de gastos con personas sintéticas
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.models.transaccion import TipoTransaccion
from app.utils.finanzas import clasificar_gastos
from tests.motor.personas import DEFINICION_PERSONAS, generar_todas_las_personas
from tests.motor.personas.catalogo import IPC_MAP
from tests.motor.personas.generador import FECHA_FIN_HISTORIA
from tests.motor.personas.modelos import Persona


class EvaluadorPersonas:
    """Evalúa el desempeño de clasificar_gastos comparando su salida con la verdad conocida."""

    def __init__(self, personas: list[Persona], fecha_destino: date = FECHA_FIN_HISTORIA):
        self.personas = personas
        self.fecha_destino = fecha_destino

    def evaluar_persona(self, persona: Persona) -> dict[str, Any]:
        """Ejecuta clasificar_gastos para una persona y calcula métricas contra la verdad."""
        txs = persona.movimientos
        ciclos = persona.ciclos

        resultado = clasificar_gastos(
            transacciones=txs,
            ciclos=ciclos,
            ipc_records=IPC_MAP,
            fecha_destino=self.fecha_destino,
        )

        compromiso_ids = {tx.id for tx in resultado.comprometidos}
        habito_ids = {tx.id for tx in resultado.habitos}
        variable_ids = {tx.id for tx in resultado.variables}

        # Mapeo de clase asignada por ID de transacción
        asignada_por_tx: dict[str, str] = {}
        for tx in txs:
            if tx.tipo == TipoTransaccion.INGRESO:
                asignada_por_tx[tx.id] = "NO_EVALUADO"
            elif tx.id in compromiso_ids:
                asignada_por_tx[tx.id] = "COMPROMISO"
            elif tx.id in habito_ids:
                asignada_por_tx[tx.id] = "HABITO"
            elif tx.id in variable_ids:
                asignada_por_tx[tx.id] = "VARIABLE"
            else:
                asignada_por_tx[tx.id] = "VARIABLE"

        # 1. Evaluación por Grupo Verdadero
        movs_por_grupo: dict[str, list[Any]] = defaultdict(list)
        for tx in txs:
            movs_por_grupo[tx.grupo_verdadero].append(tx)

        # Mapear streams detectados a grupos verdaderos por intersección de IDs
        streams_por_grupo: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in resultado.streams:
            stream_tx_ids = set(s.transacciones_ids)
            for g_nom, g_txs in movs_por_grupo.items():
                g_tx_ids = {t.id for t in g_txs}
                overlap = stream_tx_ids & g_tx_ids
                if overlap:
                    streams_por_grupo[g_nom].append({
                        "descripcion": s.descripcion,
                        "frecuencia": s.frecuencia,
                        "estado": s.estado,
                        "clase": s.clase,
                        "ocurrencias": s.cantidad_ocurrencias,
                        "monto_mediano_deflactado": float(s.monto_mediano_deflactado),
                        "overlap_txs": len(overlap),
                    })

        reporte_grupos: list[dict[str, Any]] = []
        for g_nom, g_info in persona.grupos_verdad.items():
            g_txs = movs_por_grupo.get(g_nom, [])
            total_g_txs = len(g_txs)

            conteo_clases_g: dict[str, int] = defaultdict(int)
            for t in g_txs:
                conteo_clases_g[asignada_por_tx[t.id]] += 1

            clase_predominante = max(conteo_clases_g.items(), key=lambda x: x[1])[0] if conteo_clases_g else "NINGUNA"

            streams_asociados = streams_por_grupo.get(g_nom, [])
            detectado_como_stream = len(streams_asociados) > 0

            # Determinar si el grupo fue clasificado correctamente
            acierto_grupo = (clase_predominante == g_info.clase_esperada)

            reporte_grupos.append({
                "grupo": g_nom,
                "tipo_verdadero": g_info.tipo_verdadero,
                "clase_esperada": g_info.clase_esperada,
                "clase_asignada_predominante": clase_predominante,
                "distribucion_clases": dict(conteo_clases_g),
                "total_transacciones": total_g_txs,
                "detectado_como_stream": detectado_como_stream,
                "streams_asociados": streams_asociados,
                "acierto": acierto_grupo,
            })

        # 2. Evaluación por Tipo de Verdad (conteo de transacciones)
        conteo_por_tipo_verdad: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for tx in txs:
            c_asig = asignada_por_tx[tx.id]
            conteo_por_tipo_verdad[tx.tipo_verdadero][c_asig] += 1

        # 3. Métricas de Precisión y Exhaustividad (a nivel de transacción de egreso)
        txs_egreso = [tx for tx in txs if tx.tipo == TipoTransaccion.EGRESO]

        # Métricas COMPROMISO
        # Positivo real: tipo_verdadero == "gasto_fijo"
        tp_comp = sum(1 for tx in txs_egreso if tx.tipo_verdadero == "gasto_fijo" and asignada_por_tx[tx.id] == "COMPROMISO")
        fp_comp = sum(1 for tx in txs_egreso if tx.tipo_verdadero != "gasto_fijo" and asignada_por_tx[tx.id] == "COMPROMISO")
        fn_comp = sum(1 for tx in txs_egreso if tx.tipo_verdadero == "gasto_fijo" and asignada_por_tx[tx.id] != "COMPROMISO")

        prec_comp = (tp_comp / (tp_comp + fp_comp)) if (tp_comp + fp_comp) > 0 else 0.0
        rec_comp = (tp_comp / (tp_comp + fn_comp)) if (tp_comp + fn_comp) > 0 else 0.0
        f1_comp = (2 * prec_comp * rec_comp / (prec_comp + rec_comp)) if (prec_comp + rec_comp) > 0 else 0.0

        # Métricas HABITO
        # Positivo real: tipo_verdadero == "costumbre"
        tp_hab = sum(1 for tx in txs_egreso if tx.tipo_verdadero == "costumbre" and asignada_por_tx[tx.id] == "HABITO")
        fp_hab = sum(1 for tx in txs_egreso if tx.tipo_verdadero != "costumbre" and asignada_por_tx[tx.id] == "HABITO")
        fn_hab = sum(1 for tx in txs_egreso if tx.tipo_verdadero == "costumbre" and asignada_por_tx[tx.id] != "HABITO")

        prec_hab = (tp_hab / (tp_hab + fp_hab)) if (tp_hab + fp_hab) > 0 else 0.0
        rec_hab = (tp_hab / (tp_hab + fn_hab)) if (tp_hab + fn_hab) > 0 else 0.0
        f1_hab = (2 * prec_hab * rec_hab / (prec_hab + rec_hab)) if (prec_hab + rec_hab) > 0 else 0.0

        # 4. Métricas a nivel de Grupo
        grupos_comp = [g for g in reporte_grupos if g["clase_esperada"] == "COMPROMISO"]
        grupos_hab = [g for g in reporte_grupos if g["clase_esperada"] == "HABITO"]
        grupos_var = [g for g in reporte_grupos if g["clase_esperada"] == "VARIABLE"]

        aciertos_grupos_comp = sum(1 for g in grupos_comp if g["clase_asignada_predominante"] == "COMPROMISO")
        aciertos_grupos_hab = sum(1 for g in grupos_hab if g["clase_asignada_predominante"] == "HABITO")
        aciertos_grupos_var = sum(1 for g in grupos_var if g["clase_asignada_predominante"] == "VARIABLE")

        return {
            "persona_id": persona.id,
            "nombre": persona.nombre,
            "tipo_ingreso": persona.tipo_ingreso,
            "cobertura_real": persona.cobertura_real,
            "total_transacciones": len(txs),
            "total_egresos": len(txs_egreso),
            "total_streams_detectados": len(resultado.streams),
            "conteo_por_tipo_verdad": {k: dict(v) for k, v in conteo_por_tipo_verdad.items()},
            "metricas_transacciones": {
                "compromiso": {
                    "tp": tp_comp,
                    "fp": fp_comp,
                    "fn": fn_comp,
                    "precision": round(prec_comp, 4),
                    "exhaustividad": round(rec_comp, 4),
                    "f1": round(f1_comp, 4),
                },
                "habito": {
                    "tp": tp_hab,
                    "fp": fp_hab,
                    "fn": fn_hab,
                    "precision": round(prec_hab, 4),
                    "exhaustividad": round(rec_hab, 4),
                    "f1": round(f1_hab, 4),
                },
            },
            "metricas_grupos": {
                "compromiso": {
                    "total": len(grupos_comp),
                    "aciertos": aciertos_grupos_comp,
                    "exhaustividad_grupo": round(aciertos_grupos_comp / len(grupos_comp), 4) if grupos_comp else 0.0,
                },
                "habito": {
                    "total": len(grupos_hab),
                    "aciertos": aciertos_grupos_hab,
                    "exhaustividad_grupo": round(aciertos_grupos_hab / len(grupos_hab), 4) if grupos_hab else 0.0,
                },
                "variable": {
                    "total": len(grupos_var),
                    "aciertos": aciertos_grupos_var,
                    "exhaustividad_grupo": round(aciertos_grupos_var / len(grupos_var), 4) if grupos_var else 0.0,
                },
            },
            "reporte_grupos": reporte_grupos,
        }

    def evaluar_todas(self) -> dict[str, Any]:
        """Evalúa las 10 personas sintéticas y genera métricas globales."""
        resultados_personas = [self.evaluar_persona(p) for p in self.personas]

        # Consolidación Global
        total_txs = sum(r["total_transacciones"] for r in resultados_personas)
        total_egresos = sum(r["total_egresos"] for r in resultados_personas)
        total_streams = sum(r["total_streams_detectados"] for r in resultados_personas)

        # Suma de TP, FP, FN a nivel global
        tp_comp_g = sum(r["metricas_transacciones"]["compromiso"]["tp"] for r in resultados_personas)
        fp_comp_g = sum(r["metricas_transacciones"]["compromiso"]["fp"] for r in resultados_personas)
        fn_comp_g = sum(r["metricas_transacciones"]["compromiso"]["fn"] for r in resultados_personas)

        prec_comp_g = (tp_comp_g / (tp_comp_g + fp_comp_g)) if (tp_comp_g + fp_comp_g) > 0 else 0.0
        rec_comp_g = (tp_comp_g / (tp_comp_g + fn_comp_g)) if (tp_comp_g + fn_comp_g) > 0 else 0.0
        f1_comp_g = (2 * prec_comp_g * rec_comp_g / (prec_comp_g + rec_comp_g)) if (prec_comp_g + rec_comp_g) > 0 else 0.0

        tp_hab_g = sum(r["metricas_transacciones"]["habito"]["tp"] for r in resultados_personas)
        fp_hab_g = sum(r["metricas_transacciones"]["habito"]["fp"] for r in resultados_personas)
        fn_hab_g = sum(r["metricas_transacciones"]["habito"]["fn"] for r in resultados_personas)

        prec_hab_g = (tp_hab_g / (tp_hab_g + fp_hab_g)) if (tp_hab_g + fp_hab_g) > 0 else 0.0
        rec_hab_g = (tp_hab_g / (tp_hab_g + fn_hab_g)) if (tp_hab_g + fn_hab_g) > 0 else 0.0
        f1_hab_g = (2 * prec_hab_g * rec_hab_g / (prec_hab_g + rec_hab_g)) if (prec_hab_g + rec_hab_g) > 0 else 0.0

        # Consolidación a nivel de Grupos
        tot_g_comp = sum(r["metricas_grupos"]["compromiso"]["total"] for r in resultados_personas)
        aciertos_g_comp = sum(r["metricas_grupos"]["compromiso"]["aciertos"] for r in resultados_personas)
        tot_g_hab = sum(r["metricas_grupos"]["habito"]["total"] for r in resultados_personas)
        aciertos_g_hab = sum(r["metricas_grupos"]["habito"]["aciertos"] for r in resultados_personas)
        tot_g_var = sum(r["metricas_grupos"]["variable"]["total"] for r in resultados_personas)
        aciertos_g_var = sum(r["metricas_grupos"]["variable"]["aciertos"] for r in resultados_personas)

        # Matriz de confusión global
        matriz_confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for r in resultados_personas:
            for t_verd, dist in r["conteo_por_tipo_verdad"].items():
                for c_asig, cant in dist.items():
                    matriz_confusion[t_verd][c_asig] += cant

        return {
            "resumen_global": {
                "total_personas": len(self.personas),
                "total_transacciones": total_txs,
                "total_egresos": total_egresos,
                "total_streams_detectados": total_streams,
                "compromiso": {
                    "tp": tp_comp_g,
                    "fp": fp_comp_g,
                    "fn": fn_comp_g,
                    "precision": round(prec_comp_g, 4),
                    "exhaustividad": round(rec_comp_g, 4),
                    "f1": round(f1_comp_g, 4),
                    "grupos_totales": tot_g_comp,
                    "grupos_acertados": aciertos_g_comp,
                    "exhaustividad_grupos": round(aciertos_g_comp / tot_g_comp, 4) if tot_g_comp else 0.0,
                },
                "habito": {
                    "tp": tp_hab_g,
                    "fp": fp_hab_g,
                    "fn": fn_hab_g,
                    "precision": round(prec_hab_g, 4),
                    "exhaustividad": round(rec_hab_g, 4),
                    "f1": round(f1_hab_g, 4),
                    "grupos_totales": tot_g_hab,
                    "grupos_acertados": aciertos_g_hab,
                    "exhaustividad_grupos": round(aciertos_g_hab / tot_g_hab, 4) if tot_g_hab else 0.0,
                },
                "variable": {
                    "grupos_totales": tot_g_var,
                    "grupos_acertados": aciertos_g_var,
                    "especificidad_grupos": round(aciertos_g_var / tot_g_var, 4) if tot_g_var else 0.0,
                },
                "matriz_confusion": {k: dict(v) for k, v in matriz_confusion.items()},
            },
            "personas": resultados_personas,
        }


def formatear_reporte_legible(evaluacion: dict[str, Any], etiqueta: str) -> str:
    """Genera un reporte en texto plano detallado y legible."""
    g = evaluacion["resumen_global"]
    lineas = []
    lineas.append("=" * 90)
    lineas.append(f"EVALUACION DE CLASIFICADOR DE GASTOS CON PERSONAS SINTETICAS ({etiqueta.upper()})")
    lineas.append("=" * 90)
    lineas.append(f"Total personas evaluadas: {g['total_personas']}")
    lineas.append(f"Total transacciones analizadas: {g['total_transacciones']} ({g['total_egresos']} gastos de consumo)")
    lineas.append(f"Total streams recurrentes detectados por el motor: {g['total_streams_detectados']}")
    lineas.append("")
    lineas.append("-" * 90)
    lineas.append("RESUMEN GLOBAL DE RENDIMIENTO DEL MOTOR ACTUAL")
    lineas.append("-" * 90)
    comp = g["compromiso"]
    lineas.append(f"COMPROMISOS (Verdad: gasto_fijo):")
    lineas.append(f"  * A nivel Transacción: Precisión = {comp['precision']:.2%} | Exhaustividad = {comp['exhaustividad']:.2%} | F1 = {comp['f1']:.2%}")
    lineas.append(f"    (TP = {comp['tp']}, FP = {comp['fp']}, FN = {comp['fn']})")
    lineas.append(f"  * A nivel Grupo: {comp['grupos_acertados']}/{comp['grupos_totales']} grupos detectados como COMPROMISO ({comp['exhaustividad_grupos']:.2%})")
    lineas.append("")
    hab = g["habito"]
    lineas.append(f"HÁBITOS (Verdad: costumbre):")
    lineas.append(f"  * A nivel Transacción: Precisión = {hab['precision']:.2%} | Exhaustividad = {hab['exhaustividad']:.2%} | F1 = {hab['f1']:.2%}")
    lineas.append(f"    (TP = {hab['tp']}, FP = {hab['fp']}, FN = {hab['fn']})")
    lineas.append(f"  * A nivel Grupo: {hab['grupos_acertados']}/{hab['grupos_totales']} grupos detectados como HABITO ({hab['exhaustividad_grupos']:.2%})")
    lineas.append("")
    var = g["variable"]
    lineas.append(f"VARIABLES (Verdad: gasto_diario / eventual):")
    lineas.append(f"  * A nivel Grupo: {var['grupos_acertados']}/{var['grupos_totales']} grupos mantenidos como VARIABLE sin falsos streams ({var['especificidad_grupos']:.2%})")
    lineas.append("")
    lineas.append("MATRIZ DE CONFUSIÓN GLOBAL (Verdad vs Asignado por el motor):")
    lineas.append(f"{'Tipo de Verdad':<24} | {'COMPROMISO':>12} | {'HABITO':>12} | {'VARIABLE':>12} | {'NO_EVALUADO':>12}")
    lineas.append("-" * 80)
    for t_verd, fila in g["matriz_confusion"].items():
        c_comp = fila.get("COMPROMISO", 0)
        c_hab = fila.get("HABITO", 0)
        c_var = fila.get("VARIABLE", 0)
        c_noeval = fila.get("NO_EVALUADO", 0)
        lineas.append(f"{t_verd:<24} | {c_comp:>12} | {c_hab:>12} | {c_var:>12} | {c_noeval:>12}")

    lineas.append("")
    lineas.append("=" * 90)
    lineas.append("DETALLE INDIVIDUAL POR PERSONA SINTETICA")
    lineas.append("=" * 90)

    for p in evaluacion["personas"]:
        lineas.append(f"\n[{p['persona_id']}] {p['nombre']} (Ingreso: {p['tipo_ingreso']}, Cobertura: {p['cobertura_real']})")
        lineas.append(f"Total txs: {p['total_transacciones']} ({p['total_egresos']} egresos) | Streams detectados: {p['total_streams_detectados']}")
        m_tx = p["metricas_transacciones"]
        lineas.append(f"Métricas Compromiso: Precisión = {m_tx['compromiso']['precision']:.2%}, Exhaustividad = {m_tx['compromiso']['exhaustividad']:.2%}, F1 = {m_tx['compromiso']['f1']:.2%}")
        lineas.append(f"Métricas Hábito:      Precisión = {m_tx['habito']['precision']:.2%}, Exhaustividad = {m_tx['habito']['exhaustividad']:.2%}, F1 = {m_tx['habito']['f1']:.2%}")
        lineas.append("Evaluación por grupos:")
        for grp in p["reporte_grupos"]:
            estado_icono = "[ACIERTO]" if grp["acierto"] else "[FALLO]"
            stream_info = f"Stream detectado ({len(grp['streams_asociados'])})" if grp["detectado_como_stream"] else "NO detectado como stream"
            lineas.append(
                f"  {estado_icono} {grp['grupo']:<22} | Verdad: {grp['tipo_verdadero']:<14} | Esperado: {grp['clase_esperada']:<10} | Asignado: {grp['clase_asignada_predominante']:<10} | {stream_info}"
            )
            if grp["streams_asociados"]:
                for s in grp["streams_asociados"]:
                    lineas.append(f"     -> Stream: '{s['descripcion']}' ({s['frecuencia']}, {s['estado']}, {s['ocurrencias']} occ, ${s['monto_mediano_deflactado']:.2f}) => Clase: {s['clase']}")

    return "\n".join(lineas)


def main():
    parser = argparse.ArgumentParser(description="Evaluar clasificador de gastos con personas sintéticas.")
    parser.add_argument("--etiqueta", default="antes_fase2", help="Etiqueta para identificar la corrida (default: antes_fase2)")
    args = parser.parse_args()

    etiqueta = args.etiqueta
    print(f"Iniciando evaluación de personas sintéticas (etiqueta: {etiqueta})...")

    personas = generar_todas_las_personas()
    evaluador = EvaluadorPersonas(personas=personas)
    resultado = evaluador.evaluar_todas()

    reporte_txt = formatear_reporte_legible(resultado, etiqueta=etiqueta)

    # Rutas de salida en auditorias/ del workspace raíz
    dir_workspace = Path(__file__).resolve().parent.parent.parent.parent
    dir_fotos = dir_workspace / "auditorias" / "fotos"
    dir_fotos.mkdir(parents=True, exist_ok=True)

    ruta_json = dir_fotos / f"personas_{etiqueta}.json"
    ruta_txt = dir_fotos / f"personas_{etiqueta}.txt"

    with open(ruta_json, "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    with open(ruta_txt, "w", encoding="utf-8") as f:
        f.write(reporte_txt)

    # También guardar reporte crudo en auditorias/raw/paso03/Q5.txt
    ruta_q5 = dir_workspace / "auditorias" / "raw" / "paso03" / "Q5.txt"
    ruta_q5.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta_q5, "w", encoding="utf-8") as f:
        f.write(reporte_txt)

    print(f"Guardado JSON en: {ruta_json}")
    print(f"Guardado TXT en:  {ruta_txt}")
    print("\n" + reporte_txt)


if __name__ == "__main__":
    main()
