"""
Handlers de flujo determinístico para WhatsApp IA (Fase 2).
Cada handler encapsula la detección, procesamiento, persistencia en ConversacionWpp y envío de respuesta.
"""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.usuario import Moneda, Usuario
from app.routers.whatsapp.db_lookups import _buscar_slot_filling_activo
from app.routers.whatsapp.detectors import _es_saludo
from app.routers.whatsapp.parsers import _nombre_corto_categoria
from app.services import whatsapp_service
from app.utils.formato import formatear_monto


def manejar_saludo(
    mensaje_texto: str,
    usuario: Usuario,
    db: Session,
    from_number: str,
    wamid: str | None = None,
) -> bool:
    """
    Maneja el intent determinístico de saludo rioplatense ('hola', 'buenas', etc.).
    Si el usuario tenía una operación a medias (slot filling), la describe y la cancela/desactiva.
    Si no aplica, retorna False. Si aplica, persiste ConversacionWpp, envía la respuesta y retorna True.
    """
    if not _es_saludo(mensaje_texto):
        return False

    conv_activa_saludo = _buscar_slot_filling_activo(usuario.id, db)
    msg_saludo = ""
    if conv_activa_saludo and conv_activa_saludo.slot_filling_estado:
        est_saludo = conv_activa_saludo.slot_filling_estado
        monto_saludo = est_saludo.get("monto")
        cat_saludo = est_saludo.get("categoria")
        mon_saludo = est_saludo.get("moneda", "ARS")
        mon_enum = Moneda.USD if mon_saludo == "USD" else Moneda.ARS
        cat_disp = _nombre_corto_categoria(cat_saludo) if cat_saludo else ""
        if monto_saludo is not None:
            monto_fmt = formatear_monto(float(monto_saludo), mon_enum)
            if cat_disp:
                linea_pend = f"Tenías una operación a medias (anotar {monto_fmt} en {cat_disp}). Podés completarla o empezar de nuevo."
            else:
                linea_pend = f"Tenías una operación a medias de {monto_fmt}. Podés completarla o empezar de nuevo."
        else:
            linea_pend = "Tenías una operación a medias. Podés completarla o empezar de nuevo."
        msg_saludo = f"Hola. {linea_pend}\nTambién podés registrar otro gasto, ingreso o consultar tus saldos."
        # Desactivar para que el saludo no arrastre ni reactive nada
        conv_activa_saludo.slot_filling_activo = False
        conv_activa_saludo.accion_ejecutada = "interrumpida_por_saludo"
        db.flush()
    else:
        msg_saludo = "Hola. Podés registrar gastos, ingresos o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco'."

    nueva_conv = ConversacionWpp(
        usuario_id=usuario.id,
        wamid=wamid,
        mensaje_usuario=mensaje_texto,
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        transcripcion=None,
        mensaje_bot=msg_saludo,
        intent_detectado="saludo",
        entidades={},
        accion_ejecutada=None,
        confianza=Decimal("1.000"),
        slot_filling_activo=False,
        slot_filling_estado=None,
    )
    db.add(nueva_conv)
    db.commit()
    whatsapp_service.enviar_whatsapp(from_number, msg_saludo)
    return True
