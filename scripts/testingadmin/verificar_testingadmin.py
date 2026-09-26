"""
Script Verificador Reutilizable de testingadmin@argentum.com.
Ubicación: scripts/testingadmin/verificar_testingadmin.py

Verificador determinístico en modo solo lectura. Inspecciona:
1. Conteos por tabla para testingadmin.
2. Ausencia de filas con fecha futura en tablas operativas.
3. Comparación entre saldo_actual guardado y calcular_saldo_teorico para cada billetera de testingadmin.
4. Cuotas vencidas impagas (debe ser 0).
5. Ingresos/sueldos con es_recurrente (debe ser 0).
6. Conciliación de billeteras de todos los demás usuarios con calcular_saldo_teorico (anonimizada, solo diferencias).
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from typing import Any, Dict, List, Tuple

# Asegurar raíz del backend en sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ["LOG_LEVEL"] = "CRITICAL"

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.models.billetera import Billetera
from app.models.cuota import Cuota
from app.models.grupo_cuotas import GrupoCuotas
from app.models.meta import Meta
from app.models.movimiento_meta import MovimientoMeta
from app.models.presupuesto import Presupuesto
from app.models.rendimiento_billetera import RendimientoBilletera
from app.models.suscripcion import Suscripcion
from app.models.tarjeta_credito import TarjetaCredito
from app.models.transaccion import TipoTransaccion, Transaccion
from app.models.transferencia_interna import TransferenciaInterna
from app.models.usuario import Usuario
from app.services.conciliacion_service import calcular_saldo_teorico
from app.utils.fecha import hoy_argentina

EMAIL_TESTINGADMIN = "testingadmin@argentum.com"
DIFERENCIA_CONOCIDA_OTROS = Decimal("-941.00")


def verificar_testingadmin(db: Session, fecha_corte=None) -> Tuple[bool, List[str]]:
    """
    Ejecuta todas las verificaciones en modo solo lectura.
    Retorna (exito, lineas_reporte).
    """
    if fecha_corte is None:
        fecha_corte = hoy_argentina()

    user = db.query(Usuario).filter(Usuario.email == EMAIL_TESTINGADMIN).first()
    if not user:
        return False, [f"ERROR CRITICO: Usuario {EMAIL_TESTINGADMIN} no encontrado en la base."]

    lineas: List[str] = []
    lineas.append("================================================================================")
    lineas.append(f"=== VERIFICACION DE ESTADO: {EMAIL_TESTINGADMIN} ===")
    lineas.append(f"=== Fecha de evaluacion: {fecha_corte.isoformat()} ===")
    lineas.append("================================================================================")
    lineas.append("")

    todo_correcto = True

    # --------------------------------------------------------------------------
    # 1. CONTEOS POR TABLA (testingadmin)
    # --------------------------------------------------------------------------
    lineas.append("--- 1. CONTEOS POR TABLA (testingadmin) ---")
    c_tx = db.query(Transaccion).filter(Transaccion.usuario_id == user.id).count()
    c_tr = db.query(TransferenciaInterna).filter(TransferenciaInterna.usuario_id == user.id).count()
    c_cuotas = db.query(Cuota).join(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id).count()
    c_grupos = db.query(GrupoCuotas).filter(GrupoCuotas.usuario_id == user.id).count()
    c_rends = db.query(RendimientoBilletera).join(Billetera).filter(Billetera.usuario_id == user.id).count()
    c_mm = db.query(MovimientoMeta).join(Meta).filter(Meta.usuario_id == user.id).count()
    c_bill = db.query(Billetera).filter(Billetera.usuario_id == user.id).count()
    c_metas = db.query(Meta).filter(Meta.usuario_id == user.id).count()
    c_pres = db.query(Presupuesto).filter(Presupuesto.usuario_id == user.id).count()
    c_tarj = db.query(TarjetaCredito).filter(TarjetaCredito.usuario_id == user.id).count()
    c_subs = db.query(Suscripcion).filter(Suscripcion.usuario_id == user.id).count()

    lineas.append(f"  Transacciones:           {c_tx}")
    lineas.append(f"  Transferencias internas: {c_tr}")
    lineas.append(f"  Cuotas totales:          {c_cuotas}")
    lineas.append(f"  Grupos de cuotas:        {c_grupos}")
    lineas.append(f"  Rendimientos billetera:  {c_rends}")
    lineas.append(f"  Movimientos de meta:     {c_mm}")
    lineas.append(f"  Billeteras:              {c_bill}")
    lineas.append(f"  Metas:                   {c_metas}")
    lineas.append(f"  Presupuestos:            {c_pres}")
    lineas.append(f"  Tarjetas:                {c_tarj}")
    lineas.append(f"  Suscripciones:           {c_subs}")
    lineas.append("")

    # --------------------------------------------------------------------------
    # 2. VERIFICACION DE FECHAS FUTURAS
    # --------------------------------------------------------------------------
    lineas.append("--- 2. VERIFICACION DE FECHAS FUTURAS ---")
    # Movimientos operativos directos (excluyendo cuotas hijas de tarjeta que representan vencimientos futuros)
    tx_futuras = db.query(Transaccion).filter(
        Transaccion.usuario_id == user.id,
        Transaccion.fecha > fecha_corte,
        Transaccion.es_cuota_hija == False,
    ).all()

    tr_futuras = db.query(TransferenciaInterna).filter(
        TransferenciaInterna.usuario_id == user.id,
        TransferenciaInterna.fecha > fecha_corte,
    ).all()

    rend_futuros = db.query(RendimientoBilletera).join(Billetera).filter(
        Billetera.usuario_id == user.id,
        func.date(RendimientoBilletera.fecha) > fecha_corte,
    ).all()

    mm_futuros = db.query(MovimientoMeta).join(Meta).filter(
        Meta.usuario_id == user.id,
        MovimientoMeta.fecha > fecha_corte,
    ).all()

    cant_futuras = len(tx_futuras) + len(tr_futuras) + len(rend_futuros) + len(mm_futuros)
    if cant_futuras == 0:
        lineas.append("  [OK] Ninguna fila con fecha posterior a la fecha de corte.")
        lineas.append(f"       (Transacciones directas: 0, Transferencias: 0, Rendimientos: 0, Movimientos meta: 0)")
    else:
        todo_correcto = False
        lineas.append(f"  [FALLO] Se encontraron {cant_futuras} registros con fecha futura:")
        for txf in tx_futuras:
            lineas.append(f"    - Tx: {txf.fecha} | {txf.descripcion} | {txf.monto}")
        for trf in tr_futuras:
            lineas.append(f"    - Transferencia: {trf.fecha} | {trf.notas} | {trf.monto_origen}")
        for rf in rend_futuros:
            lineas.append(f"    - Rendimiento: {rf.fecha} | {rf.monto}")
        for mmf in mm_futuros:
            lineas.append(f"    - Movimiento Meta: {mmf.fecha} | {mmf.monto}")
    lineas.append("")

    # --------------------------------------------------------------------------
    # 3. SALDO GUARDADO VS CALCULAR_SALDO_TEORICO (testingadmin)
    # --------------------------------------------------------------------------
    lineas.append("--- 3. CONCILIACION BILLETERAS TESTINGADMIN ---")
    billeteras = db.query(Billetera).filter(Billetera.usuario_id == user.id).order_by(Billetera.nombre).all()
    descuadres_testingadmin = 0

    for b in billeteras:
        s_guardado = Decimal(str(b.saldo_actual or 0)).quantize(Decimal("0.01"))
        s_teorico = calcular_saldo_teorico(db, b.id, hasta=fecha_corte)
        diff = s_guardado - s_teorico

        if diff == Decimal("0.00"):
            estado = "[OK]"
        else:
            estado = "[DESCUADRE]"
            descuadres_testingadmin += 1
            todo_correcto = False

        lineas.append(
            f"  {estado} {b.nombre:<25} ({b.moneda.value}): "
            f"Guardado={s_guardado:>14} | Teórico={s_teorico:>14} | Diff={diff:>10}"
        )

    if descuadres_testingadmin == 0:
        lineas.append("  -> Todas las billeteras de testingadmin concilian con diferencia 0.00")
    else:
        lineas.append(f"  -> ATENCION: {descuadres_testingadmin} billeteras presentan descuadre en testingadmin")
    lineas.append("")

    # --------------------------------------------------------------------------
    # 4. CUOTAS VENCIDAS IMPAGAS
    # --------------------------------------------------------------------------
    lineas.append("--- 4. CUOTAS VENCIDAS IMPAGAS ---")
    cuotas_vencidas_impagas = db.query(Cuota).join(GrupoCuotas).filter(
        GrupoCuotas.usuario_id == user.id,
        Cuota.pagada == False,
        Cuota.fecha_vencimiento <= fecha_corte,
    ).all()

    if len(cuotas_vencidas_impagas) == 0:
        lineas.append("  [OK] 0 cuotas vencidas impagas.")
    else:
        todo_correcto = False
        lineas.append(f"  [FALLO] Se encontraron {len(cuotas_vencidas_impagas)} cuotas vencidas impagas:")
        for cvi in cuotas_vencidas_impagas:
            lineas.append(f"    - Cuota #{cvi.numero_cuota} vto={cvi.fecha_vencimiento} monto={cvi.monto_proyectado}")
    lineas.append("")

    # --------------------------------------------------------------------------
    # 5. INGRESOS CON ES_RECURRENTE = TRUE
    # --------------------------------------------------------------------------
    lineas.append("--- 5. INGRESOS CON ES_RECURRENTE ---")
    ingresos_recurrentes = db.query(Transaccion).filter(
        Transaccion.usuario_id == user.id,
        Transaccion.tipo == TipoTransaccion.INGRESO,
        Transaccion.es_recurrente == True,
    ).all()

    if len(ingresos_recurrentes) == 0:
        lineas.append("  [OK] 0 ingresos con es_recurrente = True.")
    else:
        todo_correcto = False
        lineas.append(f"  [FALLO] Se encontraron {len(ingresos_recurrentes)} ingresos con es_recurrente = True:")
        for ir in ingresos_recurrentes:
            lineas.append(f"    - {ir.fecha} | {ir.descripcion} | {ir.monto}")
    lineas.append("")

    # --------------------------------------------------------------------------
    # 6. CONCILIACION DE OTROS USUARIOS (ANONIMIZADA)
    # --------------------------------------------------------------------------
    lineas.append("--- 6. CONCILIACION OTROS USUARIOS (ANONIMIZADA, SOLO DIFERENCIAS) ---")
    otros_usuarios = (
        db.query(Usuario)
        .filter(Usuario.email != EMAIL_TESTINGADMIN)
        .order_by(Usuario.email)
        .all()
    )

    total_billeteras_otros = 0
    diferencias_otros = []

    for u_idx, u in enumerate(otros_usuarios, start=1):
        b_otros = db.query(Billetera).filter(Billetera.usuario_id == u.id).order_by(Billetera.nombre).all()
        for b in b_otros:
            total_billeteras_otros += 1
            s_guardado = Decimal(str(b.saldo_actual or 0)).quantize(Decimal("0.01"))
            s_teorico = calcular_saldo_teorico(db, b.id, hasta=fecha_corte)
            diff = s_guardado - s_teorico

            if diff != Decimal("0.00"):
                diferencias_otros.append({
                    "usuario_alias": f"Usuario_{u_idx:02d}",
                    "billetera_alias": f"{b.nombre} ({b.moneda.value})",
                    "guardado": s_guardado,
                    "teorico": s_teorico,
                    "diferencia": diff,
                })

    lineas.append(f"  Total billeteras evaluadas de otros usuarios: {total_billeteras_otros}")
    lineas.append(f"  Billeteras con diferencia != 0: {len(diferencias_otros)}")

    descuadres_inesperados = 0
    for item in diferencias_otros:
        es_conocida = (item["diferencia"] == DIFERENCIA_CONOCIDA_OTROS)
        tag = "[DIFERENCIA CONOCIDA BASELINE]" if es_conocida else "[DESCUADRE INESPERADO]"
        lineas.append(
            f"    {tag} {item['usuario_alias']} | {item['billetera_alias']} -> "
            f"Guardado={item['guardado']}, Teorico={item['teorico']}, Diff={item['diferencia']}"
        )
        if not es_conocida:
            descuadres_inesperados += 1
            todo_correcto = False

    if descuadres_inesperados == 0 and len(diferencias_otros) == 1:
        lineas.append("  [OK] Todos los demás usuarios concilian exactamente, salvo la diferencia conocida de -941.00.")
    elif len(diferencias_otros) == 0:
        lineas.append("  [OK] Todas las billeteras de los demás usuarios concilian al 100%.")
    else:
        lineas.append(f"  [FALLO] Existen {descuadres_inesperados} diferencias no esperadas en otros usuarios.")
    lineas.append("")

    # --------------------------------------------------------------------------
    # RESUMEN FINAL
    # --------------------------------------------------------------------------
    lineas.append("================================================================================")
    if todo_correcto:
        lineas.append("=== RESULTADO GLOBAL: VERIFICACION EXITOSA (TODO EN VERDE) ===")
    else:
        lineas.append("=== RESULTADO GLOBAL: FALLO EN VERIFICACION ===")
    lineas.append("================================================================================")

    return todo_correcto, lineas


def main():
    parser = argparse.ArgumentParser(description="Verificador reutilizable de testingadmin")
    parser.add_argument("--output", "-o", type=str, default=None, help="Ruta del archivo de salida")
    args = parser.parse_args()

    script_path = os.path.abspath(__file__)
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(script_path), "..", ".."))

    with open(script_path, "r", encoding="utf-8") as f:
        codigo_propio = f.read()

    db = SessionLocal()
    try:
        exito, lineas_reporte = verificar_testingadmin(db)
    finally:
        db.close()

    header_lines = [
        "================================================================================",
        f"=== CODIGO DEL SCRIPT: {os.path.relpath(script_path, backend_dir)} ===",
        "================================================================================",
        codigo_propio.strip(),
        "",
    ]
    contenido_completo = "\n".join(header_lines + lineas_reporte) + "\n"

    print(contenido_completo)

    if args.output:
        out_path = os.path.abspath(args.output)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(contenido_completo)
        print(f"Reporte guardado en {out_path}")

    sys.exit(0 if exito else 1)


if __name__ == "__main__":
    main()
