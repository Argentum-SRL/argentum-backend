"""
app/routers/whatsapp/memoria_comercio_wpp.py — Flujo de memoria de categorización por comercio en WhatsApp.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.categoria import Categoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.memoria_comercio import MemoriaComercio
from app.models.subcategoria import Subcategoria
from app.models.transaccion import TipoTransaccion, Transaccion
from app.core.reglas_texto_wpp import ejemplo_wpp
from app.models.usuario import Usuario
from app.services import memoria_comercio_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion


def preguntar_memoria_tras_correccion(
    usuario: Usuario,
    db: Session,
    from_number: str,
    tx: Transaccion | None,
    cambios: dict | None,
    prefijo_confirmacion: str | None = None,
    solo_categoria: bool = False,
) -> bool:
    """
    Pregunta al usuario si desea recordar la categorización para futuros movimientos
    después de haber confirmado una corrección de categoría.
    Solo actúa si:
    - cambios trae categoria_id,
    - el tipo de tx es egreso o ingreso,
    - clave_comercio(tx.descripcion) no es None,
    - la memoria de esa clave no existe o tiene otra categoría o subcategoría.
    Devuelve True si envió la pregunta (junto con el prefijo si corresponde).
    """
    if not tx or not cambios:
        return False

    categoria_id_raw = cambios.get("categoria_id")
    if not categoria_id_raw:
        return False

    tipo_str = tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo).lower()
    if tipo_str not in ("egreso", "ingreso"):
        return False

    clave = memoria_comercio_service.clave_comercio(tx.descripcion)
    if not clave:
        return False

    nuevo_cat_id = UUID(str(categoria_id_raw))
    nuevo_subcat_id = UUID(str(cambios["subcategoria_id"])) if cambios.get("subcategoria_id") else None

    # Verificar si la memoria ya existe con la misma categorización
    memoria_existente = memoria_comercio_service.buscar(db, usuario.id, tx.descripcion, tipo_str)
    if memoria_existente is not None:
        if (
            memoria_existente.categoria_id == nuevo_cat_id
            and memoria_existente.subcategoria_id == nuevo_subcat_id
        ):
            return False

    nombre = cambios.get("categoria_nombre")
    if not nombre:
        cat_db = db.get(Categoria, nuevo_cat_id)
        sub_db = db.get(Subcategoria, nuevo_subcat_id) if nuevo_subcat_id else None
        nombre = sub_db.nombre if sub_db else (cat_db.nombre if cat_db else "Otros")

    msg_pregunta = f"¿Siempre que digas {ejemplo_wpp(clave)} lo pongo ahí?"
    if solo_categoria:
        msg_envio = f"Listo, lo pasé a {nombre}.\n{msg_pregunta}"
    elif prefijo_confirmacion:
        msg_envio = f"{prefijo_confirmacion}\n{msg_pregunta}"
    else:
        msg_envio = msg_pregunta
    whatsapp_service.enviar_whatsapp(from_number, msg_envio)

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=None,
        mensaje_usuario="",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_pregunta,
        intent_detectado="memoria_comercio",
        entidades={
            "descripcion": tx.descripcion,
            "tipo": tipo_str,
            "categoria_id": str(nuevo_cat_id),
            "subcategoria_id": str(nuevo_subcat_id) if nuevo_subcat_id else None,
            "clave": clave,
            "categoria_nombre": nombre,
        },
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    return True


def confirmar_memoria(
    usuario: Usuario,
    db: Session,
    from_number: str,
    conversacion: ConversacionWpp,
) -> None:
    """
    Confirma y guarda la memoria de un comercio a partir de una propuesta previa.
    Si hay movimientos anteriores con categoría distinta, pregunta si desea actualizarlos.
    """
    entidades = conversacion.entidades or {}
    descripcion = entidades.get("descripcion") or entidades.get("clave", "")
    tipo = entidades.get("tipo", "egreso")
    cat_id_raw = entidades.get("categoria_id")
    if not cat_id_raw:
        whatsapp_service.enviar_whatsapp(from_number, "No encontré la categoría para recordar.")
        conversacion.accion_ejecutada = "error_sin_categoria"
        db.commit()
        return

    cat_id = UUID(str(cat_id_raw))
    subcat_id_raw = entidades.get("subcategoria_id")
    subcat_id = UUID(str(subcat_id_raw)) if subcat_id_raw else None

    try:
        memoria = memoria_comercio_service.guardar(
            db=db,
            usuario_id=usuario.id,
            descripcion=descripcion,
            tipo=tipo,
            categoria_id=cat_id,
            subcategoria_id=subcat_id,
            commit=True,
        )
    except HTTPException as e:
        msg_err = "No pude guardar eso. Probá de nuevo en un rato."
        detail = e.detail if isinstance(e.detail, str) else msg_err
        whatsapp_service.enviar_whatsapp(from_number, detail)
        conversacion.accion_ejecutada = "error_guardar"
        db.commit()
        return
    except Exception:
        msg_err = "No pude guardar eso. Probá de nuevo en un rato."
        whatsapp_service.enviar_whatsapp(from_number, msg_err)
        conversacion.accion_ejecutada = "error_guardar"
        db.commit()
        return

    conversacion.accion_ejecutada = f"memoria:{memoria.id}"

    anteriores = memoria_comercio_service.anteriores_distintos(db, usuario.id, memoria)
    if not anteriores:
        msg = "Listo, lo voy a recordar."
        whatsapp_service.enviar_whatsapp(from_number, msg)
        db.commit()
    else:
        n = len(anteriores)
        clave = entidades.get("clave") or memoria.clave
        nombre = entidades.get("categoria_nombre")
        if not nombre:
            cat_db = db.get(Categoria, cat_id)
            sub_db = db.get(Subcategoria, subcat_id) if subcat_id else None
            nombre = sub_db.nombre if sub_db else (cat_db.nombre if cat_db else "Otros")

        if n == 1:
            que = "el gasto anterior" if tipo == "egreso" else "el ingreso anterior"
        else:
            que = (
                f"los {n} gastos anteriores"
                if tipo == "egreso"
                else f"los {n} ingresos anteriores"
            )
        msg = (
            "Listo, lo voy a recordar.\n"
            f"¿Paso también {que} de {ejemplo_wpp(clave)} a {nombre}?"
        )
        whatsapp_service.enviar_whatsapp(from_number, msg)

        nueva_conv = ConversacionWpp(
            usuario_id=usuario.id,
            wamid=None,
            mensaje_usuario="",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            transcripcion=None,
            mensaje_bot=msg,
            intent_detectado="memoria_anteriores",
            entidades={
                "memoria_id": str(memoria.id),
                "transaccion_ids": [str(t.id) for t in anteriores[:200]],
                "clave": clave,
                "categoria_nombre": nombre,
            },
            accion_ejecutada=None,
            confianza=Decimal("1.000"),
            slot_filling_activo=False,
            slot_filling_estado=None,
        )
        db.add(nueva_conv)
        db.commit()


def confirmar_anteriores(
    usuario: Usuario,
    db: Session,
    from_number: str,
    conversacion: ConversacionWpp,
) -> None:
    """
    Aplica la memoria del comercio a las transacciones anteriores registradas en la propuesta.
    """
    entidades = conversacion.entidades or {}
    memoria_id_str = entidades.get("memoria_id")
    tx_ids_raw = entidades.get("transaccion_ids") or []
    tx_ids = [UUID(str(tid)) for tid in tx_ids_raw]

    memoria = db.get(MemoriaComercio, UUID(str(memoria_id_str))) if memoria_id_str else None
    if not memoria:
        whatsapp_service.enviar_whatsapp(from_number, "No encontré esa regla para aplicarla.")
        conversacion.accion_ejecutada = "error_memoria_no_encontrada"
        db.commit()
        return

    res = memoria_comercio_service.aplicar_a_anteriores(db, usuario.id, memoria, tx_ids, commit=True)
    actualizadas = res.get("actualizadas", 0)
    conversacion.accion_ejecutada = f"memoria_aplicada:{memoria.id}"
    nombre_cat = entidades.get("categoria_nombre")
    if not nombre_cat:
        c_db = db.get(Categoria, memoria.categoria_id) if memoria.categoria_id else None
        s_db = db.get(Subcategoria, memoria.subcategoria_id) if memoria.subcategoria_id else None
        nombre_cat = s_db.nombre if s_db else (c_db.nombre if c_db else "esa categoría")
    tipo_m = entidades.get("tipo") or (
        memoria.tipo.value if hasattr(memoria.tipo, "value") else str(memoria.tipo)
    )
    if actualizadas == 1:
        unidad = "gasto" if tipo_m == "egreso" else "ingreso"
    else:
        unidad = "gastos" if tipo_m == "egreso" else "ingresos"
    msg = f"Listo, pasé {actualizadas} {unidad} a {nombre_cat}."
    whatsapp_service.enviar_whatsapp(from_number, msg)
    emitir_evento_actualizacion(db, usuario.id, "transacciones")
    db.commit()
