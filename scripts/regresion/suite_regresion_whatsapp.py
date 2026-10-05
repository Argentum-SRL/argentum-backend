"""
Suite consolidada de regresión de WhatsApp para Argentum.
Punto de entrada modularizado con catálogo y escenarios separados.
"""
from __future__ import annotations

import sys
import os
import time
import argparse
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

# Asegurar path al backend
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from scripts.local.base_actual import imprimir_base_actual
imprimir_base_actual()

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

from app.core.database import SessionLocal, engine
from app.models.transaccion import Transaccion

from scripts.regresion.suite import comun, escenarios_p05
from scripts.regresion.suite.comun import (
    DIR_GRABACIONES,
    PROMPT_FILE,
    USUARIO_PRUEBAS_EMAIL,
    GestorGrabacionesIA,
    ColectorSalidas,
    verificar_antiguedad_grabaciones,
    resolver_datos_base,
)
from scripts.regresion.suite.controles import (
    obtener_conteos_base,
    obtener_saldos_21,
    verificar_saldos_contra_referencia,
    verificar_reconciliacion_billeteras,
)
from scripts.regresion.suite.escenarios_p16 import GRABACIONES_P16
from scripts.regresion.suite.catalogo import obtener_catalogo


def _ejecutar_suite(verbose: bool = False, ia_real: bool = False, regrabar: bool = False, forzar_grabadas: bool = False, solo_escenario: str | None = None, volcar_salidas: str | None = None):
    if volcar_salidas:
        colector = ColectorSalidas(volcar_salidas)
    else:
        colector = None
    comun._colector_salidas = colector
    escenarios_p05._colector_salidas = colector
    _colector_salidas = colector

    gestor = GestorGrabacionesIA(
        dir_grabaciones=DIR_GRABACIONES,
        ia_real=ia_real,
        regrabar=regrabar,
        forzar_grabadas=forzar_grabadas,
    )
    gestor._cache_grabaciones.update(GRABACIONES_P16)
    comun._gestor_actual = gestor
    _gestor_actual = gestor

    t0_suite = time.perf_counter()

    ok_ant, msg_ant = verificar_antiguedad_grabaciones(DIR_GRABACIONES, PROMPT_FILE)
    if not ok_ant:
        print(f"[AVISO] {msg_ant}")

    db = SessionLocal()
    datos = resolver_datos_base(db)
    u_admin = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    if u_admin.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Verificación de usuario fallida. Resuelto: {u_admin.email}")
    
    conteos_inicio = obtener_conteos_base(db, u_admin.id)
    saldos_inicio_21 = obtener_saldos_21(db)
    movs_otros_inicio = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id != u_admin.id)).scalar()
    total_billeteras = len(saldos_inicio_21)
    db.close()

    from app.utils.fecha import hoy_argentina
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    escenarios = obtener_catalogo(datos, hoy=hoy, ayer=ayer)

    if solo_escenario:
        escenarios = [e for e in escenarios if e["id"] == solo_escenario]
        if not escenarios:
            print(f"[ERROR] No se encontró el escenario con id: {solo_escenario}")
            return 0, 0, 0, 1, []

    total = len(escenarios)
    aprobados = 0
    omitidos = 0
    fallidos = 0
    detalles_fallidos = []

    if verbose:
        print(f"Total escenarios: {total}\n")

    for i, esc in enumerate(escenarios, 1):
        eid = esc["id"]
        punto = esc["punto"]
        nombre = esc["nombre"]

        if esc.get("omitido"):
            omitidos += 1
            if verbose:
                print(f"[{eid}] {nombre}: OMITIDO (Motivo: {esc['motivo']})")
            continue

        esperado = esc["esperado"]
        match_tipo = esc["match"]

        _gestor_actual.iniciar_escenario(eid)
        if _colector_salidas is not None:
            _colector_salidas.iniciar_escenario(eid)
        t0 = time.perf_counter()
        try:
            obtenido = esc["ejecutar"]()
            dur = time.perf_counter() - t0

            if match_tipo == "exacto":
                pasa = (obtenido.strip() == esperado.strip())
            else:
                pasa = (esperado.strip() in obtenido.strip())

            if pasa:
                aprobados += 1
                if verbose:
                    print(f"[{eid}] {nombre}: APROBADO ({dur:.2f}s)")
            else:
                fallidos += 1
                print(f"[{eid}] {nombre}: FALLIDO ({dur:.2f}s) | Esperado: '{esperado.strip()}' | Obtenido: '{obtenido.strip()}'")
                detalles_fallidos.append({
                    "id": eid,
                    "nombre": nombre,
                    "esperado": esperado,
                    "obtenido": obtenido,
                })
        except Exception as e:
            dur = time.perf_counter() - t0
            fallidos += 1
            print(f"[{eid}] {nombre}: ERROR ({dur:.2f}s) -> {e}")
            detalles_fallidos.append({
                "id": eid,
                "nombre": nombre,
                "esperado": esperado,
                "obtenido": f"EXCEPCION: {type(e).__name__}: {e}",
            })

        if _colector_salidas is not None and eid not in _colector_salidas.salidas:
            _colector_salidas.salidas[eid] = {
                "mensajes": list(_colector_salidas._mensajes_escenario),
                "movimientos": [],
                "conversaciones": [],
            }

    dur_total = time.perf_counter() - t0_suite

    if _colector_salidas is not None:
        _colector_salidas.guardar()

    # Verificación estricta de rollback y conteos
    db = SessionLocal()
    conteos_fin = obtener_conteos_base(db, u_admin.id)
    movs_otros_fin = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id != u_admin.id)).scalar()
    db.close()

    movs_otros_nuevos = movs_otros_fin - movs_otros_inicio

    saldos_intactos = (conteos_inicio["saldos"] == conteos_fin["saldos"])
    sin_residuos = (
        conteos_inicio["tx"] == conteos_fin["tx"] and
        conteos_inicio["conv"] == conteos_fin["conv"] and
        conteos_inicio["tr"] == conteos_fin["tr"] and
        conteos_inicio["mm"] == conteos_fin["mm"] and
        conteos_inicio["msg"] == conteos_fin["msg"] and
        saldos_intactos
    )

    # Verificación de saldos contra referencia histórica de testingadmin
    db_ref = SessionLocal()
    saldos_ref_ok, desvios_ref, detalles_ref = verificar_saldos_contra_referencia(db_ref, saldos_inicio_21)
    db_ref.close()

    billeteras_ajenas_cambiadas = sum(
        1 for d in detalles_ref if d["email"] != USUARIO_PRUEBAS_EMAIL and d["diff"] is not None and d["diff"] != Decimal("0.00")
    )

    # Verificación de reconciliación de saldos en todas las 21 billeteras con aislamiento REPEATABLE READ
    session_factory_rr = sessionmaker(
        bind=engine.execution_options(isolation_level="REPEATABLE READ"),
        autocommit=False,
        autoflush=False,
    )
    db_rec = session_factory_rr()
    try:
        rec_ok, discrepancias, detalles_rec = verificar_reconciliacion_billeteras(db_rec)
    finally:
        db_rec.close()

    disparos_guarda = comun._disparos_guarda

    # Anonimización para reportes (Decisión 2: testingadmin con email real, demás Usuario_01, Usuario_02, ...)
    otros_emails = sorted(list({
        d["email"] for d in (detalles_ref + detalles_rec)
        if d.get("email") and d["email"] != USUARIO_PRUEBAS_EMAIL
    }))
    mapa_anon = {em: f"Usuario_{i+1:02d}" for i, em in enumerate(otros_emails)}

    def _fmt_email(em: str | None) -> str:
        if not em or em == USUARIO_PRUEBAS_EMAIL:
            return em or "testingadmin@argentum.com"
        return mapa_anon.get(em, "Usuario_XX")

    if verbose:
        print("\n=== RESUMEN DE EJECUCION ===")
        print(f"Total: {total} | Aprobados: {aprobados} | Omitidos: {omitidos} | Fallidos: {fallidos} | Tiempo: {dur_total:.2f}s")
        print(f"Llamadas IA: {_gestor_actual.llamadas_grabadas} grabadas, {_gestor_actual.llamadas_reales} reales")

        if detalles_fallidos:
            print("\n=== DETALLE DE ESCENARIOS FALLIDOS ===")
            for d in detalles_fallidos:
                print(f"\n--- [{d['id']}] {d['nombre']} ---")
                print("ESPERADO:")
                print(d["esperado"])
                print("OBTENIDO:")
                print(d["obtenido"])

        print("\n=== VERIFICACION DE ROLLBACK Y CONTEOS ===")
        print(f"Transacciones: antes={conteos_inicio['tx']} | después={conteos_fin['tx']}")
        print(f"Conversaciones: antes={conteos_inicio['conv']} | después={conteos_fin['conv']}")
        print(f"Transferencias internas: antes={conteos_inicio['tr']} | después={conteos_fin['tr']}")
        print(f"Movimientos meta: antes={conteos_inicio['mm']} | después={conteos_fin['mm']}")
        print(f"Mensajes procesados: antes={conteos_inicio['msg']} | después={conteos_fin['msg']}")
        print(f"Saldos de billeteras intactos: {'SÍ' if saldos_intactos else 'NO'}")
        print(f"¿Rollback total verificado (cero residuo)?: {'SÍ' if sin_residuos else 'NO'}")

        print(f"\n=== VERIFICACION DE SALDOS CONTRA REFERENCIA HISTORICA ({total_billeteras} BILLETERAS) ===")
        for d in detalles_ref:
            u_label = _fmt_email(d["email"])
            if d["email"] == USUARIO_PRUEBAS_EMAIL:
                st = "OK" if d["diff"] == Decimal("0.00") else f"DESVIO ({d['diff']})"
                print(f"  {u_label} | {d['billetera']} {d['moneda']}: antes={d['referencia']} | después={d['actual']} | diferencia={d['diff']} | criterio=referencia -> {st}")
            else:
                st = "OK" if d["diff"] == Decimal("0.00") else f"CAMBIO ({d['diff']})"
                print(f"  {u_label} | {d['billetera']} {d['moneda']}: antes={d['saldo_inicial']} | después={d['actual']} | diferencia={d['diff']} | criterio=foto_inicio -> {st}")
        print(f"¿Todos los saldos de testingadmin cumplen la referencia?: {'SÍ' if saldos_ref_ok else 'NO'}")
        if not saldos_ref_ok:
            print(f"ALERTA: Se detectaron {len(desvios_ref)} billeteras con saldos alterados:")
            for desv in desvios_ref:
                u_label = _fmt_email(desv["email"])
                print(f"  - {u_label} ({desv['billetera']} {desv['moneda']}): antes={desv['referencia']}, después={desv['actual']}, diferencia={desv['diff']}")
        print(f"Actividad en otras cuentas: {movs_otros_nuevos} movimientos nuevos, {billeteras_ajenas_cambiadas} billeteras con saldo distinto")
        print(f"Guarda graph.facebook.com: {disparos_guarda} disparos")

        print(f"\n=== VERIFICACION DE RECONCILIACION ({total_billeteras} BILLETERAS) ===")
        for d in detalles_rec:
            u_label = _fmt_email(d["email"])
            if d["ok"]:
                st = f"OK (baseline {d['esperado_diff']:+.2f})" if d["esperado_diff"] != Decimal("0.00") else "OK"
            else:
                st = f"DESVIO_NO_ESPERADO (diff={d['diferencia']:+.2f}, esperado={d['esperado_diff']:+.2f})"
                print(f"  {u_label} | {d['billetera']}: guardado={d['guardado']} | calc={d['calculado']} | diff={d['diferencia']} -> {st}")
        print(f"¿Reconciliación de todas las billeteras dentro del baseline?: {'SÍ' if rec_ok else 'NO'}")
        if not rec_ok:
            print(f"ALERTA: Se detectaron {len(discrepancias)} billeteras con desviaciones fuera del baseline:")
            for disc in discrepancias:
                u_label = _fmt_email(disc["email"])
                print(f"  - {u_label} ({disc['billetera']}): guardado={disc['guardado']}, calculado={disc['calculado']}, diff={disc['diferencia']}, esperado={disc['esperado_diff']}")
    else:
        # Modo compacto (menos de 30 líneas en verde)
        print("\n=== RESUMEN DE EJECUCION ===")
        print(f"Total: {total} | Aprobados: {aprobados} | Omitidos: {omitidos} | Fallidos: {fallidos} | Tiempo: {dur_total:.2f}s")
        print(f"Llamadas IA: {_gestor_actual.llamadas_grabadas} grabadas, {_gestor_actual.llamadas_reales} reales")
        print(f"Rollback y conteos: {'OK (cero residuo)' if sin_residuos else 'FALLO'}")
        if saldos_ref_ok:
            print(f"Saldos {total_billeteras} billeteras: OK (testingadmin contra referencia)")
        else:
            print(f"Saldos {total_billeteras} billeteras: DESVIO ({len(desvios_ref)} billeteras)")
            for desv in desvios_ref:
                u_label = _fmt_email(desv["email"])
                print(f"  - {u_label} ({desv['billetera']} {desv['moneda']}): antes={desv['referencia']}, después={desv['actual']}, diferencia={desv['diff']}")
        print(f"Actividad en otras cuentas: {movs_otros_nuevos} movimientos nuevos, {billeteras_ajenas_cambiadas} billeteras con saldo distinto")
        print(f"Guarda graph.facebook.com: {'OK (0 disparos)' if disparos_guarda == 0 else f'DISPARADA ({disparos_guarda})'}")
        if rec_ok:
            print(f"Reconciliación {total_billeteras} billeteras: OK (todas dentro del baseline)")
        else:
            print(f"Reconciliación {total_billeteras} billeteras: DESVIO ({len(discrepancias)} fuera de baseline)")
            for disc in discrepancias:
                u_label = _fmt_email(disc["email"])
                print(f"  - {u_label} ({disc['billetera']}): guardado={disc['guardado']}, calc={disc['calculado']}, diff={disc['diferencia']}")

    return total, aprobados, omitidos, fallidos, detalles_fallidos


def correr_suite_completa(verbose: bool = False, ia_real: bool = False, regrabar: bool = False, forzar_grabadas: bool = False, escenario: str | None = None, volcar_salidas: str | None = None):
    print("=== INICIANDO SUITE CONSOLIDADA DE REGRESION DE WHATSAPP ===")
    modo_str = "IA Real" if ia_real else ("Regrabar" if regrabar else "Grabadas (replay)")
    salida_str = "Detallada" if verbose else "Compacta"
    filtro_str = f" | Escenario: {escenario}" if escenario else ""
    volcar_str = f" | Volcar salidas: {volcar_salidas}" if volcar_salidas else ""
    print(f"Modo IA: {modo_str} | Salida: {salida_str}{filtro_str}{volcar_str} | Usuario: {USUARIO_PRUEBAS_EMAIL}")

    return _ejecutar_suite(verbose=verbose, ia_real=ia_real, regrabar=regrabar, forzar_grabadas=forzar_grabadas, solo_escenario=escenario, volcar_salidas=volcar_salidas)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Suite consolidada de regresión de WhatsApp")
    parser.add_argument("-v", "--verbose", action="store_true", help="Salida detallada escenario por escenario y tablas completas")
    parser.add_argument("--ia-real", "--live", action="store_true", help="Ejecutar todas las llamadas contra OpenAI real")
    parser.add_argument("--regrabar", "--record", action="store_true", help="Regrabar todas las llamadas contra OpenAI real y sobrescribir archivos")
    parser.add_argument("--forzar-grabadas", action="store_true", help="Forzar uso de grabaciones incluso en escenarios de modelo P7.1-P7.7 (modo offline)")
    parser.add_argument("--escenario", type=str, default=None, help="Ejecutar solo el escenario especificado por ID (ej: P6.5)")
    parser.add_argument("--volcar-salidas", type=str, default=None, help="Ruta del archivo JSON donde volcar la foto de salidas de cada escenario")
    args = parser.parse_args()

    total, aprobados, omitidos, fallidos, _ = correr_suite_completa(
        verbose=args.verbose,
        ia_real=args.ia_real,
        regrabar=args.regrabar,
        forzar_grabadas=args.forzar_grabadas,
        escenario=args.escenario,
        volcar_salidas=args.volcar_salidas,
    )
    if fallidos > 0:
        sys.exit(1)
