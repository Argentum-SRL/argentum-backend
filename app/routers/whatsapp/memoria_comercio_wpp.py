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
from app.models.usuario import Usuario
from app.services import memoria_comercio_service, whatsapp_service
from app.services.evento_service import emitir_evento_actualizacion


def preguntar_memoria_tras_correccion(
    usuario: Usuario,
    db: Session,
    from_number: str,
    tx: Transaccion | None,
    cambios: dict | None,
) -> None:
    """
    Pregunta al usuario si desea recordar la categorización para futuros movimientos
    después de haber confirmado una corrección de categoría.
    Solo actúa si:
    - cambios trae categoria_id,
    - el tipo de tx es egreso o ingreso,
    - clave_comercio(tx.descripcion) no es None,
    - la memoria de esa clave no existe o tiene otra categoría o subcategoría.
    """
    if not tx or not cambios:
        return

    categoria_id_raw = cambios.get("categoria_id")
    if not categoria_id_raw:
        return

    tipo_str = tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo).lower()
    if tipo_str not in ("egreso", "ingreso"):
        return

    clave = memoria_comercio_service.clave_comercio(tx.descripcion)
    if not clave:
        return

    nuevo_cat_id = UUID(str(categoria_id_raw))
    nuevo_subcat_id = UUID(str(cambios["subcategoria_id"])) if cambios.get("subcategoria_id") else None

    # Verificar si la memoria ya existe con la misma categorización
    memoria_existente = memoria_comercio_service.buscar(db, usuario.id, tx.descripcion, tipo_str)
    if memoria_existente is not None:
        if (
            memoria_existente.categoria_id == nuevo_cat_id
            and memoria_existente.subcategoria_id == nuevo_subcat_id
        ):
            return

    nombre = cambios.get("categoria_nombre")
    if not nombre:
        cat_db = db.get(Categoria, nuevo_cat_id)
        sub_db = db.get(Subcategoria, nuevo_subcat_id) if nuevo_subcat_id else None
        nombre = sub_db.nombre if sub_db else (cat_db.nombre if cat_db else "Otros")

    msg_pregunta = f'¿Siempre que diga "{clave}" lo pongo en {nombre}? Respondé sí o no.'
    whatsapp_service.enviar_whatsapp(from_number, msg_pregunta)

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
        whatsapp_service.enviar_whatsapp(from_number, "No se encontró la categoría para recordar.")
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
        detail = e.detail if isinstance(e.detail, str) else "No se pudo guardar la memoria del comercio."
        whatsapp_service.enviar_whatsapp(from_number, detail)
        conversacion.accion_ejecutada = "error_guardar"
        db.commit()
        return
    except Exception:
        whatsapp_service.enviar_whatsapp(from_number, "No se pudo guardar la memoria del comercio.")
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

        msg = (
            f'Listo, lo voy a recordar. Tenés {n} movimiento(s) anterior(es) de "{clave}" '
            f'en otra categoría. ¿Los paso también a {nombre}? Respondé sí o no.'
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
        whatsapp_service.enviar_whatsapp(from_number, "No se encontró la regla de memoria.")
        conversacion.accion_ejecutada = "error_memoria_no_encontrada"
        db.commit()
        return

    res = memoria_comercio_service.aplicar_a_anteriores(db, usuario.id, memoria, tx_ids, commit=True)
    actualizadas = res.get("actualizadas", 0)
    conversacion.accion_ejecutada = f"memoria_aplicada:{memoria.id}"
    msg = f"Listo: {actualizadas} movimiento(s) actualizado(s)."
    whatsapp_service.enviar_whatsapp(from_number, msg)
    emitir_evento_actualizacion(db, usuario.id, "transacciones")
    db.commit()
