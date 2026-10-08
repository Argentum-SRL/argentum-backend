"""
Evaluador del detector de gastos fijos (Fase 4d1).
Ubicación: scripts/motor/evaluar_patrones.py

Este script evalúa el detector puro de gastos fijos (app/utils/patrones.py) en tres ámbitos:
E1. Personas sintéticas (P01 a P10) con verdad conocida, métricas globales y grilla de hiperparámetros.
E2. Usuario testingadmin@argentum.com en base local de solo lectura.
E3. Usuarios reales anonimizados (Usuario_NN ordenados por fecha_registro) informando únicamente cantidades.

Red de seguridad:
- Sesión de base de datos estrictamente de solo lectura.
- Listener before_commit que lanza RuntimeError ante cualquier intento de commit.
- Cierre garantizado con rollback().
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

# Asegurar raíz del backend en sys.path
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from scripts.local.base_actual import imprimir_base_actual
imprimir_base_actual()

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.transaccion import TipoTransaccion
from app.models.usuario import Usuario
from app.services.datos_motor_service import cargar_datos_motor
from app.services.definiciones_service import ContextoDefiniciones
from app.utils.fecha import hoy_argentina
from app.utils.patrones import (
    clave_patron,
    detectar_fijos,
    movimientos_elegibles,
)
from tests.motor.personas import generar_todas_las_personas
from tests.motor.personas.catalogo import IPC_MAP
from tests.motor.personas.generador import FECHA_FIN_HISTORIA
from tests.motor.personas.modelos import Persona


# Decisión del 08/10 (Sebastián): Fijo es lo que se paga una vez por mes, cada dos meses
# o una vez por año con un monto parecido, de cualquier rubro.
# Estos grupos son considerados verdad de gasto fijo según la regla 1A.
REGLA_1A = {
    ("P01", "gimnasio"),
    ("P09", "gimnasio"),
    ("P02", "cuidado_personal"),
}


def es_fijo_verdad(persona_id: str, grupo_nombre: str, tipo_verdadero: str) -> bool:
    """Determina si un grupo es gasto fijo según la verdad conocida y la decisión del 08/10."""
    if tipo_verdadero == "gasto_fijo":
        return True
    if (persona_id, grupo_nombre) in REGLA_1A:
        return True
    return False


def sesion_solo_lectura() -> Session:
    """Crea una sesión SQLAlchemy con bloqueo estricto contra escrituras."""
    db = SessionLocal()

    @event.listens_for(db, "before_commit")
    def _bloquear_commit(session: Session) -> None:
        raise RuntimeError("Intento de escritura bloqueado en script de solo lectura.")

    return db


def evaluar_personas_sinteticas() -> None:
    """E1: Evaluación sobre las 10 personas sintéticas con verdad conocida."""
    print("=" * 80)
    print("=== E1. EVALUACIÓN CON PERSONAS SINTÉTICAS ===")
    print("=" * 80)

    personas = generar_todas_las_personas()
    fecha_ref = FECHA_FIN_HISTORIA

    def _armar_ctx(hoy_f: date) -> ContextoDefiniciones:
        return ContextoDefiniciones(
            grupos_cuotas_cantidades={},
            billeteras_inversion_ids=set(),
            categoria_ahorro_ids=set(),
            subcategoria_tarjeta_id=None,
            subcategoria_tarjeta_ids=set(),
            hoy=hoy_f,
        )

    ctx_base = _armar_ctx(fecha_ref)

    # Detalle por persona y grupo de egreso
    print("\n--- DETALLE POR PERSONA Y GRUPO DE EGRESO ---")
    todos_los_egresos: list[tuple[Persona, Any, bool]] = []
    ids_fuerte_base: set[Any] = set()
    ids_debil_base: set[Any] = set()

    total_grupos_fijo_verdad = 0
    grupos_fijo_detectados_50 = 0

    for p in personas:
        print(f"\nPersona {p.id} ({p.nombre}):")
        res_p = detectar_fijos(p.movimientos, IPC_MAP, fecha_ref, ctx=ctx_base)

        patrones_por_tx_id: dict[Any, str] = {}
        for f in res_p.fijos:
            for tx_id in f.transacciones_ids:
                patrones_por_tx_id[tx_id] = f.fuerza
                if f.fuerza == "fuerte":
                    ids_fuerte_base.add(tx_id)
                else:
                    ids_debil_base.add(tx_id)

        # Mapa de motivo para descartados
        motivo_descarte_por_clave: dict[str, str] = {
            d.clave: d.motivo for d in res_p.descartados
        }

        # Agrupar movimientos de egreso por grupo_verdadero
        movs_egreso = [m for m in p.movimientos if m.tipo == TipoTransaccion.EGRESO]
        por_grupo: dict[str, list[Any]] = defaultdict(list)
        for m in movs_egreso:
            por_grupo[m.grupo_verdadero].append(m)

        for g_nom, g_txs in sorted(por_grupo.items()):
            tipo_v = g_txs[0].tipo_verdadero
            es_fijo_v = es_fijo_verdad(p.id, g_nom, tipo_v)
            if es_fijo_v:
                total_grupos_fijo_verdad += 1

            for m in g_txs:
                todos_los_egresos.append((p, m, es_fijo_v))

            # Determinar resultado asignado al grupo
            fuerzas_en_grupo = [patrones_por_tx_id[m.id] for m in g_txs if m.id in patrones_por_tx_id]
            cant_en_patron = len(fuerzas_en_grupo)

            if es_fijo_v and cant_en_patron >= (len(g_txs) / 2):
                grupos_fijo_detectados_50 += 1

            if "fuerte" in fuerzas_en_grupo:
                res_str = "fuerte"
            elif "debil" in fuerzas_en_grupo:
                res_str = "debil"
            else:
                c_grupo = clave_patron(g_txs[0])
                if c_grupo in motivo_descarte_por_clave:
                    motivo = f"no_detectado ({motivo_descarte_por_clave[c_grupo]})"
                else:
                    motivo = "no_detectado"
                res_str = motivo

            v_str = "gasto_fijo (verdad)" if es_fijo_v else f"{tipo_v} (no fijo)"
            print(f"  Grupo {g_nom:<22} | Verdad: {v_str:<22} | Resultado: {res_str:<28} | Movimientos: {len(g_txs):2d}")

    # Global por movimiento
    print("\n--- MÉTRICAS GLOBALES POR MOVIMIENTO ---")
    # Caso A: Fuerte + Débil
    tp_fd = sum(1 for _, m, v in todos_los_egresos if v and (m.id in ids_fuerte_base or m.id in ids_debil_base))
    fp_fd = sum(1 for _, m, v in todos_los_egresos if not v and (m.id in ids_fuerte_base or m.id in ids_debil_base))
    fn_fd = sum(1 for _, m, v in todos_los_egresos if v and (m.id not in ids_fuerte_base and m.id not in ids_debil_base))
    prec_fd = (tp_fd / (tp_fd + fp_fd)) if (tp_fd + fp_fd) > 0 else 0.0
    rec_fd = (tp_fd / (tp_fd + fn_fd)) if (tp_fd + fn_fd) > 0 else 0.0

    # Caso B: Solo Fuerte
    tp_f = sum(1 for _, m, v in todos_los_egresos if v and m.id in ids_fuerte_base)
    fp_f = sum(1 for _, m, v in todos_los_egresos if not v and m.id in ids_fuerte_base)
    fn_f = sum(1 for _, m, v in todos_los_egresos if v and m.id not in ids_fuerte_base)
    prec_f = (tp_f / (tp_f + fp_f)) if (tp_f + fp_f) > 0 else 0.0
    rec_f = (tp_f / (tp_f + fn_f)) if (tp_f + fn_f) > 0 else 0.0

    print(f"Línea base COMPROMISO actual: TP 444, FP 0, FN 72 (Precisión 100.0%, Exhaustividad {444/516*100:.2f}%)")
    print(f"Fuerte + Débil:  TP={tp_fd:3d}, FP={fp_fd:3d}, FN={fn_fd:3d} | Precisión={prec_fd*100:.2f}% | Exhaustividad={rec_fd*100:.2f}%")
    print(f"Solo Fuerte:     TP={tp_f:3d}, FP={fp_f:3d}, FN={fn_f:3d} | Precisión={prec_f*100:.2f}% | Exhaustividad={rec_f*100:.2f}%")

    # Por grupo
    print("\n--- DETECCIÓN POR GRUPO (>= 50% de movimientos en patrón) ---")
    rec_g = (grupos_fijo_detectados_50 / total_grupos_fijo_verdad) if total_grupos_fijo_verdad > 0 else 0.0
    print(f"Grupos fijos detectados: {grupos_fijo_detectados_50} de {total_grupos_fijo_verdad} ({rec_g*100:.2f}%)")

    # Grilla de hiperparámetros
    print("\n--- GRILLA DE DISPERSIÓN MÁXIMA Y PRESENCIA MÍNIMA ---")
    disp_valores = [Decimal("0.10"), Decimal("0.15"), Decimal("0.20"), Decimal("0.30")]
    pres_valores = [Decimal("0.70"), Decimal("0.80")]

    print(f"{'disp_max':<10} | {'pres_min':<10} | {'Prec (F+D)':<12} | {'Rec (F+D)':<12} | {'Prec (Fuerte)':<14} | {'Rec (Fuerte)':<14} | Grupos no fijos como fijos")
    print("-" * 110)

    for d_val in disp_valores:
        for p_val in pres_valores:
            ids_f_grid: set[Any] = set()
            ids_d_grid: set[Any] = set()
            no_fijos_detectados: list[str] = []

            for p in personas:
                res_grid = detectar_fijos(
                    p.movimientos,
                    IPC_MAP,
                    fecha_ref,
                    ctx=ctx_base,
                    dispersion_max=d_val,
                    presencia_min=p_val,
                )
                ids_en_patron_p: set[Any] = set()
                for f in res_grid.fijos:
                    for tx_id in f.transacciones_ids:
                        ids_en_patron_p.add(tx_id)
                        if f.fuerza == "fuerte":
                            ids_f_grid.add(tx_id)
                        else:
                            ids_d_grid.add(tx_id)

                # Identificar si algún grupo no fijo fue detectado
                movs_eg = [m for m in p.movimientos if m.tipo == TipoTransaccion.EGRESO]
                grupos_p: dict[str, list[Any]] = defaultdict(list)
                for m in movs_eg:
                    grupos_p[m.grupo_verdadero].append(m)

                for g_nom, g_txs in grupos_p.items():
                    if not es_fijo_verdad(p.id, g_nom, g_txs[0].tipo_verdadero):
                        if any(m.id in ids_en_patron_p for m in g_txs):
                            no_fijos_detectados.append(f"{p.id}:{g_nom}")

            tp_g_fd = sum(1 for _, m, v in todos_los_egresos if v and (m.id in ids_f_grid or m.id in ids_d_grid))
            fp_g_fd = sum(1 for _, m, v in todos_los_egresos if not v and (m.id in ids_f_grid or m.id in ids_d_grid))
            fn_g_fd = sum(1 for _, m, v in todos_los_egresos if v and (m.id not in ids_f_grid and m.id not in ids_d_grid))
            prec_g_fd = (tp_g_fd / (tp_g_fd + fp_g_fd)) if (tp_g_fd + fp_g_fd) > 0 else 0.0
            rec_g_fd = (tp_g_fd / (tp_g_fd + fn_g_fd)) if (tp_g_fd + fn_g_fd) > 0 else 0.0

            tp_g_f = sum(1 for _, m, v in todos_los_egresos if v and m.id in ids_f_grid)
            fp_g_f = sum(1 for _, m, v in todos_los_egresos if not v and m.id in ids_f_grid)
            fn_g_f = sum(1 for _, m, v in todos_los_egresos if v and m.id not in ids_f_grid)
            prec_g_f = (tp_g_f / (tp_g_f + fp_g_f)) if (tp_g_f + fp_g_f) > 0 else 0.0
            rec_g_f = (tp_g_f / (tp_g_f + fn_g_f)) if (tp_g_f + fn_g_f) > 0 else 0.0

            no_fijos_str = ", ".join(no_fijos_detectados) if no_fijos_detectados else "ninguno (0 FP)"
            print(
                f"{str(d_val):<10} | {str(p_val):<10} | {prec_g_fd*100:>10.2f}% | {rec_g_fd*100:>10.2f}% | "
                f"{prec_g_f*100:>12.2f}% | {rec_g_f*100:>12.2f}% | {no_fijos_str}"
            )


def evaluar_testingadmin(db: Session) -> None:
    """E2: Evaluación detallada de testingadmin en copia local."""
    print("\n" + "=" * 80)
    print("=== E2. TESTINGADMIN (BASE LOCAL) ===")
    print("=" * 80)

    u = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
    if not u:
        print("Usuario testingadmin@argentum.com no encontrado en la base local.")
        return

    hoy = hoy_argentina()
    datos = cargar_datos_motor(db, u, hoy)
    res = detectar_fijos(datos["txs"], datos["ipc"], datos["hoy"], ctx=datos["ctx"])

    print(f"\nFecha destino: {hoy}")
    print(f"Total movimientos elegibles: {len(movimientos_elegibles(datos['txs'], datos['ctx']))}")
    print(f"Patrones fijos detectados: {len(res.fijos)}")
    print(f"Agrupaciones descartadas: {len(res.descartados)}")

    print("\n--- PATRONES FIJOS DETECTADOS ---")
    if not res.fijos:
        print("  Ningún patrón fijo detectado.")
    for idx, f in enumerate(res.fijos, 1):
        print(f"Patrón #{idx}:")
        print(f"  clave:                     {f.clave}")
        print(f"  descripcion:               {f.descripcion}")
        print(f"  categoria_id:              {f.categoria_id}")
        print(f"  subcategoria_id:           {f.subcategoria_id}")
        print(f"  billetera_id:              {f.billetera_id}")
        print(f"  moneda:                    {f.moneda}")
        print(f"  frecuencia:                {f.frecuencia}")
        print(f"  fuerza:                    {f.fuerza}")
        print(f"  ocurrencias:               {f.ocurrencias}")
        print(f"  presencia:                 {f.presencia}")
        print(f"  dispersion_monto:          {f.dispersion_monto}")
        print(f"  monto_mediano_deflactado:  {f.monto_mediano_deflactado}")
        print(f"  ultimo_monto:              {f.ultimo_monto}")
        print(f"  dia_tipico:                {f.dia_tipico}")
        print(f"  ultima_fecha:              {f.ultima_fecha}")
        print(f"  proxima_fecha:             {f.proxima_fecha}")
        print(f"  transacciones_ids:         ({len(f.transacciones_ids)} IDs) {f.transacciones_ids}")

    print("\n--- DESCARTADOS CON 2 O MÁS OCURRENCIAS ---")
    descartados_relevantes = [d for d in res.descartados if d.ocurrencias >= 2]
    if not descartados_relevantes:
        print("  Ningún descartado con >= 2 ocurrencias.")
    for d in descartados_relevantes:
        print(f"  clave: {d.clave:<30} | moneda: {d.moneda} | ocurrencias: {d.ocurrencias:2d} | motivo: {d.motivo:<20} | dispersión: {d.dispersion_monto}")


def evaluar_usuarios_reales(db: Session) -> None:
    """E3: Métricas puramente cuantitativas para usuarios reales anonimizados."""
    print("\n" + "=" * 80)
    print("=== E3. USUARIOS REALES (SOLO CANTIDADES) ===")
    print("=" * 80)

    usuarios = db.execute(
        select(Usuario).order_by(Usuario.fecha_registro)
    ).scalars().all()

    hoy = hoy_argentina()
    conteo_anon = 1

    for u in usuarios:
        if u.email == "testingadmin@argentum.com":
            continue

        anon_id = f"Usuario_{conteo_anon:02d}"
        conteo_anon += 1

        try:
            datos = cargar_datos_motor(db, u, hoy)
            elegibles = movimientos_elegibles(datos["txs"], datos["ctx"])
            res = detectar_fijos(datos["txs"], datos["ipc"], datos["hoy"], ctx=datos["ctx"])

            fuertes = sum(1 for f in res.fijos if f.fuerza == "fuerte")
            debiles = sum(1 for f in res.fijos if f.fuerza == "debil")

            descartados_por_motivo: dict[str, int] = defaultdict(int)
            for d in res.descartados:
                descartados_por_motivo[d.motivo] += 1

            dict_desc_str = ", ".join(f"{k}: {v}" for k, v in sorted(descartados_por_motivo.items()))
            print(
                f"{anon_id}: elegibles={len(elegibles):3d} | fijos_fuertes={fuertes:2d} | fijos_debiles={debiles:2d} | descartados=[{dict_desc_str}]"
            )
        except Exception as exc:
            print(f"{anon_id}: ERROR {exc}")


def main() -> None:
    """Punto de entrada principal para la ejecución de E1, E2 y E3."""
    evaluar_personas_sinteticas()

    db = sesion_solo_lectura()
    try:
        evaluar_testingadmin(db)
        evaluar_usuarios_reales(db)
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    main()
