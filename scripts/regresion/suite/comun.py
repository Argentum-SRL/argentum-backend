"""Módulo común de la suite de regresión de WhatsApp: utilidades, grabaciones IA, colector y guarda."""
from __future__ import annotations

import sys
import os
import json
import time
import uuid
import re
import copy
import argparse
import threading
from decimal import Decimal
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import structlog
import logging
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

from sqlalchemy import select, text, func
from sqlalchemy.orm import sessionmaker, Session
from app.core.database import SessionLocal, engine
from app.models.usuario import Usuario, Moneda
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.grupo_cuotas import GrupoCuotas
from app.models.cuota import Cuota
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
from app.models.transferencia_interna import TransferenciaInterna
from app.models.transaccion import (
    Transaccion,
    TipoTransaccion,
    OrigenTransaccion,
    EstadoVerificacionTransaccion,
    MetodoPago,
)
from app.routers.whatsapp_ia import _procesar_webhook_whatsapp_sync
from app.routers.whatsapp.propuestas import _construir_propuesta_transaccion
from app.routers.whatsapp.registro import _confirmar_propuesta_transaccion
from app.routers.whatsapp.db_lookups import _resolver_categoria_y_subcategoria
from app.services import ai_service
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.schemas.suscripcion import SuscripcionCreate
from app.services import suscripcion_service

# Paths y configuración
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DIR_GRABACIONES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "grabaciones_ia"))
PROMPT_FILE = os.path.abspath(os.path.join(BASE_DIR, "app", "services", "ai_service.py"))

ESCENARIOS_IA_REAL = {
    "P7.1",  # Golosinas -> Kiosco (jerga argentina + descripción)
    "P7.2",  # Verdulería -> Verdulería (jerga argentina + descripción)
    "P7.3",  # Nafta -> Combustible (jerga argentina + descripción)
    "P7.4",  # Prepaga -> Obra social / Prepaga (jerga argentina + descripción)
    "P7.5",  # Bondi -> Transporte público (jerga argentina + descripción)
    "P7.6",  # Corte de pelo -> Cuidado personal (jerga argentina + descripción preservada)
    "P7.7",  # Concepto raro -> Otros (prohibición de categorías inventadas + descripción)
}


def _json_serializador(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class GestorGrabacionesIA:
    """
    Gestiona el almacenamiento y reproducción de respuestas de OpenAI para la suite de regresión.
    Evita llamadas costosas a OpenAI en escenarios que prueban lógica interna de negocio del backend.
    """
    def __init__(self, dir_grabaciones: str, ia_real: bool = False, regrabar: bool = False, forzar_grabadas: bool = False):
        self.dir_grabaciones = dir_grabaciones
        os.makedirs(self.dir_grabaciones, exist_ok=True)
        self.ia_real = ia_real
        self.regrabar = regrabar
        self.forzar_grabadas = forzar_grabadas
        self._lock = threading.Lock()
        self._escenario_actual = None
        self._call_count = 0
        self._cache_grabaciones = {}
        self.llamadas_reales = 0
        self.llamadas_grabadas = 0

    def iniciar_escenario(self, escenario_id: str):
        with self._lock:
            self._escenario_actual = escenario_id
            self._call_count = 0

    def _ruta_grabacion(self, escenario_id: str) -> str:
        safe_id = re.sub(r"[^\w\-.]", "_", escenario_id)
        return os.path.join(self.dir_grabaciones, f"{safe_id}.json")

    def _cargar_grabacion(self, escenario_id: str) -> dict | None:
        if escenario_id in self._cache_grabaciones:
            return self._cache_grabaciones[escenario_id]
        ruta = self._ruta_grabacion(escenario_id)
        if os.path.exists(ruta):
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._cache_grabaciones[escenario_id] = data
                    return data
            except Exception:
                return None
        return None

    def _guardar_grabacion(self, escenario_id: str, data: dict):
        ruta = self._ruta_grabacion(escenario_id)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=_json_serializador)
        self._cache_grabaciones[escenario_id] = data

    def procesar(self, fn_real, mensaje, usuario, db, historial=None, estado_previo=None):
        with self._lock:
            esc_id = self._escenario_actual or "GENERAL"
            idx = self._call_count
            self._call_count += 1

        debe_usar_ia_real = (
            self.ia_real or 
            self.regrabar or 
            (esc_id in ESCENARIOS_IA_REAL and not self.forzar_grabadas)
        )

        if debe_usar_ia_real:
            with self._lock:
                self.llamadas_reales += 1
            res = fn_real(mensaje, usuario, db, historial=historial, estado_previo=estado_previo)
            if self.regrabar or not os.path.exists(self._ruta_grabacion(esc_id)):
                with self._lock:
                    rec = self._cargar_grabacion(esc_id) or {
                        "escenario_id": esc_id,
                        "fecha_grabacion": datetime.now(timezone.utc).isoformat(),
                        "llamadas": []
                    }
                    if idx == 0 and self.regrabar:
                        rec["llamadas"] = []
                        rec["fecha_grabacion"] = datetime.now(timezone.utc).isoformat()
                    rec["llamadas"].append({
                        "call_idx": idx,
                        "mensaje": mensaje,
                        "respuesta": res
                    })
                    self._guardar_grabacion(esc_id, rec)
            return res

        # Modo replay
        rec = self._cargar_grabacion(esc_id)
        if rec and "llamadas" in rec and idx < len(rec["llamadas"]):
            with self._lock:
                self.llamadas_grabadas += 1
            return copy.deepcopy(rec["llamadas"][idx]["respuesta"])

        # No existe grabación previa para esta llamada: llamar a IA real y guardarla
        print(f"  [GRABANDO IA] Escenario {esc_id} llamada #{idx} ('{mensaje[:40]}') no tiene grabación. Llamando a IA real y guardando...")
        with self._lock:
            self.llamadas_reales += 1
        res = fn_real(mensaje, usuario, db, historial=historial, estado_previo=estado_previo)
        with self._lock:
            if not rec:
                rec = {
                    "escenario_id": esc_id,
                    "fecha_grabacion": datetime.now(timezone.utc).isoformat(),
                    "llamadas": []
                }
            rec["llamadas"].append({
                "call_idx": idx,
                "mensaje": mensaje,
                "respuesta": res
            })
            self._guardar_grabacion(esc_id, rec)
        return res


def verificar_antiguedad_grabaciones(dir_grabaciones: str, prompt_file: str) -> tuple[bool, str]:
    if not os.path.exists(dir_grabaciones) or not os.path.exists(prompt_file):
        return True, "OK"
    grabaciones = [os.path.join(dir_grabaciones, f) for f in os.listdir(dir_grabaciones) if f.endswith(".json")]
    if not grabaciones:
        return True, "Sin grabaciones previas"
    mtime_prompt = os.path.getmtime(prompt_file)
    mtime_grabaciones = max(os.path.getmtime(g) for g in grabaciones)
    if mtime_prompt > mtime_grabaciones:
        diff_seg = int(mtime_prompt - mtime_grabaciones)
        return False, f"Las grabaciones son más viejas que app/services/ai_service.py por {diff_seg}s. Se recomienda regrabar con --regrabar."
    return True, "OK (grabaciones al día con el prompt)"


def verificar_grabaciones_sin_datos_personales(dir_grabaciones: str) -> tuple[bool, list[str]]:
    patrones_env = os.environ.get("WPP_PATRONES_PROHIBIDOS")
    if patrones_env:
        patrones_prohibidos = [p.strip().lower() for p in patrones_env.split(",") if p.strip()]
    else:
        patrones_prohibidos = [
            "password", "secret", "token", "tarjeta", "dni", "cuit", "cvu", "cbu"
        ]
    hallazgos = []
    if not os.path.exists(dir_grabaciones):
        return True, []
    for fname in os.listdir(dir_grabaciones):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(dir_grabaciones, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read().lower()
            for p in patrones_prohibidos:
                if p in content:
                    hallazgos.append(f"{fname}: contiene '{p}'")
    return len(hallazgos) == 0, hallazgos


_original_procesar_mensaje = ai_service.procesar_mensaje

def _hook_procesar_mensaje(mensaje, usuario, db, historial=None, estado_previo=None):
    if _gestor_actual is not None:
        return _gestor_actual.procesar(
            _original_procesar_mensaje,
            mensaje,
            usuario,
            db,
            historial=historial,
            estado_previo=estado_previo,
        )
    return _original_procesar_mensaje(
        mensaje, usuario, db, historial=historial, estado_previo=estado_previo
    )

ai_service.procesar_mensaje = _hook_procesar_mensaje

_disparos_guarda: int = 0

def _instalar_guarda_seguridad():
    global _disparos_guarda
    _disparos_guarda = 0

    try:
        import httpx
        _orig_client_send = httpx.Client.send
        def _guarda_client_send(self, request, *args, **kwargs):
            global _disparos_guarda
            url_str = str(getattr(request, "url", ""))
            if "graph.facebook.com" in url_str:
                _disparos_guarda += 1
                raise RuntimeError(f"GUARDA DE SEGURIDAD DISPARADA: Pedido HTTP a {url_str} interceptado en la suite")
            return _orig_client_send(self, request, *args, **kwargs)
        httpx.Client.send = _guarda_client_send
    except Exception:
        pass

    try:
        import requests
        _orig_session_send = requests.Session.send
        def _guarda_session_send(self, request, *args, **kwargs):
            global _disparos_guarda
            url_str = str(getattr(request, "url", ""))
            if "graph.facebook.com" in url_str:
                _disparos_guarda += 1
                raise RuntimeError(f"GUARDA DE SEGURIDAD DISPARADA: Pedido HTTP a {url_str} interceptado en la suite")
            return _orig_session_send(self, request, *args, **kwargs)
        requests.Session.send = _guarda_session_send
    except Exception:
        pass

_instalar_guarda_seguridad()


class ColectorSalidas:
    """
    Colector de fotos de salida por escenario:
    - mensajes enviados por el bot (orden y textual)
    - movimientos creados antes del rollback
    - conversaciones_wpp creadas (intent y accion_ejecutada)
    Sin IDs ni horas de creación.
    """
    def __init__(self, ruta_json: str):
        self.ruta_json = ruta_json
        self.salidas: dict[str, dict] = {}
        self.escenario_actual: str | None = None
        self._mensajes_escenario: list[str] = []
        self._lock = threading.Lock()

    def iniciar_escenario(self, escenario_id: str):
        with self._lock:
            self.escenario_actual = escenario_id
            self._mensajes_escenario = []

    def registrar_mensaje(self, mensaje: str):
        with self._lock:
            if self.escenario_actual:
                self._mensajes_escenario.append(mensaje)

    def registrar_salida_escenario(self, escenario_id: str, movimientos: list[dict], conversaciones: list[dict]):
        with self._lock:
            mensajes = list(self._mensajes_escenario)
            if escenario_id == "P5.6":
                mensajes = sorted(mensajes)
            self.salidas[escenario_id] = {
                "mensajes": mensajes,
                "movimientos": movimientos,
                "conversaciones": conversaciones,
            }

    def guardar(self):
        if not self.ruta_json:
            return
        p = Path(self.ruta_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(self.salidas, f, ensure_ascii=False, indent=2)

_colector_salidas: ColectorSalidas | None = None


def _normalizar_accion_ejecutada(accion: str | None) -> str | None:
    if accion is None:
        return None
    return re.sub(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '<ID>', str(accion))

USUARIO_PRUEBAS_EMAIL = "testingadmin@argentum.com"
TELEFONO_TEST = "5491100000000"

def make_payload(from_number: str, message: str, wamid: str | None = None) -> bytes:
    if not wamid:
        wamid = f"wamid_reg_{uuid.uuid4().hex[:12]}"
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": from_number or TELEFONO_TEST,
                                    "type": "text",
                                    "text": {"body": message},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    return json.dumps(payload).encode("utf-8")

_testingadmin_id = None

def _mock_buscar_usuario_testingadmin(from_number, db_session):
    global _testingadmin_id
    if _testingadmin_id is None:
        _testingadmin_id = db_session.execute(
            select(Usuario.id).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)
        ).scalar_one_or_none()
    u = db_session.get(Usuario, _testingadmin_id)
    if not u or u.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Intento de resolución a usuario no-testingadmin: {getattr(u, 'email', None)}")
    return u


def run_isolated(fn):
    """Ejecuta una función en una transacción aislada que siempre termina en rollback."""
    conn = engine.connect()
    trans = conn.begin()
    respuestas = []
    BoundSession = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")

    tx_ids_antes = set(conn.execute(select(Transaccion.id)).scalars().all())
    conv_ids_antes = set(conn.execute(select(ConversacionWpp.id)).scalars().all())

    def _mock_envio(t, m):
        respuestas.append((t, m))
        if _colector_salidas is not None:
            _colector_salidas.registrar_mensaje(m)

    try:
        # Modularización WhatsApp: Un solo camino de envío vía whatsapp_service.enviar_whatsapp.
        with patch("app.routers.whatsapp_ia.SessionLocal", BoundSession), \
             patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_usuario_testingadmin), \
             patch("app.services.whatsapp_service.enviar_whatsapp", side_effect=_mock_envio), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            res = fn(conn, BoundSession, respuestas)

            if _colector_salidas is not None and _colector_salidas.escenario_actual:
                sess_col = BoundSession()
                try:
                    if tx_ids_antes:
                        tx_nuevas = sess_col.execute(
                            select(Transaccion).where(Transaccion.id.not_in(tx_ids_antes))
                        ).scalars().all()
                    else:
                        tx_nuevas = sess_col.execute(select(Transaccion)).scalars().all()

                    cat_map = dict(sess_col.execute(select(Categoria.id, Categoria.nombre)).all())
                    subcat_map = dict(sess_col.execute(select(Subcategoria.id, Subcategoria.nombre)).all())
                    bill_map = dict(sess_col.execute(select(Billetera.id, Billetera.nombre)).all())

                    movs = []
                    for tx in tx_nuevas:
                        movs.append({
                            "tipo": tx.tipo.value if hasattr(tx.tipo, "value") else str(tx.tipo),
                            "monto": float(tx.monto),
                            "moneda": tx.moneda.value if hasattr(tx.moneda, "value") else str(tx.moneda),
                            "fecha": tx.fecha.isoformat() if hasattr(tx.fecha, "isoformat") else str(tx.fecha),
                            "categoria": cat_map.get(tx.categoria_id),
                            "subcategoria": subcat_map.get(tx.subcategoria_id),
                            "billetera": bill_map.get(tx.billetera_id),
                            "descripcion": tx.descripcion,
                        })
                    movs.sort(key=lambda m: (m["fecha"], m["tipo"], m["monto"], m["descripcion"], m["billetera"] or "", m["categoria"] or "", m["subcategoria"] or ""))

                    if conv_ids_antes:
                        conv_nuevas = sess_col.execute(
                            select(ConversacionWpp).where(ConversacionWpp.id.not_in(conv_ids_antes))
                        ).scalars().all()
                    else:
                        conv_nuevas = sess_col.execute(select(ConversacionWpp)).scalars().all()

                    convs = []
                    for c in conv_nuevas:
                        convs.append({
                            "intent": c.intent_detectado,
                            "accion_ejecutada": _normalizar_accion_ejecutada(c.accion_ejecutada),
                        })
                    convs.sort(key=lambda c: (c["intent"] or "", c["accion_ejecutada"] or ""))

                    _colector_salidas.registrar_salida_escenario(_colector_salidas.escenario_actual, movs, convs)
                finally:
                    sess_col.close()

            return res
    finally:
        trans.rollback()
        conn.close()


def resolver_datos_base(db: Session):
    u = db.execute(select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalars().first()
    if not u:
        raise RuntimeError(f"ABORT CRITICO: Usuario de pruebas no encontrado en base de datos: {USUARIO_PRUEBAS_EMAIL}")
    if u.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Usuario resuelto no es testingadmin: {u.email}")
    
    bills = db.execute(select(Billetera).where(Billetera.usuario_id == u.id)).scalars().all()
    return {
        USUARIO_PRUEBAS_EMAIL: {
            "usuario": u,
            "billeteras": {b.nombre: b for b in bills},
        }
    }

_gestor_actual: GestorGrabacionesIA | None = None
