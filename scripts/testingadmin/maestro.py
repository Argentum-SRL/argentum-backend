"""
Script Maestro de Regeneración de testingadmin@argentum.com.

Orquestador principal que ejecuta todas las etapas de generación determinística
con semilla fija, registra la verdad conocida en verdad_testingadmin.json
e imprime todas las métricas de ejecución requeridas.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

sys.path.insert(0, ".")
os.environ["LOG_LEVEL"] = "CRITICAL"

from sqlalchemy import func, text
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.billetera import Billetera
from app.models.categoria import Categoria
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.utils.fecha import hoy_argentina
from scripts.testingadmin.datos_base import CatalogoEntidades
from scripts.testingadmin.generador_fijos import generar_fijos
from scripts.testingadmin.generador_ingresos import generar_ingresos
from scripts.testingadmin.generador_metas_inversiones import generar_metas_e_inversiones
from scripts.testingadmin.generador_tarjetas_suscripciones import generar_tarjetas_y_suscripciones
from scripts.testingadmin.generador_variables import generar_variables
from scripts.testingadmin.seguridad import (
    ejecutar_borrado_testingadmin,
    instalar_arnes_interceptores,
    obtener_interceptaciones,
    snapshot_cuentas_ajenas,
    verificar_canario_y_usuario,
)

SEMILLA_FIJA = 2026
VERDAD_CONOCIDA_PATH = os.path.join(
    os.path.dirname(__file__), "verdad_testingadmin.json"
)
HUELLA_PATH = os.path.join(
    os.path.dirname(__file__), "huella_testingadmin.json"
)


def generar_huella_testingadmin(db: Session, usuario_id: Any) -> Dict[str, Any]:
    """
    Genera la huella canónica del contenido generado para testingadmin@argentum.com.
    Cubre: movimientos, transferencias, cuotas, grupos de cuotas, rendimientos y movimientos de meta.
    Sin IDs ni fechas de creación/modificación técnicas, ordenada canónicamente.
    Calcula el SHA256 del contenido canónico y guarda scripts/testingadmin/huella_testingadmin.json.
    """
    # 1. Movimientos (Transacciones)
    txs = (
        db.query(Transaccion)
        .filter(Transaccion.usuario_id == usuario_id)
        .order_by(
            Transaccion.fecha.asc(),
            Transaccion.tipo.asc(),
            Transaccion.monto.asc(),
            Transaccion.descripcion.asc(),
        )
        .all()
    )
    lista_movimientos = []
    for tx in txs:
        lista_movimientos.append({
            "descripcion": tx.descripcion,
            "es_cuota_hija": bool(tx.es_cuota_hija),
            "es_padre_cuotas": bool(tx.es_padre_cuotas),
            "es_recurrente": bool(tx.es_recurrente),
            "estado_verificacion": tx.estado_verificacion.value if hasattr(tx.estado_verificacion, "value") else str(tx.estado_verificacion),
            "fecha": tx.fecha.isoformat(),
            "metodo_pago": tx.metodo_pago.value if tx.metodo_pago else None,
            "moneda": tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda),
            "monto": str(tx.monto),
            "origen": tx.origen.value if hasattr(tx.origen, "value") else str(tx.origen),
            "tipo": tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo),
        })

    # 2. Transferencias internas
    trs = (
        db.query(TransferenciaInterna)
        .filter(TransferenciaInterna.usuario_id == usuario_id)
        .order_by(
            TransferenciaInterna.fecha.asc(),
            TransferenciaInterna.monto_origen.asc(),
            TransferenciaInterna.notas.asc(),
        )
        .all()
    )
    lista_transferencias = []
    for tr in trs:
        lista_transferencias.append({
            "fecha": tr.fecha.isoformat(),
            "moneda": tr.moneda.value if hasattr(tr.moneda, "value") else str(tr.moneda),
            "monto_destino": str(tr.monto_destino),
            "monto_origen": str(tr.monto_origen),
            "notas": tr.notas,
        })

    # 3. Cuotas
    cuotas = (
        db.query(Cuota)
        .join(GrupoCuotas)
        .filter(GrupoCuotas.usuario_id == usuario_id)
        .order_by(
            Cuota.fecha_vencimiento.asc(),
            Cuota.numero_cuota.asc(),
            Cuota.monto_proyectado.asc(),
        )
        .all()
    )
    lista_cuotas = []
    for c in cuotas:
        lista_cuotas.append({
            "ajustada_manual": bool(c.ajustada_manual),
            "fecha_vencimiento": c.fecha_vencimiento.isoformat() if c.fecha_vencimiento else None,
            "monto_proyectado": str(c.monto_proyectado),
            "monto_real": str(c.monto_real) if c.monto_real is not None else None,
            "numero_cuota": int(c.numero_cuota),
            "pagada": bool(c.pagada),
        })

    # 4. Grupos de cuotas
    grupos = (
        db.query(GrupoCuotas)
        .filter(GrupoCuotas.usuario_id == usuario_id)
        .order_by(
            GrupoCuotas.descripcion.asc(),
            GrupoCuotas.cantidad_cuotas.asc(),
            GrupoCuotas.monto_total.asc(),
        )
        .all()
    )
    lista_grupos = []
    for g in grupos:
        lista_grupos.append({
            "cantidad_cuotas": int(g.cantidad_cuotas),
            "descripcion": g.descripcion,
            "monto_total": str(g.monto_total),
            "tasa_interes": str(g.tasa_interes) if g.tasa_interes is not None else None,
            "tiene_interes": bool(g.tiene_interes),
        })

    # 5. Rendimientos de billetera
    rends = (
        db.query(RendimientoBilletera)
        .join(Billetera)
        .filter(Billetera.usuario_id == usuario_id)
        .order_by(
            RendimientoBilletera.fecha.asc(),
            RendimientoBilletera.monto.asc(),
        )
        .all()
    )
    lista_rendimientos = []
    for r in rends:
        lista_rendimientos.append({
            "fecha": r.fecha.isoformat(),
            "monto": str(r.monto),
        })

    # 6. Movimientos de meta
    movs_meta = (
        db.query(MovimientoMeta)
        .join(Meta)
        .filter(Meta.usuario_id == usuario_id)
        .order_by(
            MovimientoMeta.fecha.asc(),
            MovimientoMeta.tipo.asc(),
            MovimientoMeta.monto.asc(),
        )
        .all()
    )
    lista_movimientos_meta = []
    for m in movs_meta:
        lista_movimientos_meta.append({
            "fecha": m.fecha.isoformat(),
            "moneda_movimiento": m.moneda_movimiento.value if hasattr(m.moneda_movimiento, "value") else str(m.moneda_movimiento),
            "monto": str(m.monto),
            "tipo": m.tipo.value if hasattr(m.tipo, "value") else str(m.tipo),
        })

    contenido = {
        "cuotas": lista_cuotas,
        "grupos_cuotas": lista_grupos,
        "movimientos": lista_movimientos,
        "movimientos_meta": lista_movimientos_meta,
        "rendimientos": lista_rendimientos,
        "transferencias": lista_transferencias,
    }

    canonico_str = json.dumps(contenido, sort_keys=True, ensure_ascii=False)
    sha256_hash = hashlib.sha256(canonico_str.encode("utf-8")).hexdigest()

    huella_data = {
        "sha256": sha256_hash,
        "conteos": {
            "movimientos": len(lista_movimientos),
            "transferencias": len(lista_transferencias),
            "cuotas": len(lista_cuotas),
            "grupos_cuotas": len(lista_grupos),
            "rendimientos": len(lista_rendimientos),
            "movimientos_meta": len(lista_movimientos_meta),
        },
        "contenido": contenido,
    }

    with open(HUELLA_PATH, "w", encoding="utf-8") as f:
        json.dump(huella_data, f, indent=2, ensure_ascii=False)

    return huella_data


def ejecutar_regeneracion(silent: bool = False, fecha_corte: Optional[date] = None) -> Dict[str, Any]:
    t_inicio = time.perf_counter()
    rng = random.Random(SEMILLA_FIJA)

    if fecha_corte is None:
        fecha_corte = hoy_argentina()

    db = SessionLocal()
    try:
        # 1. Seguridad y Canario
        user = verificar_canario_y_usuario(db)
        instalar_arnes_interceptores()
        snap_antes = snapshot_cuentas_ajenas(db)

        # 2. Borrado consistente
        conteos_borrados = ejecutar_borrado_testingadmin(db, user)

        # 3. Inicializar catálogo base
        cat = CatalogoEntidades(db, user)

        # 4. Generación de Ingresos (Sueldo, SAC, Freelance)
        res_ing = generar_ingresos(db, cat, rng, fecha_corte=fecha_corte)

        # 5. Generación de Gastos Fijos (Alquiler IPC, Expensas, Luz, Gas, Internet, Celular, Prepaga, Guitarra)
        res_fij = generar_fijos(db, cat, rng, fecha_corte=fecha_corte)

        # 6. Generación de Gastos Variables y Costumbres (Día a día, delivery, café, taxi, temporadas, carga incompleta)
        res_var = generar_variables(db, cat, rng, fecha_corte=fecha_corte)

        # 7. Generación de Tarjetas y Suscripciones (Compras 1 pago, cuotas, cobros suscripción, pago de resúmenes)
        res_tarj = generar_tarjetas_y_suscripciones(db, cat, rng, fecha_corte=fecha_corte)

        # 8. Generación de Metas e Inversiones (Aportes metas, ahorro remunerado, transferencias, presupuestos)
        res_met = generar_metas_e_inversiones(db, cat, rng, fecha_corte=fecha_corte)

        # 9. Seguridad: Verificar que ninguna cuenta ajena fue alterada
        snap_despues = snapshot_cuentas_ajenas(db)
        if snap_antes != snap_despues:
            raise RuntimeError(
                f"ABORT CRÍTICO: Se detectó alteración en cuentas ajenas: antes={snap_antes}, despues={snap_despues}"
            )

        # 10. Recopilar métricas de la base de datos
        total_tx = db.query(Transaccion).filter(Transaccion.usuario_id == user.id).count()
        total_tr = db.query(TransferenciaInterna).filter(TransferenciaInterna.usuario_id == user.id).count()
        total_mm = db.query(MovimientoMeta).join(Meta).filter(Meta.usuario_id == user.id).count()
        total_cuotas = db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id).count()
        cuotas_pagadas = db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id, Cuota.pagada == True).count()
        cuotas_impagas = db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id, Cuota.pagada == False).count()

        # Envíos interceptados
        interceptaciones = obtener_interceptaciones()

        # Consolidar archivo de verdad conocida
        verdad_conocida = {
            "usuario": {
                "email": user.email,
                "id": str(user.id),
                "semilla": SEMILLA_FIJA,
                "periodo_historial": {
                    "inicio": cat.ciclos[0].fecha_inicio.isoformat(),
                    "fin": cat.ciclos[-1].fecha_fin.isoformat(),
                    "total_ciclos": len(cat.ciclos),
                },
            },
            "ingresos": res_ing["verdad_ingresos"],
            "gastos_fijos": res_fij["verdad_fijos"],
            "gastos_variables_y_costumbres": res_var["verdad_variables"],
            "tarjetas_y_suscripciones": res_tarj["verdad_tarjetas_suscripciones"],
            "metas_e_inversiones": res_met["verdad_metas_inversiones"],
            "estadisticas_generacion": {
                "total_transacciones": total_tx,
                "total_transferencias": total_tr,
                "total_movimientos_meta": total_mm,
                "total_cuotas": total_cuotas,
                "cuotas_pagadas": cuotas_pagadas,
                "cuotas_impagas": cuotas_impagas,
                "total_envios_interceptados": len(interceptaciones),
            },
        }

        with open(VERDAD_CONOCIDA_PATH, "w", encoding="utf-8") as f:
            json.dump(verdad_conocida, f, indent=2, ensure_ascii=False)

        # 11. Generar huella canónica determinística (Decisión 4)
        huella = generar_huella_testingadmin(db, user.id)

        t_total = time.perf_counter() - t_inicio

        # 12. Reporte en consola si no es silencioso
        if not silent:
            imprimir_reporte(db, cat, total_tx, cuotas_pagadas, cuotas_impagas, len(res_tarj["verdad_tarjetas_suscripciones"]["resumenes_pagados"]), total_mm, total_tr, interceptaciones, huella["sha256"], t_total)

        return {
            "total_transacciones": total_tx,
            "verdad_conocida": verdad_conocida,
            "huella_sha256": huella["sha256"],
            "tiempo_segundos": t_total,
        }

    finally:
        db.close()


def imprimir_reporte(
    db: Session,
    cat: CatalogoEntidades,
    total_tx: int,
    cuotas_pagadas: int,
    cuotas_impagas: int,
    resumenes_pagados_count: int,
    total_movimientos_meta: int,
    total_transferencias: int,
    interceptaciones: list,
    huella_sha256: str,
    tiempo_total: float,
):
    lines = []
    lines.append("================================================================================")
    lines.append("=== EJECUCION DE LA REGENERACION (testingadmin@argentum.com) ===")
    lines.append("================================================================================")
    lines.append(f"Semilla utilizada: {SEMILLA_FIJA}")
    lines.append(f"Total movimientos (transacciones): {total_tx}")
    lines.append(f"Huella SHA256: {huella_sha256}")
    lines.append(f"Tiempo total de corrida: {tiempo_total:.2f} s")
    lines.append("")

    # Movimientos por tipo
    lines.append("--- 1. MOVIMIENTOS POR TIPO ---")
    txs_tipo = db.query(Transaccion.tipo, func.count(Transaccion.id)).filter(
        Transaccion.usuario_id == cat.user.id
    ).group_by(Transaccion.tipo).all()
    for tipo, cnt in txs_tipo:
        lines.append(f"  {tipo.value if hasattr(tipo, 'value') else tipo}: {cnt}")
    lines.append("")

    # Movimientos por ciclo
    lines.append("--- 2. MOVIMIENTOS POR CICLO ---")
    for ciclo in cat.ciclos:
        cnt_ciclo = db.query(Transaccion).filter(
            Transaccion.usuario_id == cat.user.id,
            Transaccion.fecha >= ciclo.fecha_inicio,
            Transaccion.fecha <= ciclo.fecha_fin,
        ).count()
        lines.append(f"  Ciclo {ciclo.numero:02d} ({ciclo.fecha_inicio} al {ciclo.fecha_fin}): {cnt_ciclo} movimientos")
    lines.append("")

    # Movimientos por categoría
    lines.append("--- 3. MOVIMIENTOS POR CATEGORIA ---")
    txs_cat = db.query(Categoria.nombre, func.count(Transaccion.id)).join(
        Transaccion, Transaccion.categoria_id == Categoria.id
    ).filter(
        Transaccion.usuario_id == cat.user.id
    ).group_by(Categoria.nombre).order_by(func.count(Transaccion.id).desc()).all()
    for nom_cat, cnt in txs_cat:
        lines.append(f"  {nom_cat:<28}: {cnt}")
    lines.append("")

    # Cuotas pagadas e impagas
    lines.append("--- 4. CUOTAS ---")
    lines.append(f"  Cuotas pagadas: {cuotas_pagadas}")
    lines.append(f"  Cuotas impagas (a vencer en el futuro): {cuotas_impagas}")
    lines.append("")

    # Resúmenes, suscripciones, metas y transferencias
    lines.append("--- 5. OPERACIONES VINCULADAS ---")
    lines.append(f"  Resúmenes de tarjeta pagados con servicio real: {resumenes_pagados_count}")
    cant_subs = db.query(Transaccion).filter(
        Transaccion.usuario_id == cat.user.id,
        Transaccion.suscripcion_id != None,
    ).count()
    lines.append(f"  Cobros históricos de suscripción registrados: {cant_subs}")
    lines.append(f"  Aportes a metas de ahorro registrados: {total_movimientos_meta}")
    lines.append(f"  Transferencias internas realizadas: {total_transferencias}")
    lines.append("")

    # Cantidad de envíos interceptados
    lines.append("--- 6. ENVIOS INTERCEPTADOS POR CANAL (SEGURIDAD) ---")
    canales_count = defaultdict(int)
    for inter in interceptaciones:
        canales_count[inter.get("canal", "desconocido")] += 1
    lines.append(f"  Total envíos interceptados y bloqueados: {len(interceptaciones)}")
    for canal, cnt in canales_count.items():
        lines.append(f"    - {canal}: {cnt}")
    lines.append("================================================================================")

    output_str = "\n".join(lines) + "\n"
    print(output_str)


if __name__ == "__main__":
    ejecutar_regeneracion()
