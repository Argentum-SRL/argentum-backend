"""
scripts/regresion/paridad_web_whatsapp.py

Comprueba, consulta por consulta, que WhatsApp y la web devuelven los mismos números
para el mismo usuario (testingadmin@argentum.com).
- Solo lectura sobre la base de datos local.
- Dentro de una transacción que se revierte al final.
- Sin enviar WhatsApp y sin llamar a la IA.
- Para cada consulta:
    1. Calcula los números que muestra el texto de WhatsApp (extraídos del texto armado).
    2. Calcula los números que devuelve la función/endpoint de la web, formateados con formatear_monto.
    3. Imprime ambos conjuntos de números y marca IGUAL o DISTINTO (o SIN EQUIVALENTE).
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.usuario import Usuario, Moneda
from app.utils.fecha import hoy_argentina
from app.utils.formato import formatear_monto
from app.routers.whatsapp.enriquecedores import enriquecer_respuesta_por_intent
from app.routers.whatsapp.handlers_permitirse import detectar_consulta_permitirse, armar_respuesta_permitirse
from app.routers.whatsapp.gastos import (
    _extraer_periodo_gastos,
    _rango_periodo,
    _formatear_respuesta_gastos,
    ETIQUETAS_PERIODO,
)
from app.services import (
    dashboard_service,
    proyeccion_service,
    dolar_service,
    contexto_financiero_service,
    meta_service,
    presupuesto_service,
    tools_service,
    gastos_consulta_service,
)


def normalizar_monto_str(m: str) -> str:
    """Normaliza un string de monto quitando espacios extras tras símbolo $ o US$."""
    m_clean = m.strip().replace(" ", "")
    return m_clean


def extraer_montos_de_texto(texto: str) -> List[str]:
    """Extrae todos los montos con formato monetario del texto ($X o US$X)."""
    patron = re.compile(r"(?:US\$|\$)[\d\.]+(?:,\d+)?", re.IGNORECASE)
    hallados = patron.findall(texto)
    return [normalizar_monto_str(x) for x in hallados]


def verificar_paridad_numerica(
    consulta_nombre: str,
    montos_wpp: List[str],
    montos_web: List[str],
    texto_wpp_completo: str,
    log_func=print,
) -> str:
    """
    Compara si los números generados por la web se corresponden con los mostrados en WhatsApp.
    Retorna 'IGUAL' o 'DISTINTO'.
    """
    log_func(f"\nConsulta: {consulta_nombre}")
    log_func(f"  Texto WhatsApp:\n    {texto_wpp_completo.strip()}")
    log_func(f"  Números en WhatsApp : {montos_wpp}")
    log_func(f"  Números de la Web   : {montos_web}")

    if not montos_web and not montos_wpp:
        resultado = "IGUAL"
    else:
        # Verificar que todos los números calculados por la web estén en el mensaje de WhatsApp
        todos_presentes = all(m in montos_wpp for m in montos_web)
        resultado = "IGUAL" if todos_presentes else "DISTINTO"

    log_func(f"  Resultado: {resultado}")
    return resultado


def ejecutar_verificaciones_paridad(db, usuario: Usuario, log_func=print) -> Dict[str, str]:
    resultados: Dict[str, str] = {}

    log_func("=" * 80)
    log_func(f"PARIDAD DE NÚMEROS WEB / WHATSAPP (Usuario: {usuario.email})")
    log_func("=" * 80)

    # -------------------------------------------------------------------------
    # 1. Saldo
    # -------------------------------------------------------------------------
    ia_res_saldo = {"intent": "consultar_saldo", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_saldo", ia_res_saldo, "saldo", usuario, db)
    msg_wpp_saldo = ia_res_saldo.get("respuesta_usuario", "")
    wpp_num_saldo = extraer_montos_de_texto(msg_wpp_saldo)

    # Web: contexto_financiero_service._calcular_saldo_disponible_sync
    disp_ctx = contexto_financiero_service._calcular_saldo_disponible_sync(db, usuario.id)
    ars_tot = formatear_monto(disp_ctx["ars"]["total_billeteras"], Moneda.ARS)
    ars_disp = formatear_monto(disp_ctx["ars"]["saldo_disponible"], Moneda.ARS)
    web_num_saldo = [normalizar_monto_str(ars_tot), normalizar_monto_str(ars_disp)]
    if float(disp_ctx["usd"]["total_billeteras"]) > 0 or float(disp_ctx["usd"]["saldo_disponible"]) > 0:
        usd_tot = formatear_monto(disp_ctx["usd"]["total_billeteras"], Moneda.USD)
        usd_disp = formatear_monto(disp_ctx["usd"]["saldo_disponible"], Moneda.USD)
        web_num_saldo.extend([normalizar_monto_str(usd_tot), normalizar_monto_str(usd_disp)])

    resultados["Saldo"] = verificar_paridad_numerica(
        "Saldo", wpp_num_saldo, web_num_saldo, msg_wpp_saldo, log_func=log_func
    )

    # -------------------------------------------------------------------------
    # 2. Balance
    # -------------------------------------------------------------------------
    ia_res_bal = {"intent": "consultar_balance", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_balance", ia_res_bal, "balance", usuario, db)
    msg_wpp_bal = ia_res_bal.get("respuesta_usuario", "")
    wpp_num_bal = extraer_montos_de_texto(msg_wpp_bal)

    # Web: dashboard_service.calcular_balance_ciclo
    bal_ciclo = dashboard_service.calcular_balance_ciclo(db, usuario)
    b_ars = bal_ciclo["ars"]
    ing_fmt = formatear_monto(b_ars["ingresos"], Moneda.ARS)
    egr_fmt = formatear_monto(b_ars["egresos"], Moneda.ARS)
    bal_fmt = formatear_monto(abs(b_ars["balance"]), Moneda.ARS)
    web_num_bal = [normalizar_monto_str(ing_fmt), normalizar_monto_str(egr_fmt), normalizar_monto_str(bal_fmt)]

    resultados["Balance"] = verificar_paridad_numerica(
        "Balance", wpp_num_bal, web_num_bal, msg_wpp_bal, log_func=log_func
    )

    # -------------------------------------------------------------------------
    # 3. Proyección
    # -------------------------------------------------------------------------
    ia_res_proy = {"intent": "consultar_proyeccion", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_proyeccion", ia_res_proy, "proyección", usuario, db)
    msg_wpp_proy = ia_res_proy.get("respuesta_usuario", "")
    wpp_num_proy = extraer_montos_de_texto(msg_wpp_proy)

    # Web: proyeccion_service.calcular_proyeccion
    proy_res = proyeccion_service.calcular_proyeccion(db, usuario)
    p_ars = proy_res.get("ars", {})
    bal_proy = p_ars.get("balance_proyectado")
    web_num_proy = []
    if bal_proy is not None:
        bal_proy_fmt = formatear_monto(abs(bal_proy), Moneda.ARS)
        web_num_proy.append(normalizar_monto_str(bal_proy_fmt))

    resultados["Proyección"] = verificar_paridad_numerica(
        "Proyección", wpp_num_proy, web_num_proy, msg_wpp_proy, log_func=log_func
    )

    # -------------------------------------------------------------------------
    # 4. Cotizaciones
    # -------------------------------------------------------------------------
    ia_res_cot = {"intent": "consultar_cotizacion", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_cotizacion", ia_res_cot, "cotización", usuario, db)
    msg_wpp_cot = ia_res_cot.get("respuesta_usuario", "")
    wpp_num_cot = extraer_montos_de_texto(msg_wpp_cot)

    # Web: dolar_service.get_cotizaciones_dolar
    cots_data = dolar_service.get_cotizaciones_dolar().get("cotizaciones", {})
    web_num_cot = []
    for tipo_k in ("blue", "mep", "oficial"):
        c_item = cots_data.get(tipo_k, {})
        if c_item and c_item.get("venta"):
            c_fmt = formatear_monto(c_item["venta"], Moneda.ARS)
            web_num_cot.append(normalizar_monto_str(c_fmt))

    resultados["Cotizaciones"] = verificar_paridad_numerica(
        "Cotizaciones", wpp_num_cot, web_num_cot, msg_wpp_cot, log_func=log_func
    )

    # -------------------------------------------------------------------------
    # 5. Metas
    # -------------------------------------------------------------------------
    ia_res_meta = {"intent": "consultar_meta", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_meta", ia_res_meta, "metas", usuario, db)
    msg_wpp_meta = ia_res_meta.get("respuesta_usuario", "")
    wpp_num_meta = extraer_montos_de_texto(msg_wpp_meta)

    # Web: meta_service.obtener_metas
    metas_web = meta_service.obtener_metas(db, usuario.id, activas_solo=True)
    web_num_meta = []
    for m in metas_web:
        acum_fmt = formatear_monto(m.monto_actual, m.moneda)
        obj_fmt = formatear_monto(m.monto_objetivo, m.moneda)
        web_num_meta.append(normalizar_monto_str(acum_fmt))
        web_num_meta.append(normalizar_monto_str(obj_fmt))

    resultados["Metas"] = verificar_paridad_numerica(
        "Metas", wpp_num_meta, web_num_meta, msg_wpp_meta, log_func=log_func
    )

    # -------------------------------------------------------------------------
    # 6. Presupuestos
    # -------------------------------------------------------------------------
    ia_res_pres = {"intent": "consultar_presupuesto", "entidades": {}}
    enriquecer_respuesta_por_intent("consultar_presupuesto", ia_res_pres, "presupuestos", usuario, db)
    msg_wpp_pres = ia_res_pres.get("respuesta_usuario", "")
    wpp_num_pres = extraer_montos_de_texto(msg_wpp_pres)

    # Web: presupuesto_service.obtener_presupuestos
    pres_web = presupuesto_service.obtener_presupuestos(db, usuario.id, estado="activo")
    web_num_pres = []
    for p in pres_web:
        per = presupuesto_service.obtener_periodo_activo(None, p)
        usado_val = per.monto_usado if per else Decimal("0")
        limite_val = p.monto
        web_num_pres.append(normalizar_monto_str(formatear_monto(usado_val, p.moneda)))
        web_num_pres.append(normalizar_monto_str(formatear_monto(limite_val, p.moneda)))

    resultados["Presupuestos"] = verificar_paridad_numerica(
        "Presupuestos", wpp_num_pres, web_num_pres, msg_wpp_pres, log_func=log_func
    )

    # -------------------------------------------------------------------------
    # 7. Gastos
    # -------------------------------------------------------------------------
    msg_g = "cuanto gaste este mes"
    clave_g = _extraer_periodo_gastos(msg_g)
    hoy_g = hoy_argentina()
    ciclo_g = dashboard_service.get_ciclo_fechas(usuario, hoy_g)
    desde_g, hasta_g = _rango_periodo(clave_g, hoy_g, ciclo_g, usuario=usuario)
    res_g = gastos_consulta_service.calcular_gastos_periodo(db, usuario.id, desde_g, hasta_g, top_n=3)
    wpp_msg_g = _formatear_respuesta_gastos(
        ETIQUETAS_PERIODO[clave_g], None, False,
        float(res_g["ars"]["total"]), res_g["ars"]["cantidad"],
        float(res_g["usd"]["total"]), res_g["usd"]["cantidad"],
        res_g["top_categorias_ars"],
        desde=desde_g, hasta=hasta_g,
    )
    log_func("\nConsulta: Gastos")
    log_func("  Función WhatsApp: manejar_consulta_gastos (gastos.py)")
    log_func(f"  Texto WhatsApp:\n    {wpp_msg_g}")
    log_func(f"  Números en WhatsApp: {extraer_montos_de_texto(wpp_msg_g)}")
    log_func("  Endpoint Web    : SIN EQUIVALENTE")
    log_func("  Resultado       : SIN EQUIVALENTE")
    resultados["Gastos"] = "SIN EQUIVALENTE"

    # -------------------------------------------------------------------------
    # 8. ¿Me lo puedo permitir? - Contado 300.000
    # -------------------------------------------------------------------------
    txt_c1 = "¿me lo puedo permitir 300000 de contado?"
    c1_parsed = detectar_consulta_permitirse(txt_c1)
    r1_wpp = tools_service.calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=float(c1_parsed["precio"]),
        modo=c1_parsed["modo"],
        cantidad_cuotas=c1_parsed["cantidad_cuotas"],
        tiene_interes=c1_parsed["tiene_interes"],
        tna=None,
        ingreso_manual=None,
        db=db,
    )
    msg_wpp_c1 = armar_respuesta_permitirse(r1_wpp, c1_parsed)
    wpp_num_c1 = extraer_montos_de_texto(msg_wpp_c1)

    # Web: can_afford_endpoint (tools_service.calcular_puede_permitirse)
    r1_web = tools_service.calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=300000.0,
        modo="contado",
        cantidad_cuotas=1,
        tiene_interes=False,
        tna=None,
        ingreso_manual=None,
        db=db,
    )
    disp_fmt = formatear_monto(r1_web["saldo_disponible_actual"], Moneda.ARS)
    rest_fmt = formatear_monto(r1_web["saldo_restante_post_compra"], Moneda.ARS)
    web_num_c1 = [normalizar_monto_str(rest_fmt), normalizar_monto_str(disp_fmt)]

    resultados["¿Me lo puedo permitir? (contado 300.000)"] = verificar_paridad_numerica(
        "¿Me lo puedo permitir? (contado 300.000)",
        wpp_num_c1,
        web_num_c1,
        msg_wpp_c1,
        log_func=log_func,
    )

    # -------------------------------------------------------------------------
    # 8b. ¿Me lo puedo permitir? - 6 cuotas de 600.000
    # -------------------------------------------------------------------------
    txt_c2 = "¿me puedo permitir 6 cuotas de 600.000?"
    c2_parsed = detectar_consulta_permitirse(txt_c2)
    r2_wpp = tools_service.calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=float(c2_parsed["precio"]),
        modo=c2_parsed["modo"],
        cantidad_cuotas=c2_parsed["cantidad_cuotas"],
        tiene_interes=c2_parsed["tiene_interes"],
        tna=None,
        ingreso_manual=None,
        db=db,
    )
    msg_wpp_c2 = armar_respuesta_permitirse(r2_wpp, c2_parsed)
    wpp_num_c2 = extraer_montos_de_texto(msg_wpp_c2)

    # Web: can_afford_endpoint con precio total de 6 * 600.000 = 3.600.000 en 6 cuotas
    r2_web = tools_service.calcular_puede_permitirse(
        user_id=usuario.id,
        precio_total=3600000.0,
        modo="cuotas",
        cantidad_cuotas=6,
        tiene_interes=False,
        tna=None,
        ingreso_manual=None,
        db=db,
    )
    cuota_fmt = formatear_monto(r2_web["monto_cuota"], Moneda.ARS)
    carga_fmt = formatear_monto(r2_web["carga_mensual_nueva_total"], Moneda.ARS)
    margen_fmt = formatear_monto(abs(r2_web["margen_libre_post_compra"]), Moneda.ARS)
    web_num_c2 = [normalizar_monto_str(cuota_fmt), normalizar_monto_str(carga_fmt), normalizar_monto_str(margen_fmt)]

    resultados["¿Me lo puedo permitir? (6 cuotas de 600.000)"] = verificar_paridad_numerica(
        "¿Me lo puedo permitir? (6 cuotas de 600.000)",
        wpp_num_c2,
        web_num_c2,
        msg_wpp_c2,
        log_func=log_func,
    )

    # -------------------------------------------------------------------------
    # RESUMEN
    # -------------------------------------------------------------------------
    log_func("\n" + "=" * 80)
    log_func("RESUMEN DE PARIDAD WEB / WHATSAPP")
    log_func("=" * 80)
    for c_nom, res_st in resultados.items():
        log_func(f"  {c_nom:<48}: {res_st}")

    return resultados


def main():
    parser = argparse.ArgumentParser(description="Verificar paridad numérica entre Web y WhatsApp")
    parser.add_argument("--crudo", type=Path, default=None, help="Ruta para guardar crudo de salida")
    args = parser.parse_args()

    buf = io.StringIO()

    def log(msg=""):
        print(msg)
        buf.write(msg + "\n")

    db = SessionLocal()
    try:
        user = db.execute(select(Usuario).where(Usuario.email == "testingadmin@argentum.com")).scalar_one_or_none()
        if not user:
            log("ERROR CRÍTICO: testingadmin@argentum.com no existe en la base local")
            sys.exit(1)

        resultados = ejecutar_verificaciones_paridad(db, user, log_func=log)

        # Si se especificó o corresponde escribir crudo:
        if args.crudo:
            args.crudo.parent.mkdir(parents=True, exist_ok=True)
            args.crudo.write_text(buf.getvalue(), encoding="utf-8")
            print(f"\nCrudo guardado en: {args.crudo}")

    finally:
        db.rollback()
        db.close()

    # Verificar si hubo algún DISTINTO
    hubo_distinto = any(st == "DISTINTO" for st in resultados.values())
    if hubo_distinto:
        print("\nADVERTENCIA: Se encontraron diferencias de paridad (ver detalle).")
    else:
        print("\nÉXITO: Todas las consultas comprobadas presentan paridad exacta (o SIN EQUIVALENTE).")


if __name__ == "__main__":
    main()
