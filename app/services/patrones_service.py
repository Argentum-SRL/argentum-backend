"""
app/services/patrones_service.py — Servicio del módulo 'Lo que se repite' de Argentum.
Clasifica patrones de egresos e ingresos habituales y gestiona las decisiones del usuario.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.decision_patron import DecisionPatron
from app.models.usuario import Usuario
from app.schemas.patrones import (
    ItemIngresoRead,
    ItemRepetidoRead,
    PatronesResumenResponse,
)
from app.services.datos_motor_service import cargar_datos_motor
from app.services.ingreso_habitual_service import obtener_ingreso_habitual
from app.utils.fecha import hoy_argentina
from app.utils.patrones import (
    MESES_VENTANA_FRECUENTE,
    clasificar_cajas,
)
from app.utils.texto import normalizar_texto


def _normalizar_moneda_str(moneda: Any) -> str:
    m = getattr(moneda, "value", moneda)
    m_str = str(m)
    if m_str.startswith("Moneda."):
        m_str = m_str.split(".", 1)[1]
    return m_str


def armar_lo_que_se_repite(
    db: Session,
    usuario: Usuario,
    hoy: Optional[date] = None,
) -> PatronesResumenResponse:
    """
    Construye las cuatro cajas de gastos e ingresos repetidos ('Lo que se repite'):
    ingresos habituales, fijos, costumbre y día a día, aplicando las decisiones del usuario.
    """
    ref_hoy = hoy or hoy_argentina()

    # a. Cargar datos del motor y decisiones del usuario
    motor_data = cargar_datos_motor(db, usuario, fecha_referencia=ref_hoy)
    decisiones = db.execute(
        select(DecisionPatron).where(DecisionPatron.usuario_id == usuario.id)
    ).scalars().all()
    decisiones_dict: Dict[str, DecisionPatron] = {d.clave_item: d for d in decisiones}

    # b. Extraer fijos descartados para clasificar_cajas
    fijos_descartados = []
    for d in decisiones:
        if d.decision == "descartado" and d.clave_item.startswith("fijo|"):
            partes = d.clave_item.split("|", 2)
            if len(partes) == 3:
                fijos_descartados.append((partes[2], _normalizar_moneda_str(partes[1])))

    res_cajas = clasificar_cajas(
        motor_data["txs"],
        motor_data["ipc"],
        ref_hoy,
        ctx=motor_data["ctx"],
        fijos_descartados=fijos_descartados,
    )

    tx_map = {tx.id: tx for tx in motor_data["txs"]}

    cajas_gastos: Dict[str, List[ItemRepetidoRead]] = {
        "fijo": [],
        "costumbre": [],
        "dia_a_dia": [],
    }

    # c.1 Ítems de gastos fijos detectados
    for f in res_cajas.fijos:
        moneda_str = _normalizar_moneda_str(f.moneda)
        clave_item = f"fijo|{moneda_str}|{f.clave}"

        # Obtener rubro del último movimiento del patrón
        txs_patron = [tx_map[tid] for tid in f.transacciones_ids if tid in tx_map]
        txs_patron.sort(key=lambda x: (x.fecha, str(x.id)))
        rubro = None
        if txs_patron:
            ult_tx = txs_patron[-1]
            sub_nom = getattr(getattr(ult_tx, "subcategoria", None), "nombre", None)
            cat_nom = getattr(getattr(ult_tx, "categoria", None), "nombre", None)
            rubro = sub_nom or cat_nom

        monto_tipico = Decimal(str(f.monto_mediano_deflactado))
        div = 1 if f.frecuencia == "mensual" else (2 if f.frecuencia == "bimestral" else 12)
        monto_mensual = (monto_tipico / Decimal(div)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        ultimo_monto = Decimal(str(f.ultimo_monto)) if f.ultimo_monto is not None else None

        dec = decisiones_dict.get(clave_item)
        if dec:
            if dec.decision == "confirmado":
                estado = "confirmado"
                caja = "fijo"
            elif dec.decision == "movido":
                estado = "movido"
                caja = dec.caja_destino or "fijo"
            elif dec.decision == "descartado":
                continue
            else:
                estado = "sugerido"
                caja = "fijo"
        else:
            estado = "sugerido"
            caja = "fijo"

        cuenta_en_numeros = (estado in ("confirmado", "movido")) or (f.fuerza == "fuerte")

        item = ItemRepetidoRead(
            clave_item=clave_item,
            nombre=f.descripcion,
            rubro=rubro,
            moneda=moneda_str,
            caja_detectada="fijo",
            caja=caja,
            estado=estado,
            frecuencia=f.frecuencia,
            dia_tipico=f.dia_tipico,
            proxima_fecha=f.proxima_fecha,
            fuerza=f.fuerza,
            ocurrencias=f.ocurrencias,
            monto_tipico=monto_tipico,
            ultimo_monto=ultimo_monto,
            monto_mensual=monto_mensual,
            cuenta_en_numeros=cuenta_en_numeros,
            transacciones_ids=[UUID(str(tid)) for tid in f.transacciones_ids],
        )
        if caja in cajas_gastos:
            cajas_gastos[caja].append(item)

    # c.2 Ítems de gastos: Costumbre y Día a día
    for grupo_lista, caja_orig in [
        (res_cajas.costumbre, "costumbre"),
        (res_cajas.dia_a_dia, "dia_a_dia"),
    ]:
        for g in grupo_lista:
            moneda_str = _normalizar_moneda_str(g.moneda)
            clave_item = f"rubro|{moneda_str}|{g.clave}"

            monto_tipico = Decimal(str(g.monto_mensual_mediano)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            monto_mensual = monto_tipico

            dec = decisiones_dict.get(clave_item)
            if dec:
                if dec.decision == "confirmado":
                    estado = "confirmado"
                    caja = caja_orig
                elif dec.decision == "movido":
                    estado = "movido"
                    caja = dec.caja_destino or caja_orig
                elif dec.decision == "descartado":
                    continue
                else:
                    estado = "sugerido"
                    caja = caja_orig
            else:
                estado = "sugerido"
                caja = caja_orig

            cuenta_en_numeros = True

            item = ItemRepetidoRead(
                clave_item=clave_item,
                nombre=g.nombre,
                rubro=g.nombre,
                moneda=moneda_str,
                caja_detectada=caja_orig,
                caja=caja,
                estado=estado,
                frecuencia=None,
                dia_tipico=None,
                proxima_fecha=None,
                fuerza=None,
                ocurrencias=g.ocurrencias,
                monto_tipico=monto_tipico,
                ultimo_monto=None,
                monto_mensual=monto_mensual,
                cuenta_en_numeros=cuenta_en_numeros,
                transacciones_ids=[UUID(str(tid)) for tid in g.transacciones_ids],
            )
            if caja in cajas_gastos:
                cajas_gastos[caja].append(item)

    # d. Ítems de ingresos
    ingresos_items: List[ItemIngresoRead] = []
    res_ingresos = obtener_ingreso_habitual(
        db,
        usuario,
        hoy=ref_hoy,
        ctx=motor_data["ctx"],
        txs_previa=motor_data["txs"],
    )

    dict_ingresos: Dict[str, Any] = (
        res_ingresos if isinstance(res_ingresos, dict) else {getattr(res_ingresos, "moneda", "ARS"): res_ingresos}
    )

    for _, res_hab in dict_ingresos.items():
        if getattr(res_hab, "tipo", None) == "sin_datos":
            continue

        moneda_hab = getattr(res_hab, "moneda", "ARS")
        moneda_str = getattr(moneda_hab, "value", moneda_hab) if not isinstance(moneda_hab, str) else moneda_hab
        moneda_str = str(moneda_str).upper()

        fuentes = getattr(res_hab, "fuentes", [])
        if fuentes:
            for f in fuentes:
                identificador = None
                if getattr(f, "clave", None):
                    identificador = str(f.clave)
                elif getattr(f, "descripcion", None):
                    identificador = normalizar_texto(str(f.descripcion))
                elif getattr(f, "subcategoria_id", None):
                    identificador = str(f.subcategoria_id)
                elif getattr(f, "subcategoria_nombre", None):
                    identificador = normalizar_texto(str(f.subcategoria_nombre))
                elif getattr(f, "categoria_nombre", None):
                    identificador = normalizar_texto(str(f.categoria_nombre))

                clave_item = f"ingreso|{moneda_str}|{identificador}" if identificador else f"ingreso|{moneda_str}|habitual"
                nombre = getattr(f, "subcategoria_nombre", None) or getattr(f, "categoria_nombre", None) or f"Ingreso {moneda_str}"
                tipo = getattr(f, "tipo", "regular")
                monto_mensual = Decimal(str(getattr(f, "monto", "0.00"))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                dec = decisiones_dict.get(clave_item)
                if dec:
                    if dec.decision == "confirmado":
                        estado = "confirmado"
                    elif dec.decision == "descartado":
                        continue
                    else:
                        estado = "sugerido"
                else:
                    estado = "sugerido"

                cuenta_en_numeros = True
                editable = bool(clave_item)

                ingresos_items.append(
                    ItemIngresoRead(
                        clave_item=clave_item,
                        nombre=nombre,
                        tipo=tipo,
                        moneda=moneda_str,
                        monto_mensual=monto_mensual,
                        estado=estado,
                        cuenta_en_numeros=cuenta_en_numeros,
                        editable=editable,
                    )
                )
        else:
            # Sin fuentes desglosadas pero con monto de ingreso habitual
            monto_val = getattr(res_hab, "monto", None)
            if monto_val is not None:
                clave_item = f"ingreso|{moneda_str}|habitual"
                nombre = f"Ingreso habitual {moneda_str}"
                tipo = getattr(res_hab, "tipo", "regular")
                monto_mensual = Decimal(str(monto_val)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                dec = decisiones_dict.get(clave_item)
                if dec:
                    if dec.decision == "confirmado":
                        estado = "confirmado"
                    elif dec.decision == "descartado":
                        continue
                    else:
                        estado = "sugerido"
                else:
                    estado = "sugerido"

                ingresos_items.append(
                    ItemIngresoRead(
                        clave_item=clave_item,
                        nombre=nombre,
                        tipo=tipo,
                        moneda=moneda_str,
                        monto_mensual=monto_mensual,
                        estado=estado,
                        cuenta_en_numeros=True,
                        editable=True,
                    )
                )

    # g. Ordenar cada caja de mayor a menor monto_mensual
    ingresos_items.sort(key=lambda x: x.monto_mensual, reverse=True)
    cajas_gastos["fijo"].sort(key=lambda x: x.monto_mensual, reverse=True)
    cajas_gastos["costumbre"].sort(key=lambda x: x.monto_mensual, reverse=True)
    cajas_gastos["dia_a_dia"].sort(key=lambda x: x.monto_mensual, reverse=True)

    return PatronesResumenResponse(
        fecha_calculo=ref_hoy,
        meses_ventana=MESES_VENTANA_FRECUENTE,
        ingresos=ingresos_items,
        fijos=cajas_gastos["fijo"],
        costumbre=cajas_gastos["costumbre"],
        dia_a_dia=cajas_gastos["dia_a_dia"],
    )


def registrar_decision(
    db: Session,
    usuario: Usuario,
    clave_item: str,
    decision: str,
    caja_destino: Optional[str] = None,
    hoy: Optional[date] = None,
    commit: bool = True,
) -> PatronesResumenResponse:
    """
    Registra o actualiza la decisión del usuario sobre un ítem de patrones.
    Decisiones permitidas: 'confirmar', 'descartar', 'mover'.
    """
    ref_hoy = hoy or hoy_argentina()

    # Obtener estado actual
    resumen = armar_lo_que_se_repite(db, usuario, hoy=ref_hoy)
    item_encontrado: Any = None
    todos_items = resumen.fijos + resumen.costumbre + resumen.dia_a_dia + resumen.ingresos
    for it in todos_items:
        if it.clave_item == clave_item:
            item_encontrado = it
            break

    dec_existente = db.execute(
        select(DecisionPatron).where(
            DecisionPatron.usuario_id == usuario.id,
            DecisionPatron.clave_item == clave_item,
        )
    ).scalar_one_or_none()

    # 1. El ítem tiene que estar en la lista de hoy o tener una decisión guardada
    if not item_encontrado and not dec_existente:
        raise HTTPException(status_code=404, detail="No encontré ese ítem.")

    dec_clean = decision.strip().lower()

    # 2. Mover sin caja_destino: 400
    if dec_clean in ("mover", "movido") and not caja_destino:
        raise HTTPException(status_code=400, detail="Elegí a qué caja moverlo.")

    # 3. Mover un ingreso: 400
    if dec_clean in ("mover", "movido"):
        if clave_item.startswith("ingreso|") or (item_encontrado and isinstance(item_encontrado, ItemIngresoRead)):
            raise HTTPException(status_code=400, detail="Los ingresos no se pueden mover.")

    # 4. Mover a la caja en la que ya está: 400
    if dec_clean in ("mover", "movido"):
        caja_actual = None
        if item_encontrado and hasattr(item_encontrado, "caja"):
            caja_actual = item_encontrado.caja
        elif dec_existente and dec_existente.decision == "movido":
            caja_actual = dec_existente.caja_destino
        elif item_encontrado and hasattr(item_encontrado, "caja_detectada"):
            caja_actual = item_encontrado.caja_detectada

        if caja_actual == caja_destino:
            raise HTTPException(status_code=400, detail="Ya está en esa caja.")

    # 5. Mover a su caja_detectada: borra la decisión (vuelve a sugerido)
    if dec_clean in ("mover", "movido"):
        caja_detectada = getattr(item_encontrado, "caja_detectada", None)
        if caja_destino == caja_detectada:
            if dec_existente:
                db.delete(dec_existente)
                if commit:
                    db.commit()
                else:
                    db.flush()
            return armar_lo_que_se_repite(db, usuario, hoy=ref_hoy)

    # 6. Guardar o reemplazar la decisión
    if dec_clean in ("confirmar", "confirmado"):
        decision_val = "confirmado"
    elif dec_clean in ("descartar", "descartado"):
        decision_val = "descartado"
    elif dec_clean in ("mover", "movido"):
        decision_val = "movido"
    else:
        raise HTTPException(status_code=400, detail=f"Decisión inválida: {decision}")

    caja_destino_val = caja_destino if decision_val == "movido" else None

    if dec_existente:
        dec_existente.decision = decision_val
        dec_existente.caja_destino = caja_destino_val
    else:
        nueva_dec = DecisionPatron(
            usuario_id=usuario.id,
            clave_item=clave_item,
            decision=decision_val,
            caja_destino=caja_destino_val,
        )
        db.add(nueva_dec)

    if commit:
        db.commit()
    else:
        db.flush()

    return armar_lo_que_se_repite(db, usuario, hoy=ref_hoy)


def deshacer_decision(
    db: Session,
    usuario: Usuario,
    clave_item: str,
    hoy: Optional[date] = None,
    commit: bool = True,
) -> PatronesResumenResponse:
    """
    Deshace la decisión previa tomada sobre un ítem, eliminándola de la base de datos.
    """
    ref_hoy = hoy or hoy_argentina()

    dec_existente = db.execute(
        select(DecisionPatron).where(
            DecisionPatron.usuario_id == usuario.id,
            DecisionPatron.clave_item == clave_item,
        )
    ).scalar_one_or_none()

    if not dec_existente:
        raise HTTPException(status_code=404, detail="No hay nada para deshacer en ese ítem.")

    db.delete(dec_existente)
    if commit:
        db.commit()
    else:
        db.flush()

    return armar_lo_que_se_repite(db, usuario, hoy=ref_hoy)
