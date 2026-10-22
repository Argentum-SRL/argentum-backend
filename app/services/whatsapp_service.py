"""
app/services/whatsapp_service.py — Servicio de mensajería y verificación por WhatsApp con Meta Cloud API.
"""

import logging
import re
import secrets
import time
from dataclasses import dataclass

import httpx

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

from app.core.config import settings

logger = logging.getLogger(__name__)

_meta_http_client: httpx.Client | None = None


def get_meta_http_client() -> httpx.Client:
    global _meta_http_client
    if _meta_http_client is None:
        _meta_http_client = httpx.Client()
    return _meta_http_client


MAX_INTENTOS = 3


def _enmascarar_telefono(telefono: str | None) -> str:
    """Enmascara el número de teléfono mostrando solo los últimos 4 dígitos."""
    if not telefono:
        return "****"
    tel_clean = "".join(c for c in str(telefono) if c.isdigit())
    if len(tel_clean) >= 4:
        return f"***{tel_clean[-4:]}"
    return "****"


def _enmascarar_otp_en_mensaje(mensaje: str | None) -> str:
    """Enmascara códigos numéricos de verificación (ej. OTPs) en el cuerpo del mensaje."""
    if not mensaje:
        return ""
    return re.sub(r"\b\d{4,8}\b", "***", mensaje)


from datetime import datetime, timezone, timedelta
from uuid import uuid4
from sqlalchemy import select, update
from app.core.database import SessionLocal
from app.models.codigo_verificacion import CodigoVerificacion

CARACTERES_CODIGO_VINCULACION = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # Sin O, 0, I, 1, L (32 caracteres alfanuméricos)
EXPIRACION_VINCULACION_SEGUNDOS = 15 * 60  # 15 minutos


@dataclass
class EntradaCodigoVinculacion:
    usuario_id: str
    codigo: str
    expiracion: float
    creado_en: float


def generar_codigo_vinculacion(usuario_id: str | int) -> tuple[str, float]:
    """
    Genera un código de 6 caracteres alfanuméricos en mayúscula, sin caracteres ambiguos.
    Invalida cualquier código previo generado por el usuario.
    Persiste en la tabla codigos_verificacion de Postgres.
    Retorna (codigo, expiracion_timestamp).
    """
    uid_str = str(usuario_id)
    ahora = datetime.now(timezone.utc)
    expiracion = ahora + timedelta(seconds=EXPIRACION_VINCULACION_SEGUNDOS)

    with SessionLocal() as db:
        # Invalidar cualquier código previo activo del mismo usuario
        db.execute(
            update(CodigoVerificacion)
            .where(
                CodigoVerificacion.tipo == "vinculacion_whatsapp",
                CodigoVerificacion.identificador == uid_str,
                CodigoVerificacion.consumido == False,
            )
            .values(consumido=True, consumido_en=ahora)
        )

        # Generar código único de 6 caracteres con CSPRNG
        for _ in range(20):
            candidato = "".join(secrets.choice(CARACTERES_CODIGO_VINCULACION) for _ in range(6))
            existe = db.execute(
                select(CodigoVerificacion.id)
                .where(
                    CodigoVerificacion.tipo == "vinculacion_whatsapp",
                    CodigoVerificacion.codigo == candidato,
                    CodigoVerificacion.consumido == False,
                    CodigoVerificacion.expiracion > ahora,
                )
            ).first()
            if not existe:
                codigo = candidato
                break
        else:
            codigo = "".join(secrets.choice(CARACTERES_CODIGO_VINCULACION) for _ in range(6))

        nuevo = CodigoVerificacion(
            id=uuid4(),
            tipo="vinculacion_whatsapp",
            identificador=uid_str,
            codigo=codigo,
            expiracion=expiracion,
            intentos_fallidos=0,
            max_intentos=MAX_INTENTOS,
            creado_en=ahora,
            consumido=False,
        )
        db.add(nuevo)
        db.commit()

    return codigo, expiracion.timestamp()


def consumir_codigo_vinculacion(codigo: str) -> None:
    """Invalida inmediatamente el código consumido en Postgres para asegurar uso único."""
    cod = codigo.strip().upper()
    ahora = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(
            update(CodigoVerificacion)
            .where(
                CodigoVerificacion.tipo == "vinculacion_whatsapp",
                CodigoVerificacion.codigo == cod,
                CodigoVerificacion.consumido == False,
            )
            .values(consumido=True, consumido_en=ahora)
        )
        db.commit()


def buscar_codigo_vinculacion(
    mensaje_texto: str,
) -> tuple[str | None, EntradaCodigoVinculacion | None, bool]:
    """
    Busca de manera tolerante un código de vinculación en el mensaje de texto entrante consultando Postgres.
    Tolerancia:
    - Insensible a mayúsculas/minúsculas.
    - Ignora espacios, guiones y signos de puntuación alrededor o dentro del código.
    - Encuentra el código aunque el usuario haya editado el resto del mensaje.

    Retorna: (codigo_detectado, entrada_activa_o_None, es_vencido)
    """
    if not mensaje_texto:
        return None, None, False

    texto_upper = mensaje_texto.upper()
    ahora = datetime.now(timezone.utc)
    hace_dos_horas = ahora - timedelta(hours=2)

    with SessionLocal() as db:
        # Códigos no consumidos generados en las últimas 2 horas
        filas = db.execute(
            select(CodigoVerificacion)
            .where(
                CodigoVerificacion.tipo == "vinculacion_whatsapp",
                CodigoVerificacion.consumido == False,
                CodigoVerificacion.creado_en >= hace_dos_horas,
            )
        ).scalars().all()

        # 1. Coincidencia directa con códigos activos o recientemente vencidos
        for entrada in filas:
            exp_dt = (
                entrada.expiracion
                if entrada.expiracion.tzinfo is not None
                else entrada.expiracion.replace(tzinfo=timezone.utc)
            )
            patron = (
                r"(?<![A-Z0-9])"
                + r"[\s\-]*".join(re.escape(c) for c in entrada.codigo)
                + r"(?![A-Z0-9])"
            )
            if re.search(patron, texto_upper):
                if ahora <= exp_dt:
                    creado_dt = (
                        entrada.creado_en
                        if entrada.creado_en.tzinfo is not None
                        else entrada.creado_en.replace(tzinfo=timezone.utc)
                    )
                    ent = EntradaCodigoVinculacion(
                        usuario_id=entrada.identificador,
                        codigo=entrada.codigo,
                        expiracion=exp_dt.timestamp(),
                        creado_en=creado_dt.timestamp(),
                    )
                    return entrada.codigo, ent, False
                return entrada.codigo, None, True

        # 2. Detección por patrón explícito: Codigo / Código / Cod
        match_prefijo = re.search(
            r"(?:codigo|código|cod)\s*[:=]?\s*([A-Z0-9\s\-]{6,12})",
            texto_upper,
        )
        if match_prefijo:
            cand = re.sub(r"[^A-Z0-9]", "", match_prefijo.group(1))[:6]
            if len(cand) == 6:
                for entrada in filas:
                    if entrada.codigo == cand:
                        exp_dt = (
                            entrada.expiracion
                            if entrada.expiracion.tzinfo is not None
                            else entrada.expiracion.replace(tzinfo=timezone.utc)
                        )
                        if ahora <= exp_dt:
                            creado_dt = (
                                entrada.creado_en
                                if entrada.creado_en.tzinfo is not None
                                else entrada.creado_en.replace(tzinfo=timezone.utc)
                            )
                            ent = EntradaCodigoVinculacion(
                                usuario_id=entrada.identificador,
                                codigo=entrada.codigo,
                                expiracion=exp_dt.timestamp(),
                                creado_en=creado_dt.timestamp(),
                            )
                            return cand, ent, False
                        return cand, None, True
                return cand, None, True

    return None, None, False



def formatear_numero_whatsapp(telefono: str) -> str:
    """
    Formatea un número para WhatsApp Meta Cloud API (formato E.164 plano de solo dígitos, sin prefijo whatsapp:).
    - Remueve 'whatsapp:' si existe
    - Remueve espacios, guiones y caracteres no numéricos
    - Si empieza con '0' (formato local ej. 011...): convierte a '549' + número sin el 0
    - Si empieza con '15' (formato local ej. 15...): convierte a '549' + número
    """
    if not telefono:
        return ""

    tel = telefono.strip()
    if tel.startswith("whatsapp:"):
        tel = tel[9:].strip()

    if tel.startswith("+"):
        tel = tel[1:].strip()
    elif tel.startswith("0"):
        tel = f"549{tel[1:]}"
    elif tel.startswith("15"):
        tel = f"549{tel}"

    digitos = "".join(c for c in tel if c.isdigit())
    return digitos


@dataclass
class ResultadoMeta:
    ok: bool
    http_status: int | None = None
    wamid_meta: str | None = None
    codigo_error_meta: int | None = None
    motivo: str | None = None
    intentos: int = 1


def _post_meta(
    payload: dict,
    max_intentos: int = 3,
    timeout: httpx.Timeout | float | int | None = None,
) -> ResultadoMeta:
    """
    Único lugar con client.post a .../messages.
    Aplica la regla de reintentos de la decisión 3:
    Reintentos solo ante httpx.ConnectError, httpx.ConnectTimeout y httpx.PoolTimeout
    (esperas 0.5s y 1.0s).
    Nunca ante ReadTimeout, WriteTimeout, TimeoutException genérico, ReadError,
    RemoteProtocolError, 5xx ni 4xx.
    Timeout del cliente: httpx.Timeout(15.0, connect=5.0) por defecto.
    """
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp / Meta API no configurado; mensaje simulado")
        return ResultadoMeta(ok=True, motivo="sin_configuracion", intentos=0)

    url = f"https://graph.facebook.com/v21.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    client_timeout = timeout if timeout is not None else httpx.Timeout(15.0, connect=5.0)

    for intento in range(1, max_intentos + 1):
        try:
            client = get_meta_http_client()
            response = client.post(url, headers=headers, json=payload, timeout=client_timeout)

            if response.is_success:
                res_json = response.json()
                msg_id = (
                    res_json.get("messages", [{}])[0].get("id")
                    if res_json.get("messages")
                    else None
                )
                return ResultadoMeta(
                    ok=True,
                    http_status=response.status_code,
                    wamid_meta=msg_id,
                    intentos=intento,
                )

            # Error 4xx de cliente
            if 400 <= response.status_code < 500:
                codigo_error = None
                try:
                    err_json = response.json()
                    if isinstance(err_json, dict):
                        err_obj = err_json.get("error", {})
                        if isinstance(err_obj, dict):
                            codigo_error = err_obj.get("code")
                except Exception:
                    pass
                return ResultadoMeta(
                    ok=False,
                    http_status=response.status_code,
                    codigo_error_meta=codigo_error,
                    motivo="rechazado_meta",
                    intentos=intento,
                )

            # Error 5xx de servidor de Meta
            if response.status_code >= 500:
                return ResultadoMeta(
                    ok=False,
                    http_status=response.status_code,
                    motivo="error_meta_5xx",
                    intentos=intento,
                )

            return ResultadoMeta(
                ok=False,
                http_status=response.status_code,
                motivo="error_inesperado",
                intentos=intento,
            )

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            if intento < max_intentos:
                time.sleep(0.5 if intento == 1 else 1.0)
                continue
            return ResultadoMeta(
                ok=False,
                motivo="error_conexion",
                intentos=intento,
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.TimeoutException, httpx.ReadError, httpx.RemoteProtocolError):
            return ResultadoMeta(
                ok=False,
                motivo="sin_respuesta_meta",
                intentos=intento,
            )
        except Exception:
            return ResultadoMeta(
                ok=False,
                motivo="error_inesperado",
                intentos=intento,
            )

    return ResultadoMeta(ok=False, motivo="error_conexion", intentos=max_intentos)


def _enviar_registrado(
    numero: str,
    payload: dict,
    *,
    texto: str,
    tipo: str,
    usuario_id: UUID | None = None,
    wamid_entrante: str | None = None,
    partes: int = 1,
    aviso_tipo: str | None = None,
    exento_tope: bool = False,
    max_intentos: int = 3,
) -> bool:
    """
    Envía y registra un mensaje saliente. Controla el tope diario de salientes (150).
    Nunca lanza excepciones hacia el llamador.
    """
    try:
        from app.services import salientes_wpp_service

        tel_hash = salientes_wpp_service.hash_telefono(numero)
        es_plantilla = payload.get("type") == "template"

        es_seguridad = exento_tope or tipo in (
            "seguridad_texto",
            "plantilla:alerta_cambio_email",
            "plantilla:alerta_cambio_contrasena",
        )

        if not es_seguridad and tel_hash:
            salientes = salientes_wpp_service.salientes_hoy(tel_hash)
            if salientes >= settings.WHATSAPP_MAX_SALIENTES_DIA:
                salientes_wpp_service.registrar_saliente(
                    usuario_id=usuario_id,
                    telefono_hash=tel_hash,
                    tipo=tipo,
                    es_plantilla=es_plantilla,
                    partes=partes,
                    aviso_tipo=aviso_tipo,
                    wamid_entrante=wamid_entrante,
                    enviado=False,
                    motivo_no_enviado="tope_diario_salientes",
                    http_status=None,
                    codigo_error_meta=None,
                    wamid_meta=None,
                )
                return False

        res = _post_meta(payload, max_intentos=max_intentos)

        salientes_wpp_service.registrar_saliente(
            usuario_id=usuario_id,
            telefono_hash=tel_hash,
            tipo=tipo,
            es_plantilla=es_plantilla,
            partes=partes,
            aviso_tipo=aviso_tipo,
            wamid_entrante=wamid_entrante,
            enviado=res.ok,
            motivo_no_enviado=res.motivo,
            http_status=res.http_status,
            codigo_error_meta=res.codigo_error_meta,
            wamid_meta=res.wamid_meta,
            estado_meta="sent" if res.ok and res.wamid_meta else None,
        )
        return res.ok

    except Exception as exc:
        logger.error("Error crítico en _enviar_registrado: %s", exc)
        return False


class BuzonSalida:
    def __init__(self, numero: str, wamid_entrante: str | None = None):
        self.numero = numero
        self.numero_norm = formatear_numero_whatsapp(numero)
        self.wamid_entrante = wamid_entrante
        self.partes: list[str] = []
        self.usuario_id: UUID | None = None
        self.tipo: str = "respuesta"
        self._aviso_texto: str | None = None
        self._aviso_tipo: str | None = None
        self._aviso_al_enviar = None

    def agregar_aviso(self, texto: str, tipo: str, al_enviar=None) -> None:
        if self._aviso_texto is not None:
            return
        if "\n" in texto or len(texto) > 90:
            logger.warning("Aviso rechazado: contiene salto de línea o supera 90 caracteres: %s", texto)
            return
        self._aviso_texto = texto
        self._aviso_tipo = tipo
        self._aviso_al_enviar = al_enviar


_buzon_actual_cv: ContextVar[BuzonSalida | None] = ContextVar("_buzon_actual_cv", default=None)


def buzon_actual() -> BuzonSalida | None:
    return _buzon_actual_cv.get()


def _despachar_partido_4096(buzon: BuzonSalida, texto_completo: str) -> bool:
    trozos = []
    resto = texto_completo
    while len(resto) > 4096:
        corte = resto.rfind("\n", 0, 4096)
        if corte == -1:
            corte = 4096
        trozos.append(resto[:corte])
        resto = resto[corte:].lstrip("\n")
    if resto:
        trozos.append(resto)

    todos_ok = True
    for t in trozos:
        payload = {
            "messaging_product": "whatsapp",
            "to": buzon.numero_norm,
            "type": "text",
            "text": {"body": t},
        }
        ok = _enviar_registrado(
            buzon.numero,
            payload,
            texto=t,
            tipo=buzon.tipo,
            usuario_id=buzon.usuario_id,
            wamid_entrante=buzon.wamid_entrante,
            partes=1,
            aviso_tipo="partido_4096",
        )
        if not ok:
            todos_ok = False

    if todos_ok and buzon._aviso_al_enviar:
        try:
            buzon._aviso_al_enviar()
        except Exception as exc_al_enviar:
            logger.error("Error en callback al_enviar partido_4096: %s", exc_al_enviar)

    return todos_ok


@contextmanager
def buzon_de_respuesta(numero: str, wamid_entrante: str | None = None):
    buzon = BuzonSalida(numero, wamid_entrante)
    token = _buzon_actual_cv.set(buzon)
    try:
        yield buzon
    finally:
        _buzon_actual_cv.reset(token)
        try:
            if buzon.partes:
                cuerpo = "\n\n".join(buzon.partes)
                texto_completo = cuerpo + (f"\n{buzon._aviso_texto}" if buzon._aviso_texto else "")
                if len(texto_completo) <= 4096:
                    payload = {
                        "messaging_product": "whatsapp",
                        "to": buzon.numero_norm,
                        "type": "text",
                        "text": {"body": texto_completo},
                    }
                    enviado = _enviar_registrado(
                        buzon.numero,
                        payload,
                        texto=texto_completo,
                        tipo=buzon.tipo,
                        usuario_id=buzon.usuario_id,
                        wamid_entrante=buzon.wamid_entrante,
                        partes=len(buzon.partes),
                        aviso_tipo=buzon._aviso_tipo,
                    )
                    if enviado and buzon._aviso_al_enviar:
                        try:
                            buzon._aviso_al_enviar()
                        except Exception as exc_al_enviar:
                            logger.error("Error en callback al_enviar de aviso: %s", exc_al_enviar)
                else:
                    _despachar_partido_4096(buzon, texto_completo)
        except Exception as exc_despacho:
            logger.error("Error al despachar buzon_de_respuesta: %s", exc_despacho)


def enviar_whatsapp(numero: str, mensaje: str, tipo: str = "respuesta", exento_tope: bool = False) -> bool:
    """
    Envía o encola un mensaje de texto por WhatsApp.
    Si hay un buzón abierto para ese mismo número, agrega el texto como parte y devuelve True sin mandar.
    Si no, o si el buzón es para otro número, envía en el momento por _enviar_registrado.
    """
    buzon = buzon_actual()
    num_norm = formatear_numero_whatsapp(numero)

    if buzon is not None:
        if buzon.numero_norm == num_norm:
            buzon.partes.append(mensaje)
            return True
        else:
            tipo_envio = "otro_numero" if tipo == "respuesta" else tipo
            payload = {
                "messaging_product": "whatsapp",
                "to": num_norm,
                "type": "text",
                "text": {"body": mensaje},
            }
            return _enviar_registrado(
                numero,
                payload,
                texto=mensaje,
                tipo=tipo_envio,
                usuario_id=None,
                wamid_entrante=None,
                partes=1,
                exento_tope=exento_tope,
            )

    payload = {
        "messaging_product": "whatsapp",
        "to": num_norm,
        "type": "text",
        "text": {"body": mensaje},
    }
    return _enviar_registrado(
        numero,
        payload,
        texto=mensaje,
        tipo=tipo,
        usuario_id=None,
        wamid_entrante=None,
        partes=1,
        exento_tope=exento_tope,
    )


def enviar_whatsapp_template(
    numero: str,
    template_name: str,
    language_code: str,
    componentes: list | None = None,
    max_intentos: int = 3,
) -> bool:
    """
    Envía un mensaje de plantilla por WhatsApp usando Meta WhatsApp Cloud API.
    Si hay un buzón abierto para ese número, no se manda (devuelve False y registra motivo 'plantilla_con_buzon').
    """
    to_whatsapp = formatear_numero_whatsapp(numero)
    buzon = buzon_actual()

    if buzon is not None and buzon.numero_norm == to_whatsapp:
        from app.services import salientes_wpp_service
        tel_hash = salientes_wpp_service.hash_telefono(numero)
        salientes_wpp_service.registrar_saliente(
            usuario_id=buzon.usuario_id,
            telefono_hash=tel_hash,
            tipo=f"plantilla:{template_name}",
            es_plantilla=True,
            partes=1,
            aviso_tipo=None,
            wamid_entrante=buzon.wamid_entrante,
            enviado=False,
            motivo_no_enviado="plantilla_con_buzon",
            http_status=None,
            codigo_error_meta=None,
            wamid_meta=None,
        )
        return False

    payload = {
        "messaging_product": "whatsapp",
        "to": to_whatsapp,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language_code},
            "components": componentes or [],
        },
    }

    exento_seguridad = template_name in ("alerta_cambio_email", "alerta_cambio_contrasena")

    return _enviar_registrado(
        numero,
        payload,
        texto=f"[plantilla:{template_name}]",
        tipo=f"plantilla:{template_name}",
        usuario_id=None,
        wamid_entrante=None,
        partes=1,
        exento_tope=exento_seguridad,
        max_intentos=max_intentos,
    )


def marcar_leido_y_escribiendo(wamid: str) -> bool:
    """
    Marca un mensaje como leído y activa el indicador de escribiendo en Meta WhatsApp Cloud API.
    """
    if not wamid or not isinstance(wamid, str) or not wamid.startswith("wamid."):
        return False
    if not settings.WHATSAPP_ACCESS_TOKEN or not settings.WHATSAPP_PHONE_NUMBER_ID:
        return False

    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": wamid,
        "typing_indicator": {"type": "text"},
    }

    res = _post_meta(payload, max_intentos=1, timeout=3)
    return res.ok


