# -*- coding: utf-8 -*-
"""
scripts/evaluacion/evaluar_bot.py

Evalúa el flujo completo de WhatsApp contra frases_bot.json usando IA real
en transacciones aisladas con rollback (solo testingadmin, sin envíos).
"""
from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import statistics
import sys
import time
from typing import Any
import unicodedata
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOPE_CORRIDA_USD = Decimal("2.00")
MENSAJE_TOPE_CORRIDA = "Se cortó: pasó el tope de USD 2 de esta corrida."
FUNCIONES_VALIDAS = (
    "gasto",
    "ingreso",
    "transferencia",
    "cuotas",
    "dolares",
    "permitirse",
    "conveniencia",
    "consulta",
    "correccion",
    "meta",
    "suscripcion",
    "otro",
)
FRASES_PATH = Path(__file__).resolve().parent / "frases_bot.json"
OUT_DIR = Path(r"D:\Argentum\auditorias\raw\evaluacion_m2")
UMBRALES_CALIBRACION = [round(0.50 + 0.05 * i, 2) for i in range(10)]
_MARCADORES_CONFIRMACION = (
    "¿Anoto",
    "¿Lo anoto",
    "¿Las anoto",
    "¿Aporto",
    "¿Lo dejo",
)
_CAMPOS_PLATA = ("tipo", "monto", "moneda", "cuenta")


def supera_tope_corrida(costo_acumulado: Decimal) -> bool:
    """Devuelve True si el costo acumulado superó el tope de USD 2,00."""
    return Decimal(str(costo_acumulado)) > TOPE_CORRIDA_USD


def _norm_texto(txt: Any) -> str:
    if txt is None:
        return ""
    s = "".join(
        c
        for c in unicodedata.normalize("NFD", str(txt))
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(s.lower().split())


def _limpiar_error_api(exc: Exception) -> str:
    txt = str(exc)
    if "sk-" in txt:
        txt = "Error de API (clave oculta)"
    return txt[:300]


def _percentil_95(valores: list[float]) -> float:
    if not valores:
        return 0.0
    ord_v = sorted(valores)
    idx = min(len(ord_v) - 1, int(0.95 * (len(ord_v) - 1)))
    return ord_v[idx]


def comparar_resultado(esperado: dict[str, Any], obtenido: dict[str, Any]) -> dict[str, Any]:
    """Compara esperado vs obtenido campo a campo (función pura)."""
    fallos: list[str] = []
    campos_ok: dict[str, bool] = {}

    if "intent" in esperado:
        ok_int = _norm_texto(esperado["intent"]) == _norm_texto(obtenido.get("intent"))
        campos_ok["intent"] = ok_int
        if not ok_int:
            fallos.append("intent")

    if "movimientos" in esperado:
        esp_movs = esperado["movimientos"]
        obt_movs = obtenido.get("movimientos") or []
        if len(esp_movs) != len(obt_movs):
            fallos.append("cantidad")
        for em, om in zip(esp_movs, obt_movs):
            for campo, val_esp in em.items():
                val_obt = om.get(campo)
                ok = True
                if campo in ("dias", "cuotas"):
                    try:
                        ok = val_obt is not None and int(val_esp) == int(val_obt)
                    except (TypeError, ValueError):
                        ok = False
                elif campo == "monto":
                    try:
                        ok = val_obt is not None and (
                            Decimal(str(val_esp)) == Decimal(str(val_obt))
                        )
                    except Exception:
                        ok = False
                else:
                    ok = _norm_texto(val_esp) == _norm_texto(val_obt)

                campos_ok[campo] = campos_ok.get(campo, True) and ok
                if not ok and campo not in fallos:
                    fallos.append(campo)

    if "transferencias" in esperado:
        esp_trs = esperado["transferencias"]
        obt_trs = obtenido.get("transferencias") or []
        ok_tr = len(esp_trs) == len(obt_trs)
        if not ok_tr:
            fallos.append("cantidad")
        for et, ot in zip(esp_trs, obt_trs):
            for campo, val_esp in et.items():
                val_obt = ot.get(campo)
                if campo == "monto":
                    try:
                        igual = val_obt is not None and (
                            Decimal(str(val_esp)) == Decimal(str(val_obt))
                        )
                    except Exception:
                        igual = False
                else:
                    igual = _norm_texto(val_esp) == _norm_texto(val_obt)
                if not igual:
                    ok_tr = False
        campos_ok["transferencias"] = ok_tr
        if not ok_tr and "transferencias" not in fallos:
            fallos.append("transferencias")

    if "texto_contiene" in esperado:
        texto_bot = _norm_texto(" ".join(obtenido.get("mensajes_bot") or []))
        ok_txt = all(_norm_texto(t) in texto_bot for t in esperado["texto_contiene"])
        campos_ok["texto"] = ok_txt
        if not ok_txt:
            fallos.append("texto")

    return {
        "estado": "ACIERTO" if not fallos else "FALLA",
        "acierto": not fallos,
        "fallos": fallos,
        "campos_ok": campos_ok,
    }


def clasificar_primera_respuesta(mensajes_bot: list[str] | None) -> str:
    """Clasifica el primer mensaje del bot: anoto_directo, pide_confirmacion u otra."""
    if not mensajes_bot:
        return "otra"
    primer = str(mensajes_bot[0] or "").strip()
    if primer.startswith("Listo"):
        return "anoto_directo"
    if any(m in primer for m in _MARCADORES_CONFIRMACION):
        return "pide_confirmacion"
    return "otra"


def _es_resultado_simple_calibrable(res: dict[str, Any]) -> bool:
    """Devuelve True si el resultado es gasto o ingreso simple (1 mov, sin cuotas, ARS)."""
    if res.get("funcion") not in ("gasto", "ingreso"):
        return False
    esp = res.get("esperado") or {}
    movs = esp.get("movimientos") or []
    if len(movs) != 1:
        return False
    mov = movs[0]
    if mov.get("cuotas") is not None or esp.get("cuotas") is not None:
        return False
    moneda = mov.get("moneda")
    if moneda is not None and str(moneda).upper() != "ARS":
        return False
    return True


def _tiene_error_plata(res: dict[str, Any]) -> bool:
    """Devuelve True si hubo algún fallo en tipo, monto, moneda o cuenta."""
    comp = res.get("comparacion") or {}
    fallos = set(comp.get("fallos") or [])
    campos_ok = comp.get("campos_ok") or {}
    return any(
        (c in fallos) or (campos_ok.get(c) is False)
        for c in _CAMPOS_PLATA
    )


def tabla_calibracion(
    resultados: list[dict[str, Any]],
    umbrales: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Calcula la tabla de calibración de confianza IA sobre frases simples en ARS."""
    lista_umbrales = list(umbrales) if umbrales is not None else UMBRALES_CALIBRACION
    simples: list[tuple[float, bool]] = []
    for r in resultados:
        if not _es_resultado_simple_calibrable(r):
            continue
        confs_validas = [
            float(c) for c in (r.get("confianzas_ia") or []) if c is not None
        ]
        if not confs_validas:
            continue
        simples.append((min(confs_validas), _tiene_error_plata(r)))

    tabla: list[dict[str, Any]] = []
    for u in lista_umbrales:
        u_f = round(float(u), 2)
        preguntaria = sum(1 for conf, _ in simples if conf < u_f)
        atrapados = sum(1 for conf, err in simples if conf < u_f and err)
        sin_atrapar = sum(1 for conf, err in simples if conf >= u_f and err)
        tabla.append(
            {
                "umbral": u_f,
                "total_simples": len(simples),
                "preguntaria": preguntaria,
                "errores_plata_atrapados": atrapados,
                "errores_plata_sin_atrapar": sin_atrapar,
            }
        )
    return tabla


def _ejecutar_frase(frase_cfg: dict[str, Any]) -> dict[str, Any]:
    from sqlalchemy import select
    from app.core.reglas_texto_wpp import problemas_texto_wpp
    from app.models.billetera import Billetera
    from app.models.categoria import Categoria
    from app.models.conversacion_wpp import ConversacionWpp
    from app.models.cuota import Cuota
    from app.models.subcategoria import Subcategoria
    from app.models.tarjeta_credito import TarjetaCredito
    from app.models.transaccion import Transaccion
    from app.models.transferencia_interna import TransferenciaInterna
    from app.utils.fecha import hoy_argentina
    from scripts.regresion.suite import comun

    comun._gestor_actual = None
    mensajes_entrada = list(frase_cfg["mensajes"])
    if frase_cfg.get("confirmar"):
        mensajes_entrada.append("sí")

    resultado_box: dict[str, Any] = {}

    def _run(conn, BoundSession, respuestas):
        colector = comun.ColectorSalidas("")
        colector.iniciar_escenario(frase_cfg["id"])
        comun._colector_salidas = colector

        tx_antes = set(conn.execute(select(Transaccion.id)).scalars().all())
        tr_antes = set(conn.execute(select(TransferenciaInterna.id)).scalars().all())
        conv_antes = set(conn.execute(select(ConversacionWpp.id)).scalars().all())

        try:
            for msg in mensajes_entrada:
                payload = comun.make_payload(comun.TELEFONO_TEST, msg)
                comun._procesar_webhook_whatsapp_sync(payload, time.perf_counter())
        finally:
            comun._colector_salidas = None

        sess = BoundSession()
        try:
            convs = sess.execute(
                select(ConversacionWpp)
                .where(ConversacionWpp.id.not_in(conv_antes) if conv_antes else True)
                .order_by(ConversacionWpp.fecha.asc())
            ).scalars().all()
            intent_detectado = convs[0].intent_detectado if convs else None

            txs = sess.execute(
                select(Transaccion)
                .where(Transaccion.id.not_in(tx_antes) if tx_antes else True)
                .order_by(Transaccion.fecha_creacion.asc())
            ).scalars().all()
            cat_map = dict(sess.execute(select(Categoria.id, Categoria.nombre)).all())
            sub_map = dict(
                sess.execute(select(Subcategoria.id, Subcategoria.nombre)).all()
            )
            bill_map = dict(sess.execute(select(Billetera.id, Billetera.nombre)).all())
            tarj_map = dict(
                sess.execute(select(TarjetaCredito.id, TarjetaCredito.nombre)).all()
            )

            hoy = hoy_argentina()
            movs = []
            for tx in txs:
                if tx.es_cuota_hija:
                    continue
                cuenta = (
                    tarj_map.get(tx.tarjeta_id)
                    if tx.tarjeta_id
                    else bill_map.get(tx.billetera_id)
                )
                cuotas_cnt = None
                if tx.es_padre_cuotas and tx.grupo_cuotas_id:
                    hijas = sess.execute(
                        select(Cuota.id).where(Cuota.grupo_id == tx.grupo_cuotas_id)
                    ).scalars().all()
                    cuotas_cnt = len(hijas)
                movs.append(
                    {
                        "tipo": tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo),
                        "monto": float(tx.monto),
                        "moneda": (
                            tx.moneda.value
                            if hasattr(tx.moneda, "value")
                            else str(tx.moneda)
                        ),
                        "cuenta": cuenta,
                        "categoria": cat_map.get(tx.categoria_id),
                        "subcategoria": sub_map.get(tx.subcategoria_id),
                        "dias": (tx.fecha - hoy).days,
                        "cuotas": cuotas_cnt,
                    }
                )

            trs = sess.execute(
                select(TransferenciaInterna).where(
                    TransferenciaInterna.id.not_in(tr_antes) if tr_antes else True
                )
            ).scalars().all()
            transferencias = [
                {
                    "origen": bill_map.get(tr.billetera_origen_id),
                    "destino": bill_map.get(tr.billetera_destino_id),
                    "monto": float(tr.monto),
                    "moneda": (
                        tr.moneda.value
                        if hasattr(tr.moneda, "value")
                        else str(tr.moneda)
                    ),
                }
                for tr in trs
            ]
        finally:
            sess.close()

        problemas_ia = []
        for texto_msg, lleva_ia in colector.salidas_detalle.get(frase_cfg["id"], []):
            if lleva_ia:
                probs = problemas_texto_wpp(texto_msg)
                if probs:
                    problemas_ia.append({"texto": texto_msg, "problemas": probs})

        resultado_box["obtenido"] = {
            "intent": intent_detectado,
            "movimientos": movs,
            "transferencias": transferencias,
            "mensajes_bot": [m for _, m in respuestas],
            "problemas_texto_ia": problemas_ia,
        }

    comun.run_isolated(_run)
    return resultado_box["obtenido"]


def _guardar_salidas(
    modelo: str,
    razonamiento: str,
    resultados: list[dict[str, Any]],
    costo_total: Decimal,
    cortado: bool,
    tiempos_ia: list[float],
    ahora: datetime,
) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    modelo_safe = modelo.replace("/", "_").replace(":", "_")
    base = f"evaluar_bot_{modelo_safe}_{razonamiento}_{ahora:%Y%m%d}_{ahora:%H%M}"
    txt_path = OUT_DIR / f"{base}.txt"
    json_path = OUT_DIR / f"{base}.json"

    total = len(resultados)
    aciertos = sum(1 for r in resultados if r["comparacion"]["acierto"])

    por_funcion: dict[str, list[bool]] = {}
    por_campo: dict[str, list[bool]] = {}
    plata_ok = 0
    plata_tot = 0
    con_ia = [r for r in resultados if r["llamadas_ia"] > 0]
    ia_aciertos = sum(1 for r in con_ia if r["comparacion"]["acierto"])
    probs_ia_tot = sum(len(r["obtenido"].get("problemas_texto_ia", [])) for r in resultados)

    lineas: list[str] = [
        f"EVALUAR BOT | modelo={modelo} | razonamiento={razonamiento}",
        "",
    ]
    if cortado:
        lineas.append(MENSAJE_TOPE_CORRIDA)
        lineas.append("")

    for r in resultados:
        comp = r["comparacion"]
        fallos_txt = f" | fallos={comp['fallos']}" if comp["fallos"] else ""
        lineas.append(
            f"{r['id']} [{r['funcion']}] {comp['estado']}{fallos_txt} | "
            f"ia={r['llamadas_ia']} | costo={r['costo_usd']:.6f}"
        )
        por_funcion.setdefault(r["funcion"], []).append(comp["acierto"])
        for campo, ok in comp["campos_ok"].items():
            por_campo.setdefault(campo, []).append(ok)
        esp_movs = r["esperado"].get("movimientos") or []
        if esp_movs:
            plata_tot += 1
            campos_plata = ("tipo", "monto", "moneda", "cuenta")
            if all(comp["campos_ok"].get(c, True) for c in campos_plata) and (
                "cantidad" not in comp["fallos"]
            ):
                plata_ok += 1

    mediana_t = statistics.median(tiempos_ia) if tiempos_ia else 0.0
    p95_t = _percentil_95(tiempos_ia)

    prim_tot = {"anoto_directo": 0, "pide_confirmacion": 0, "otra": 0}
    prim_fn: dict[str, dict[str, int]] = {}
    for r in resultados:
        clase = r.get("primera_respuesta") or clasificar_primera_respuesta(
            (r.get("obtenido") or {}).get("mensajes_bot")
        )
        if clase not in prim_tot:
            clase = "otra"
        prim_tot[clase] += 1
        bucket = prim_fn.setdefault(
            r["funcion"],
            {"anoto_directo": 0, "pide_confirmacion": 0, "otra": 0},
        )
        bucket[clase] += 1
    calibracion = tabla_calibracion(resultados)

    lineas.extend(
        [
            "",
            f"Aciertos totales: {aciertos}/{total}",
            "Por función:",
        ]
    )
    for fn, vals in sorted(por_funcion.items()):
        lineas.append(f"  - {fn}: {sum(vals)}/{len(vals)}")
    lineas.append("Por campo:")
    for c, vals in sorted(por_campo.items()):
        lineas.append(f"  - {c}: {sum(vals)}/{len(vals)}")
    lineas.extend(
        [
            f"Plata (tipo, monto, moneda y cuenta): {plata_ok}/{plata_tot}",
            f"Frases que usaron IA: {len(con_ia)} | aciertos={ia_aciertos}/{len(con_ia)}",
            f"Textos IA con problemas de reglas: {probs_ia_tot}",
            f"Llamadas totales a IA: {len(tiempos_ia)}",
            f"Costo total (USD): {costo_total:.6f}",
            f"Tiempo por llamada IA: mediana={mediana_t:.3f}s | p95={p95_t:.3f}s",
            f"Modelo: {modelo} | Razonamiento: {razonamiento}",
            (
                "Primera respuesta: "
                f"anoto_directo={prim_tot['anoto_directo']} "
                f"pide_confirmacion={prim_tot['pide_confirmacion']} "
                f"otra={prim_tot['otra']}"
            ),
            "Primera respuesta por función:",
        ]
    )
    for fn, vals in sorted(prim_fn.items()):
        lineas.append(
            f"  - {fn}: anoto_directo={vals['anoto_directo']} "
            f"pide_confirmacion={vals['pide_confirmacion']} "
            f"otra={vals['otra']}"
        )
    lineas.append("Tabla de calibración:")
    for row in calibracion:
        lineas.append(
            f"  - umbral={row['umbral']:.2f}: "
            f"preguntaria={row['preguntaria']}/{row['total_simples']} "
            f"errores_plata_atrapados={row['errores_plata_atrapados']} "
            f"errores_plata_sin_atrapar={row['errores_plata_sin_atrapar']}"
        )

    txt_path.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    payload_json = {
        "modelo": modelo,
        "razonamiento": razonamiento,
        "cortado": cortado,
        "costo_total_usd": float(costo_total),
        "llamadas_ia": len(tiempos_ia),
        "tiempo_mediana_s": mediana_t,
        "tiempo_p95_s": p95_t,
        "calibracion": calibracion,
        "resultados": resultados,
    }
    json_path.write_text(
        json.dumps(payload_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return txt_path, json_path


def _crear_proc_medido(
    orig_proc: Any,
    frase_estado: dict[str, Any],
    tiempos_globales: list[float],
):
    """Construye el wrapper _proc_medido que mide tiempos y registra confianza e intent IA."""

    def _proc_medido(mensaje, usuario, db, historial=None, estado_previo=None):
        t0 = time.perf_counter()
        try:
            res = orig_proc(
                mensaje,
                usuario,
                db,
                historial=historial,
                estado_previo=estado_previo,
            )
            if isinstance(res, dict):
                if res.get("error"):
                    frase_estado["errores"].append("procesar_mensaje devolvió error")
                raw_conf = res.get("confianza")
                try:
                    conf_val: float | None = (
                        float(raw_conf) if raw_conf is not None else None
                    )
                except (TypeError, ValueError):
                    conf_val = None
                frase_estado["confianzas"].append(conf_val)
                raw_int = res.get("intent")
                frase_estado["intents_ia"].append(
                    str(raw_int) if raw_int is not None else None
                )
            return res
        except Exception as exc:
            frase_estado["errores"].append(_limpiar_error_api(exc))
            raise
        finally:
            dt = time.perf_counter() - t0
            frase_estado["llamadas"] += 1
            frase_estado["tiempos"].append(dt)
            tiempos_globales.append(dt)

    return _proc_medido


def main(argv: list[str] | None = None) -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    from app.core.config import settings
    from app.services import ai_service
    from app.services.uso_ia_service import calcular_costo_ia, _extraer_tokens_de_respuesta
    from app.utils.fecha import TZ_ARGENTINA
    from scripts.regresion.suite import comun

    parser = argparse.ArgumentParser(description="Evalúa el bot de WhatsApp con IA real.")
    parser.add_argument("--modelo", default=settings.OPENAI_MODEL)
    parser.add_argument(
        "--razonamiento",
        default=settings.OPENAI_RAZONAMIENTO,
        choices=["none", "low", "medium"],
    )
    parser.add_argument("--solo", default="", help="IDs separados por coma")
    args = parser.parse_args(argv)

    data = json.loads(FRASES_PATH.read_text(encoding="utf-8"))
    frases = data["frases"]
    if args.solo:
        filtro = {s.strip() for s in args.solo.split(",") if s.strip()}
        frases = [f for f in frases if f["id"] in filtro]

    est_llamadas = sum(len(f["mensajes"]) + (1 if f.get("confirmar") else 0) for f in frases)
    est_costo = calcular_costo_ia(args.modelo, 7000 * est_llamadas, 0, 200 * est_llamadas)
    print(
        f"Llamadas estimadas: hasta {est_llamadas} | "
        f"Costo máximo estimado: USD {est_costo:.4f}"
    )
    if input("Escribí SI para seguir: ").strip() != "SI":
        print("Cancelado.")
        return 0

    settings.OPENAI_MODEL = args.modelo
    settings.OPENAI_RAZONAMIENTO = args.razonamiento

    costo_acumulado = Decimal("0")
    tiempos_globales: list[float] = []
    resultados: list[dict[str, Any]] = []
    cortado = False

    import app.services.uso_ia_service as uso_mod

    orig_registrar = uso_mod.registrar_uso_ia
    frase_estado = {
        "costo": Decimal("0"),
        "llamadas": 0,
        "tiempos": [],
        "errores": [],
        "confianzas": [],
        "intents_ia": [],
    }

    def _colector_uso(usuario_id, funcion, modelo, respuesta=None, segundos_audio=0):
        t_in, t_cache, t_out = _extraer_tokens_de_respuesta(respuesta)
        c = calcular_costo_ia(
            modelo=modelo,
            tokens_entrada=t_in,
            tokens_entrada_cache=t_cache,
            tokens_salida=t_out,
            segundos_audio=segundos_audio,
        )
        frase_estado["costo"] += c

    orig_proc = comun._original_procesar_mensaje
    _proc_medido = _crear_proc_medido(
        orig_proc,
        frase_estado,
        tiempos_globales,
    )

    with (
        patch("app.services.uso_ia_service.registrar_uso_ia", side_effect=_colector_uso),
        patch.object(comun, "_original_procesar_mensaje", side_effect=_proc_medido),
        patch.object(ai_service, "procesar_mensaje", side_effect=_proc_medido),
    ):
        try:
            for frase_cfg in frases:
                if supera_tope_corrida(costo_acumulado):
                    print(MENSAJE_TOPE_CORRIDA)
                    cortado = True
                    break
                frase_estado["costo"] = Decimal("0")
                frase_estado["llamadas"] = 0
                frase_estado["tiempos"] = []
                frase_estado["errores"] = []
                frase_estado["confianzas"] = []
                frase_estado["intents_ia"] = []

                obtenido = _ejecutar_frase(frase_cfg)
                costo_acumulado += frase_estado["costo"]
                comp = comparar_resultado(frase_cfg["esperado"], obtenido)
                prim_resp = clasificar_primera_respuesta(
                    obtenido.get("mensajes_bot")
                )
                resultados.append(
                    {
                        "id": frase_cfg["id"],
                        "funcion": frase_cfg["funcion"],
                        "esperado": frase_cfg["esperado"],
                        "obtenido": obtenido,
                        "comparacion": comp,
                        "primera_respuesta": prim_resp,
                        "confianzas_ia": list(frase_estado["confianzas"]),
                        "intents_ia": list(frase_estado["intents_ia"]),
                        "llamadas_ia": frase_estado["llamadas"],
                        "tiempos_s": list(frase_estado["tiempos"]),
                        "errores": list(frase_estado["errores"]),
                        "costo_usd": float(frase_estado["costo"]),
                    }
                )
                if supera_tope_corrida(costo_acumulado):
                    print(MENSAJE_TOPE_CORRIDA)
                    cortado = True
                    break
        finally:
            uso_mod.registrar_uso_ia = orig_registrar

    ahora = datetime.now(TZ_ARGENTINA)
    txt_p, json_p = _guardar_salidas(
        args.modelo,
        args.razonamiento,
        resultados,
        costo_acumulado,
        cortado,
        tiempos_globales,
        ahora,
    )
    print(f"Escrito: {txt_p}")
    print(f"Escrito: {json_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
