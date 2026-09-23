"""
Suite consolidada de regresión de WhatsApp para Argentum.
Ejecuta todos los escenarios acumulados (Puntos 3, 4, 5, 6, 7, 8, 9, 9B, 10, 11 y 12) usando exclusivamente testingadmin@argentum.com
con verificación automática y rollback total.

CUÁNDO USAR CADA MODO:
- Modo Grabado (predeterminado): Para validar la lógica del backend sin gastar llamadas de OpenAI ni esperar latencia de red.
- Modo IA Real (--ia-real / --live): Para validar integración real con OpenAI de principio a fin.
- Regrabar (--regrabar / --record): OBLIGATORIO cuando se modifique el SYSTEM_PROMPT o el esquema estructurado en app/services/ai_service.py.
  Si el archivo ai_service.py es más reciente que las grabaciones, la suite advertirá automáticamente.
- Escenarios que siempre usan IA real por diseño: P7.1 a P7.7 (prueban modismos, jerga argentina y categorización estricta del LLM).
"""
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
from unittest.mock import patch

# Asegurar path al backend
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

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
from app.routers.whatsapp_ia import (
    _procesar_webhook_whatsapp_sync,
    _construir_propuesta_transaccion,
    _confirmar_propuesta_transaccion,
    _resolver_categoria_y_subcategoria,
)
from app.services import ai_service
from app.models.suscripcion import Suscripcion, EstadoSuscripcion, FrecuenciaSuscripcion
from app.models.historial_suscripcion import HistorialSuscripcion
from app.schemas.suscripcion import SuscripcionCreate
from app.services import suscripcion_service

# ==============================================================================
# CONFIGURACIÓN DE GRABACIONES DE IA Y FIXTURES
# ==============================================================================
DIR_GRABACIONES = os.path.abspath(os.path.join(os.path.dirname(__file__), "grabaciones_ia"))
PROMPT_FILE = os.path.abspath(os.path.join(BASE_DIR, "app", "services", "ai_service.py"))

# Escenarios que prueban el comportamiento del modelo en sí (jerga argentina, categorización estricta, mapeo de modismos).
# Estos escenarios SIEMPRE ejecutan contra la IA real a menos que se use --forzar-grabadas.
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


_gestor_actual: GestorGrabacionesIA | None = None
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

# Instalar hook para interceptar llamadas en whatsapp_ia y en cualquier servicio
ai_service.procesar_mensaje = _hook_procesar_mensaje


USUARIO_PRUEBAS_EMAIL = "testingadmin@argentum.com"
TELEFONO_TEST = "+5491100000000"

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
    try:
        # Modularización WhatsApp: Se patchea en whatsapp_ia (para imports sueltos legacy)
        # y en whatsapp_service (para módulos de handlers nuevos que usan acceso calificado).
        with patch("app.routers.whatsapp_ia.SessionLocal", BoundSession), \
             patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_usuario_testingadmin), \
             patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=lambda t, m: respuestas.append((t, m))), \
             patch("app.services.whatsapp_service.enviar_whatsapp", side_effect=lambda t, m: respuestas.append((t, m))), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            res = fn(conn, BoundSession, respuestas)
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

def obtener_conteos_base(db: Session):
    tx_cnt = db.execute(select(func.count(Transaccion.id))).scalar()
    conv_cnt = db.execute(select(func.count(ConversacionWpp.id))).scalar()
    msg_cnt = db.execute(select(text("count(*)")).select_from(text("mensajes_whatsapp_procesados"))).scalar()
    saldos = {}
    for b in db.execute(select(Billetera).order_by(Billetera.id)).scalars().all():
        saldos[str(b.id)] = b.saldo_actual
    return {"tx": tx_cnt, "conv": conv_cnt, "msg": msg_cnt, "saldos": saldos}

def obtener_saldos_21(db: Session):
    return {
        (email, b.nombre, b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)): b.saldo_actual
        for b, email in db.execute(
            select(Billetera, Usuario.email)
            .join(Usuario, Billetera.usuario_id == Usuario.id)
            .order_by(Usuario.email, Billetera.nombre)
        ).all()
    }

# Baseline documentado de diferencias aceptadas en reconciliación.
# Proviene del alta histórica de datos (junio 2026), donde las billeteras
# Galicia del usuario de pruebas tienen saldo_inicial en 0
# pero sus movimientos bancarios acumulados difieren en -$800.941 respecto al saldo guardado.
# (testingadmin@argentum.com reconcilia con diff=0.00 tras el enriquecimiento histórico del 2026-09-05).
# La suite fallará si aparece una diferencia NUEVA o si alguna de estas cambia.
DIFERENCIAS_RECONCILIACION_BASELINE = {
    ("mrm291201@gmail.com", "Galicia"): Decimal("-941.00"),
}

def verificar_reconciliacion_billeteras(db: Session):
    """
    Compara el saldo_actual guardado de cada una de las 21 billeteras contra
    el saldo calculado a partir de sus movimientos confirmados y transferencias:
      saldo_inicial + sum(ingresos) - sum(egresos) + sum(tr_in) - sum(tr_out)
    donde las transacciones computadas son aquellas que afectan saldo (no crédito,
    no pendientes, fecha <= hoy).
    Compara contra el baseline conocido de diferencias históricas y reporta
    discrepancias solo si surge una diferencia NUEVA o cambia una existente.
    """
    from app.models.billetera import Billetera
    from app.models.usuario import Usuario
    from app.utils.fecha import hoy_argentina
    
    hoy = hoy_argentina()
    billeteras = db.execute(
        select(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
    ).all()
    
    discrepancias_no_esperadas = []
    detalles = []
    
    for b, email in billeteras:
        s_guardado = b.saldo_actual
        s_inicial = b.saldo_inicial or Decimal("0.00")
        
        tx_row = db.execute(text("""
            SELECT 
                coalesce(sum(case when tipo = 'ingreso' then monto else 0 end), 0) as ingresos,
                coalesce(sum(case when tipo = 'egreso' then monto else 0 end), 0) as egresos
            FROM transacciones
            WHERE billetera_id = :bid
              AND (metodo_pago != 'credito' OR metodo_pago IS NULL)
              AND es_padre_cuotas = false
              AND es_cuota_hija = false
              AND (estado_verificacion IS NULL OR estado_verificacion != 'pendiente')
              AND fecha <= :hoy
        """), {"bid": b.id, "hoy": hoy}).mappings().fetchone()
        
        ingresos = Decimal(str(tx_row["ingresos"]))
        egresos = Decimal(str(tx_row["egresos"]))
        
        tr_in = Decimal(str(db.execute(text("""
            SELECT coalesce(sum(monto_destino), 0) 
            FROM transferencias_internas 
            WHERE billetera_destino_id = :bid
        """), {"bid": b.id}).scalar() or 0))
        
        tr_out = Decimal(str(db.execute(text("""
            SELECT coalesce(sum(monto_origen), 0) 
            FROM transferencias_internas 
            WHERE billetera_origen_id = :bid
        """), {"bid": b.id}).scalar() or 0))
        
        s_calc = s_inicial + ingresos - egresos + tr_in - tr_out
        diff = s_guardado - s_calc
        esperado_diff = DIFERENCIAS_RECONCILIACION_BASELINE.get((email, b.nombre), Decimal("0.00"))
        coincide_con_baseline = (diff == esperado_diff)
        
        item = {
            "email": email,
            "billetera": b.nombre,
            "guardado": s_guardado,
            "calculado": s_calc,
            "diferencia": diff,
            "esperado_diff": esperado_diff,
            "ok": coincide_con_baseline
        }
        detalles.append(item)
        if not coincide_con_baseline:
            discrepancias_no_esperadas.append(item)
            
    return len(discrepancias_no_esperadas) == 0, discrepancias_no_esperadas, detalles

SALDOS_REFERENCIA_21 = {
    ("testingadmin@argentum.com", "Efectivo ARS", "ARS"): Decimal("0.00"),
    ("testingadmin@argentum.com", "Efectivo USD", "USD"): Decimal("0.00"),
    ("testingadmin@argentum.com", "Galicia", "ARS"): Decimal("1889058.71"),  # Actualizado 2026-09-16: cobro legítimo Spotify 15/09 (-$4.500)
    ("testingadmin@argentum.com", "Santander", "ARS"): Decimal("84270.29"),
}

def verificar_saldos_contra_referencia(db: Session, saldos_inicio_21: dict):
    """
    Compara testingadmin contra referencias fijas y las otras cinco cuentas
    contra la foto tomada al inicio de la suite.
    """
    from app.models.billetera import Billetera
    from app.models.usuario import Usuario

    billeteras = db.execute(
        select(Billetera, Usuario.email)
        .join(Usuario, Billetera.usuario_id == Usuario.id)
        .order_by(Usuario.email, Billetera.nombre)
    ).all()

    desvios = []
    detalles = []
    for b, email in billeteras:
        actual = b.saldo_actual
        moneda = b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)
        clave = (email, b.nombre, moneda)
        ref = SALDOS_REFERENCIA_21.get(clave) or SALDOS_REFERENCIA_21.get((email, b.nombre))
        saldo_inicial = saldos_inicio_21.get(clave)
        esperado = ref if ref is not None else saldo_inicial
        diff = actual - esperado if esperado is not None else None
        item = {
            "email": email,
            "billetera": b.nombre,
            "moneda": moneda,
            "actual": actual,
            "referencia": ref,
            "saldo_inicial": saldo_inicial,
            "diff": diff
        }
        detalles.append(item)
        if esperado is None or actual != esperado:
            desvios.append(item)
    return len(desvios) == 0, desvios, detalles


# ==============================================================================
# ESCENARIOS PUNTO 3: Resolución de Billeteras (10 casos)
# ==============================================================================

def p3_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        # Opción 2 en menú de testingadmin (Efectivo ARS, Galicia, Santander) es Galicia
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "9"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_6(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        hace_31_min = datetime.now(timezone.utc) - timedelta(minutes=31)
        conv_vencida = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_venc_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            confianza=Decimal("0.900"),
            fecha=hace_31_min,
        )
        db.add(conv_vencida)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_8(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál.",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no, fue en Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p3_caso_9(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cobré 800000 de sueldo"), time.perf_counter())
        pregunta = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "1"), time.perf_counter())
        propuesta = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        return f"{pregunta}\n---\n{propuesta}"
    return run_isolated(test)

def p3_caso_10(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND nombre IN ('Galicia', 'Santander')"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 4: Cambios de tema y gestión de slots (8 casos + 6 variantes de no)
# ==============================================================================

def p4_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "hola"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 12000 en verdulería"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia"},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        respuestas.clear()
        txs_antes = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "dale"), time.perf_counter())
        txs_despues = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        resp = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        return f"{resp} (txs_creadas={txs_despues - txs_antes})"
    return run_isolated(test)

def p4_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "buenas"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_6(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté en pizza"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        prefijos = (
            "En este ciclo gastaste", "En este ciclo no registraste gastos", "En este ciclo no encontré gastos",
            "Hoy gastaste", "Hoy no registraste gastos", "Hoy no encontré gastos",
        )
        msg_ok = any(resp.startswith(p) for p in prefijos)
        return f"Intent: {intent} | Respuesta ok: {msg_ok}"
    return run_isolated(test)

def p4_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        hace_31_min = datetime.now(timezone.utc) - timedelta(minutes=31)
        conv_vencida = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_venc_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            confianza=Decimal("0.900"),
            fecha=hace_31_min,
        )
        db.add(conv_vencida)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_caso_8(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p4_variante_no(datos, msg_variante, billetera_propuesta="Galicia"):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conv_propuesta = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot=f"Voy a anotar $5.000 en Kiosco desde {billetera_propuesta}. ¿Va?",
            intent_detectado="registrar_transaccion",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": billetera_propuesta},
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_propuesta)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, msg_variante), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 5: Prevención de Duplicados y Concurrencia (8 casos)
# ==============================================================================

def p5_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es nuevo"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es un error"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_antigua = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="TEST_REG_ANTIGUA",
            metodo_pago="efectivo",
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(hours=2, minutes=5),
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_antigua)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en la farmacia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_6_concurrente(datos):
    """
    Prueba concurrencia real garantizando limpieza absoluta e infalible de base de datos.
    Usa exclusivamente testingadmin@argentum.com y su billetera Galicia.
    Registra snapshot exacto de IDs de transacciones antes y después, y borra la diferencia.
    """
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    saldo_original = b.saldo_actual
    
    # Snapshot de transacciones existentes antes de la prueba
    snap_db = SessionLocal()
    tx_ids_antes = set(snap_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
    snap_db.close()

    pid = uuid.uuid4()
    p_wamid = f"reg_c6_prop_{uuid.uuid4().hex}"
    wamid1 = f"reg_c6_conf_1_{uuid.uuid4().hex}"
    wamid2 = f"reg_c6_conf_2_{uuid.uuid4().hex}"

    db = SessionLocal()
    prop = ConversacionWpp(
        id=pid,
        usuario_id=u.id,
        wamid=p_wamid,
        mensaje_usuario="gasté 5000 en el kiosco",
        tipo_mensaje=TipoMensajeWpp.TEXTO,
        mensaje_bot=f"Voy a anotar $5.000 en Kiosco desde {b.nombre}. ¿Va?",
        intent_detectado="registrar_transaccion",
        entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": b.nombre},
        slot_filling_activo=False,
        accion_ejecutada=None,
        confianza=Decimal("0.950"),
        fecha=datetime.now(timezone.utc),
    )
    db.add(prop)
    db.commit()
    db.close()

    resp_c6 = []
    lock = threading.Lock()
    def mock_envio(to, msg):
        with lock:
            resp_c6.append(msg)

    def _mock_buscar_concurrente(tel, db_sess):
        usr = db_sess.query(Usuario).filter(Usuario.email == USUARIO_PRUEBAS_EMAIL).first()
        if not usr or usr.email != USUARIO_PRUEBAS_EMAIL:
            raise RuntimeError(f"ABORT CRITICO: Concurrencia intentó usar otro usuario: {getattr(usr, 'email', None)}")
        return usr

    bar = threading.Barrier(2)
    def worker(wid, wamid_val):
        payload = make_payload(TELEFONO_TEST, "sí", wamid_val)
        bar.wait()
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

    try:
        # Modularización WhatsApp: Doble patch para soportar imports sueltos en whatsapp_ia y acceso calificado en handlers.
        with patch("app.routers.whatsapp_ia._buscar_usuario_por_telefono", side_effect=_mock_buscar_concurrente), \
             patch("app.routers.whatsapp_ia.enviar_whatsapp", side_effect=mock_envio), \
             patch("app.services.whatsapp_service.enviar_whatsapp", side_effect=mock_envio), \
             patch("app.routers.whatsapp_ia._verificar_rate_limit_registrado", return_value=(True, None)):
            th1 = threading.Thread(target=worker, args=(1, wamid1))
            th2 = threading.Thread(target=worker, args=(2, wamid2))
            th1.start()
            th2.start()
            th1.join()
            th2.join()
    finally:
        # Limpieza infalible: identificar exactamente las transacciones creadas por diferencia de conjuntos
        clean_db = SessionLocal()
        tx_ids_despues = set(clean_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        creadas = tx_ids_despues - tx_ids_antes
        for tx_id in creadas:
            clean_db.execute(text("DELETE FROM transacciones WHERE id = :txid"), {"txid": tx_id})

        # Limpieza de mensajes procesados y conversaciones creadas en esta prueba
        clean_db.execute(text("DELETE FROM mensajes_whatsapp_procesados WHERE wamid IN (:w1, :w2, :pw)"), {"w1": wamid1, "w2": wamid2, "pw": p_wamid})
        clean_db.execute(text("DELETE FROM conversaciones_wpp WHERE usuario_id = :uid AND (wamid IN (:w1, :w2, :pw) OR id = :pid)"), {"uid": u.id, "w1": wamid1, "w2": wamid2, "pw": p_wamid, "pid": pid})
        clean_db.execute(text("UPDATE billeteras SET saldo_actual = :s WHERE id = :bid"), {"s": saldo_original, "bid": b.id})
        clean_db.commit()

        # Verificación estricta post-limpieza
        tx_post = set(clean_db.execute(select(Transaccion.id).where(Transaccion.usuario_id == u.id)).scalars().all())
        b_actual = clean_db.execute(select(Billetera).where(Billetera.id == b.id)).scalar_one()
        clean_db.close()

        if tx_post != tx_ids_antes:
            sobrantes = tx_post - tx_ids_antes
            raise RuntimeError(f"Limpieza falló: transacciones residuales no borradas: {sobrantes}")
        if b_actual.saldo_actual != saldo_original:
            raise RuntimeError(f"Limpieza falló: saldo billetera {b_actual.saldo_actual} != original {saldo_original}")

    # Verificar que exactamente 1 confirmó con éxito y 1 fue rechazada por ya confirmada
    exitos = [r for r in resp_c6 if "registrado" in r.lower()]
    dups = [r for r in resp_c6 if "ya fue confirmada" in r.lower() or "no tenés ninguna operación pendiente" in r.lower()]
    return f"Exitos={len(exitos)}, Rechazados_por_concurrencia={len(dups)}"

def p5_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y otros 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p5_caso_cuotas(datos):
    from app.models.transaccion import MetodoPago
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_cuota = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Cuota 1/3 Kiosco",
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.MANUAL,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=5),
            es_recurrente=False,
            es_cuota_hija=True,
            es_padre_cuotas=False,
        )
        db.add(tx_cuota)
        db.commit()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 6: Veracidad en Fechas, Monedas, Lotes y Descarte (10 casos)
# ==============================================================================

def p6_ejecutar_caso(datos, nombre_caso, ent):
    def test(conn, Session, respuestas):
        db = Session()
        u = db.execute(select(Usuario).where(Usuario.email == USUARIO_PRUEBAS_EMAIL)).scalar_one()

        b_nom = ent.get("billetera_destino") if ent.get("tipo") == "ingreso" else ent.get("billetera_origen")
        b_obj = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == b_nom)).scalars().first()
        b_mon = b_obj.moneda if b_obj else (Moneda.USD if "USD" in (b_nom or "") else Moneda.ARS)

        entidades_caso = dict(ent)
        if "transacciones_adicionales" in ent:
            entidades_caso["transacciones_adicionales"] = [dict(x) for x in ent["transacciones_adicionales"]]

        propuesta_texto = _construir_propuesta_transaccion(entidades_caso, b_nom, se_asumio_principal=False, billetera_moneda=b_mon)

        if "No se puede registrar ningún movimiento." in propuesta_texto:
            return f"Propuesta:\n{propuesta_texto}\nConfirmación:\nNO_APLICA"

        pid = uuid.uuid4()
        conv = ConversacionWpp(
            id=pid,
            usuario_id=u.id,
            wamid=f"sim_prop_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="simulacion",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot=propuesta_texto,
            intent_detectado="registrar_transaccion",
            entidades=entidades_caso,
            slot_filling_activo=False,
            accion_ejecutada=None,
            confianza=Decimal("0.950"),
            fecha=datetime.now(timezone.utc),
        )
        db.add(conv)
        db.commit()

        tx, msg_confirm, ya_conf = _confirmar_propuesta_transaccion(u, db, propuesta_id=pid)
        return f"Propuesta:\n{propuesta_texto}\nConfirmación:\n{msg_confirm}"

    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 7: Categorías estrictas, jerga argentina y descripciones
# ==============================================================================

def p7_ejecutar_caso(datos, mensaje, cat_esperada, desc_esperada):
    def test(conn, Session, respuestas):
        db = Session()
        u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
        res = ai_service.procesar_mensaje(mensaje, u, db)
        cat_obtenida = res.get("entidades", {}).get("categoria")
        desc_obtenida = res.get("entidades", {}).get("descripcion")

        cat_id, sub_id = _resolver_categoria_y_subcategoria(cat_obtenida, u.id, db, tipo="egreso")
        if not cat_id:
            return f"Error: no se pudo resolver categoría {cat_obtenida}"

        return f"Cat: {cat_obtenida} | Desc: {desc_obtenida}"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 8: Deshacer y Corregir movimientos por WhatsApp (11 casos)
# ==============================================================================

def p8_caso_1(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        
        b_db = db.get(Billetera, b.id)
        s0 = b_db.saldo_actual

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        db.refresh(b_db)
        txs = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().all()
        assert len(txs) == 1
        assert b_db.saldo_actual == s0 - Decimal("5000")

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        resp_propuesta = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_confirmacion = respuestas[-1][1] if respuestas else ""

        txs_post = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().all()
        db.refresh(b_db)
        
        ok_borrado = (len(txs_post) == 0)
        ok_saldo = (b_db.saldo_actual == s0)

        return f"Propuesta:\n{resp_propuesta}\nConfirmación:\n{resp_confirmacion}\nBorrado: {ok_borrado} | Saldo restaurado: {ok_saldo}"
    return run_isolated(test)

def p8_caso_2(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("DELETE FROM transacciones WHERE usuario_id = :uid AND origen = 'ia_wpp'"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_3(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_4(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        b_db = db.get(Billetera, b.id)
        s0 = b_db.saldo_actual

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 en supermercado"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3.000 no 30.000"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        db.refresh(b_db)
        
        ok_monto = (tx.monto == Decimal("3000"))
        ok_saldo = (b_db.saldo_actual == s0 - Decimal("3000"))

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nMonto corregido: {ok_monto} | Saldo ajustado (+27k): {ok_saldo}"
    return run_isolated(test)

def p8_caso_5(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eso era supermercado"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        sub = db.get(Subcategoria, tx.subcategoria_id) if tx and tx.subcategoria_id else None
        cat = db.get(Categoria, tx.categoria_id) if tx and tx.categoria_id else None
        cat_nom = sub.nombre if sub else (cat.nombre if cat else "")

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nCategoría final: {cat_nom}"
    return run_isolated(test)

def p8_caso_6(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    b_san = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Santander"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_g_db = db.get(Billetera, b_gal.id)
        b_s_db = db.get(Billetera, b_san.id)
        s0_gal = b_g_db.saldo_actual
        s0_san = b_s_db.saldo_actual

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "fue con Santander"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        db.refresh(b_g_db)
        db.refresh(b_s_db)

        ok_bill = (tx.billetera_id == b_san.id)
        ok_gal = (b_g_db.saldo_actual == s0_gal)
        ok_san = (b_s_db.saldo_actual == s0_san - Decimal("5000"))

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nBilletera Santander: {ok_bill} | Saldo Galicia revertido: {ok_gal} | Saldo Santander descontado: {ok_san}"
    return run_isolated(test)

def p8_caso_7(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "fue ayer"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        from app.utils.fecha import hoy_argentina
        ayer = hoy_argentina() - timedelta(days=1)
        ok_fecha = (tx.fecha == ayer)

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nFecha ayer: {ok_fecha}"
    return run_isolated(test)

def p8_caso_8(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_san = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Santander"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 10000 en el kiosco"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3000 con Santander"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx = db.execute(select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalars().first()
        ok_monto = (tx.monto == Decimal("3000"))
        ok_bill = (tx.billetera_id == b_san.id)

        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nMonto 3000: {ok_monto} | Santander: {ok_bill}"
    return run_isolated(test)

def p8_caso_9(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        
        tx_cuota = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("5000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Cuota 1/3 Kiosco",
            metodo_pago=MetodoPago.CREDITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=2),
            es_recurrente=False,
            es_cuota_hija=True,
            es_padre_cuotas=False,
        )
        db.add(tx_cuota)
        db.flush()

        conv = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_cuota_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="cuota",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Listo.",
            intent_detectado="registrar_transaccion",
            entidades={},
            slot_filling_activo=False,
            accion_ejecutada=str(tx_cuota.id),
            confianza=Decimal("1.000"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
        db.add(conv)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_10(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")

        hace_35_min = datetime.now(timezone.utc) - timedelta(minutes=35)
        tx_vieja = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("30000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Supermercado",
            metodo_pago=MetodoPago.DEBITO,
            billetera_id=b.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=hace_35_min,
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_vieja)
        db.flush()

        conv = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_old_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="supermercado 30000",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Listo.",
            intent_detectado="registrar_transaccion",
            entidades={},
            slot_filling_activo=False,
            accion_ejecutada=str(tx_vieja.id),
            confianza=Decimal("1.000"),
            fecha=hace_35_min,
        )
        db.add(conv)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3.000 no 30.000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else "SIN_RESPUESTA"
    return run_isolated(test)

def p8_caso_11(datos):
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_ef = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Efectivo ARS"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        # 1. Movimiento previo A registrado en Efectivo ARS
        cat_id, sub_id = _resolver_categoria_y_subcategoria("Kiosco", u.id, db, "egreso")
        tx_prev = Transaccion(
            usuario_id=u.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("1000.00"),
            moneda=Moneda.ARS,
            fecha=datetime.now(timezone.utc).date(),
            descripcion="Golosinas",
            metodo_pago=MetodoPago.EFECTIVO,
            billetera_id=b_ef.id,
            categoria_id=cat_id,
            subcategoria_id=sub_id,
            origen=OrigenTransaccion.IA_WPP,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            fecha_creacion=datetime.now(timezone.utc) - timedelta(minutes=5),
            es_recurrente=False,
            es_cuota_hija=False,
            es_padre_cuotas=False,
        )
        db.add(tx_prev)
        db.flush()

        conv_prev = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_prev_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="golosinas 1000",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="Listo. $1.000 en Kiosco desde Efectivo ARS — registrado.",
            intent_detectado="registrar_transaccion",
            entidades={},
            slot_filling_activo=False,
            accion_ejecutada=str(tx_prev.id),
            confianza=Decimal("1.000"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        db.add(conv_prev)
        db.commit()

        # 2. Enviar un nuevo gasto que queda como propuesta pendiente (Galicia)
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        # 3. Decir "no, fue en Santander"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no, fue en Santander"), time.perf_counter())
        resp_corr_prop = respuestas[-1][1] if respuestas else ""

        # Verificar que el movimiento previo A sigue intacto en Efectivo ARS
        db.refresh(tx_prev)
        ok_previa = (tx_prev and tx_prev.billetera_id == b_ef.id)

        return f"Respuesta:\n{resp_corr_prop}\nMovimiento anterior intacto en Efectivo ARS: {ok_previa}"
    return run_isolated(test)

# --- PUNTO 9A: Tarjetas de crédito y cuotas ---
def p9_caso_1(datos):
    """'gasté 30000 con la Amex': resuelve la tarjeta única, no descuenta saldo, crea una cuota."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        saldo_antes = b_gal.saldo_actual
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la Amex"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        
        db = Session()
        b_act = db.execute(select(Billetera).where(Billetera.id == b_gal.id)).scalar_one()
        saldo_intacto = (b_act.saldo_actual == saldo_antes)
        tx_padre = db.execute(
            select(Transaccion).where(
                Transaccion.usuario_id == u.id,
                Transaccion.origen == OrigenTransaccion.IA_WPP,
                Transaccion.es_padre_cuotas == True
            )
        ).first()
        cuotas = db.execute(select(Cuota).join(GrupoCuotas).where(GrupoCuotas.usuario_id == u.id)).all()
        return f"Propuesta:\n{resp_prop}\nConfirmación:\n{resp_conf}\nSaldo intacto: {saldo_intacto} | Es padre: {tx_padre is not None} | Cuotas creadas: {len(cuotas) > 0}"
    return run_isolated(test)

def p9_caso_2(datos):
    """'gasté 30000 con la Visa': pregunta cuál de las dos."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la Visa"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_3(datos):
    """'gasté 30000 con la del Santander': resuelve la 5077."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la del Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_4(datos):
    """'compré una tele en 12 cuotas de 80000': propone 12 cuotas de 80.000, total 960.000."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré una tele en 12 cuotas de 80000"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_5(datos):
    """'gasté 80000 en 12 cuotas': propone 12 cuotas de 6.666,67, total 80.000."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 80000 en 12 cuotas"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_6(datos):
    """'gasté 5000 con Galicia': sigue siendo la billetera, no la tarjeta."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con Galicia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_7(datos):
    """'gasté 5000 con la Visa del Galicia': resuelve la tarjeta 1506."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con la Visa del Galicia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_8(datos):
    """'pagué el resumen de la tarjeta': explica que se hace desde la web, no registra nada."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pagué el resumen de la tarjeta"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        db = Session()
        txs = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalar()
        return f"{resp} | Txs creadas: {txs}"
    return run_isolated(test)

def p9_caso_9(datos):
    """'gasté 5000 con la tarjeta de débito': NO es crédito."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con la tarjeta de débito"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_10(datos):
    """Registrar un consumo con tarjeta y deshacerlo: verifica que se borren padre, grupo y cuotas."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 30000 con la Amex"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        # Deshacer
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        r_borra = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        r_conf = respuestas[-1][1] if respuestas else ""
        
        db = Session()
        padres = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)).scalar()
        return f"Propuesta deshacer:\n{r_borra}\nConfirmación:\n{r_conf}\nPadres restantes: {padres}"
    return run_isolated(test)

def p9_caso_11(datos):
    """Un usuario sin tarjetas dice 'gasté 5000 con la tarjeta': mensaje claro."""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE tarjetas_credito SET estado = 'archivada' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 con la tarjeta"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9_caso_12(datos):
    """'gasté 3 gambas en el remis': ya no debe interpretarse como 3.000."""
    def test(conn, Session, respuestas):
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 3 gambas en el remis"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        no_es_3000 = ("3.000" not in resp and "3000" not in resp)
        return f"No interpretado como 3000: {no_es_3000} | Respuesta: {resp}"
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 9B: Transferencias, Extracciones y Dólares (12 casos)
# ==============================================================================

def p9b_caso_1(datos):
    """pasé 10 mil de Galicia a Santander: crea transferencia, ajusta los dos saldos, cero gastos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 10 mil de Galicia a Santander"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        
        cero_gastos = (txs_ini == txs_fin)
        saldos_ok = (bg_fin == bg_ini - Decimal("10000") and bs_fin == bs_ini + Decimal("10000"))
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)

def p9b_caso_2(datos):
    """me transferí 20000 a Santander: pregunta el origen o usa la principal, según corresponda"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me transferí 20000 a Santander"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_3(datos):
    """saqué 50000 del cajero: transfiere de la cuenta al efectivo, cero gastos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        be_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo ARS", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "saqué 50000 del cajero"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        be_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo ARS", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        
        saldos_ok = (bg_fin == bg_ini - Decimal("50000") and be_fin == be_ini + Decimal("50000"))
        cero_gastos = (txs_ini == txs_fin)
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)

def p9b_caso_4(datos):
    """saqué 50000 del cajero con un usuario sin billetera de efectivo: mensaje claro, no registra"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND es_efectivo = true"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_ini = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "saqué 50000 del cajero"), time.perf_counter())
        msg = respuestas[-1][1] if respuestas else ""
        
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_fin = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        no_registro = (txs_ini == txs_fin and trs_ini == trs_fin)
        return f"Respuesta: {msg} | No registra: {no_registro}"
    return run_isolated(test)

def p9b_caso_5(datos):
    """compré 100 dólares a 1500: transfiere 150.000 pesos y suma 100 dólares"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 1500"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        
        saldos_ok = (bg_fin == bg_ini - Decimal("150000") and bu_fin == bu_ini + Decimal("100"))
        cero_gastos = (txs_ini == txs_fin)
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok} | Cero gastos: {cero_gastos}"
    return run_isolated(test)

def p9b_caso_6(datos):
    """compré 100 dólares: pregunta la cotización o los pesos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_7(datos):
    """vendí 50 dólares a 1450: transfiere al revés"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET saldo_actual = 100 WHERE usuario_id = :uid AND nombre = 'Efectivo USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "vendí 50 dólares a 1450"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()
        
        saldos_ok = (bu_fin == bu_ini - Decimal("50") and bg_fin == bg_ini + Decimal("72500"))
        return f"Propuesta: {prop} | Confirmación: {conf} | Saldos ajustados: {saldos_ok}"
    return run_isolated(test)

def p9b_caso_8(datos):
    """compré 100 dólares a 5: advierte que la cotización es absurda"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 5"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_9(datos):
    """le transferí 5000 a mi hermano: es un gasto, no una transferencia"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        txs_ini = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_ini = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "le transferí 5000 a mi hermano"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""
        
        txs_fin = s.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        trs_fin = s.execute(select(func.count(TransferenciaInterna.id)).where(TransferenciaInterna.usuario_id == u.id)).scalar()
        es_gasto = (txs_fin == txs_ini + 1 and trs_fin == trs_ini)
        return f"Propuesta: {prop} | Confirmación: {conf} | Es gasto: {es_gasto}"
    return run_isolated(test)

def p9b_caso_10(datos):
    """gasté 5000 en el kiosco: sigue siendo un gasto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_11(datos):
    """Registrar una transferencia y deshacerla: los dos saldos vuelven"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        
        # Transferir
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 10 mil de Galicia a Santander"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        
        # Deshacer
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        prop_undo = respuestas[-1][1] if respuestas else ""
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf_undo = respuestas[-1][1] if respuestas else ""
        
        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bs_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Santander", Billetera.usuario_id == u.id)).scalar()
        
        saldos_vuelven = (bg_ini == bg_fin and bs_ini == bs_fin)
        return f"Propuesta undo: {prop_undo} | Confirmación undo: {conf_undo} | Saldos intactos: {saldos_vuelven}"
    return run_isolated(test)

def p9b_caso_12(datos):
    """Transferencia con origen y destino iguales: se rechaza"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pasé 10 mil de Galicia a Galicia"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_13(datos):
    """compré 5 dólares y responder 7500: debe registrar 5 dólares a 1.500, no rechazar"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        s = Session()
        bg_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_ini = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 5 dólares"), time.perf_counter())
        r1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "7500"), time.perf_counter())
        r2 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        r3 = respuestas[-1][1] if respuestas else ""

        bg_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Galicia", Billetera.usuario_id == u.id)).scalar()
        bu_fin = s.execute(select(Billetera.saldo_actual).where(Billetera.nombre == "Efectivo USD", Billetera.usuario_id == u.id)).scalar()

        saldos_ok = (bu_fin == bu_ini + Decimal("5") and bg_fin == bg_ini - Decimal("7500"))
        return f"R1: {r1} | R2: {r2} | R3: {r3} | Saldos: {saldos_ok}"
    return run_isolated(test)

def p9b_caso_14(datos):
    """compré 100 dólares y responder 1500: cotización unitaria"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares"), time.perf_counter())
        r1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "1500"), time.perf_counter())
        r2 = respuestas[-1][1] if respuestas else ""

        return f"R1: {r1} | R2: {r2}"
    return run_isolated(test)

def p9b_caso_15(datos):
    """compré 100 dólares y responder 150000: monto total"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares"), time.perf_counter())
        r1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "150000"), time.perf_counter())
        r2 = respuestas[-1][1] if respuestas else ""

        return f"R1: {r1} | R2: {r2}"
    return run_isolated(test)

def p9b_caso_16(datos):
    """compré 100 dólares a 15: debe advertir, con la cotización de referencia en el mensaje"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 15"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p9b_caso_17(datos):
    """compré 100 dólares a 1500 con la tabla de cotizaciones vacía: no rechaza, pide confirmación"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("DELETE FROM cotizaciones_dolar"))

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré 100 dólares a 1500"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)


def p10_caso_1(datos):
    """empecé a pagar 5000 de Disney+: pregunta la frecuencia, crea la suscripción, no cobra nada"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "empecé a pagar 5000 de Disney+"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "mensual"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp3 = respuestas[-1][1] if respuestas else ""

        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre == "Disney+")).scalars().first()
        txs_sub = db.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id, Transaccion.suscripcion_id == sub.id)).scalar() if sub else 0

        return (
            f"Pregunta frecuencia: {'¿Con qué frecuencia' in resp1}\n"
            f"Propuesta: {'Voy a programar la suscripción a Disney+' in resp2}\n"
            f"Confirmación: {'Listo. Suscripción a Disney+' in resp3}\n"
            f"Sub creada: {sub is not None and sub.estado == EstadoSuscripcion.ACTIVA}\n"
            f"Txs cobro generadas: {txs_sub}"
        )
    return run_isolated(test)

def p10_caso_2(datos):
    """gasté 5000 en Disney+: sigue siendo un gasto suelto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en Disney+"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def _limpiar_subs(conn, u_id):
    conn.execute(text("DELETE FROM historial_suscripciones WHERE suscripcion_id IN (SELECT id FROM suscripciones WHERE usuario_id = :uid)"), {"uid": u_id})
    conn.execute(text("DELETE FROM suscripciones WHERE usuario_id = :uid"), {"uid": u_id})

def p10_caso_3(datos):
    """me suscribí a Netflix por 9000 por mes: crea con frecuencia mensual confirmada"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me suscribí a Netflix por 9000 por mes"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre == "Netflix")).scalars().first()
        return (
            f"Propuesta: {'Voy a programar la suscripción a Netflix: $9.000 mensual' in resp_prop}\n"
            f"Confirmación: {'Listo. Suscripción a Netflix' in resp_conf}\n"
            f"Frecuencia mensual: {sub is not None and sub.frecuencia == FrecuenciaSuscripcion.MENSUAL}"
        )
    return run_isolated(test)

def p10_caso_4(datos):
    """pagué el Spotify: pregunta si es gasto único o suscripción"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "pagué el Spotify"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p10_caso_5(datos):
    """me suscribí a ChatGPT por 20 dólares por mes con medio de pago en pesos: crea la suscripción en dólares"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me suscribí a ChatGPT por 20 dólares por mes"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        sub = db.execute(select(Suscripcion).where(Suscripcion.usuario_id == u.id, Suscripcion.nombre.ilike("%ChatGPT%"))).scalars().first()
        hist = db.execute(select(HistorialSuscripcion).where(HistorialSuscripcion.suscripcion_id == sub.id)).scalars().first() if sub else None
        bill = db.get(Billetera, sub.billetera_id) if sub and sub.billetera_id else None

        mon_val = hist.moneda.value if hist and hasattr(hist.moneda, "value") else (hist.moneda if hist else None)
        return (
            f"Propuesta: {'US$20' in resp_prop or 'USD 20' in resp_prop}\n"
            f"Sub creada: {sub is not None}\n"
            f"Moneda sub: {mon_val}\n"
            f"Medio de pago pesos: {bill.moneda == Moneda.ARS if bill else False}"
        )
    return run_isolated(test)

def p10_caso_6(datos):
    """di de baja Netflix sin tenerla: mensaje claro"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "di de baja Netflix"), time.perf_counter())
        return respuestas[-1][1] if respuestas else ""
    return run_isolated(test)

def p10_caso_7(datos):
    """di de baja la suscripción existente: confirma y la da de baja"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        data_create = SuscripcionCreate(
            nombre="Netflix",
            monto=Decimal("9000"),
            moneda="ARS",
            frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"),
            billetera_id=b_gal.id,
        )
        sub = suscripcion_service.crear_suscripcion(db, u.id, data_create)

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "di de baja Netflix"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        db.refresh(sub)
        return (
            f"Pregunta confirmación: {'¿Confirmás dar de baja la suscripción a Netflix' in resp_prop}\n"
            f"Confirmación: {'Listo, dimos de baja tu suscripción a Netflix.' in resp_conf}\n"
            f"Estado final: {sub.estado.value}"
        )
    return run_isolated(test)

def p10_caso_8(datos):
    """aumentó Netflix, ahora son 12000: muestra el precio anterior y el nuevo"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        data_create = SuscripcionCreate(
            nombre="Netflix",
            monto=Decimal("9000"),
            moneda="ARS",
            frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"),
            billetera_id=b_gal.id,
        )
        sub = suscripcion_service.crear_suscripcion(db, u.id, data_create)

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aumentó Netflix, ahora son 12000"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        precio_vigente = suscripcion_service.obtener_precio_vigente(db, sub.id)
        return (
            f"Propuesta muestra ambos: {'de $9.000 a $12.000' in resp_prop}\n"
            f"Confirmación: {'actualicé el precio de Netflix a $12.000' in resp_conf}\n"
            f"Precio en base: {precio_vigente.monto if precio_vigente else None}"
        )
    return run_isolated(test)

def p10_caso_9(datos):
    """cuánto gasto en suscripciones: lista y total mensual"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        _limpiar_subs(conn, u.id)
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Netflix", monto=Decimal("9000"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))
        suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Spotify", monto=Decimal("3500"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasto en suscripciones"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        sin_saldos_billetera = "Galicia" not in resp and "saldo" not in resp.lower()
        return (
            f"Lista activa: {'Netflix' in resp and 'Spotify' in resp}\n"
            f"Total mensual: {'$12.500' in resp}\n"
            f"Sin saldos billetera: {sin_saldos_billetera}"
        )
    return run_isolated(test)

def p10_caso_10(datos):
    """Registrar un gasto igual a una suscripción ya cobrada este período: avisa antes"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        sub = suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Netflix", monto=Decimal("9000"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))
        tx_cobro = Transaccion(
            usuario_id=u.id,
            billetera_id=b_gal.id,
            tipo=TipoTransaccion.EGRESO,
            monto=Decimal("9000"),
            moneda=Moneda.ARS,
            descripcion="Netflix",
            fecha=hoy_argentina(),
            origen=OrigenTransaccion.RECURRENTE,
            estado_verificacion=EstadoVerificacionTransaccion.CONFIRMADA,
            suscripcion_id=sub.id,
        )
        db.add(tx_cobro)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 9000 en Netflix"), time.perf_counter())
        resp_aviso = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "es un gasto aparte"), time.perf_counter())
        resp_reg = respuestas[-1][1] if respuestas else ""

        return (
            f"Aviso cobro previo: {'ya se cobró automáticamente' in resp_aviso and 'este período' in resp_aviso}\n"
            f"Registro tras confirmación: {'registrado' in resp_reg}"
        )
    return run_isolated(test)

def p10_caso_11(datos):
    """Crear una suscripción de un servicio que ya tiene activa: avisa"""
    from app.utils.fecha import hoy_argentina
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_gal = db.execute(select(Billetera).where(Billetera.usuario_id == u.id, Billetera.nombre == "Galicia")).scalars().first()
        suscripcion_service.crear_suscripcion(db, u.id, SuscripcionCreate(
            nombre="Netflix", monto=Decimal("9000"), moneda="ARS", frecuencia="mensual",
            proximo_cobro=suscripcion_service.calcular_siguiente_cobro(hoy_argentina(), "mensual"), billetera_id=b_gal.id
        ))

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "me suscribí a Netflix por 9000 por mes"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        return (
            f"Aviso existente: {'Ya tenés una suscripción activa a Netflix' in resp and '¿Querés registrar otra igual o te referías a la existente?' in resp}"
        )
    return run_isolated(test)

def p10_caso_12(datos):
    """Un servicio que no está en el catálogo: lo acepta igual"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "empecé a pagar 15000 del Gimnasio del barrio por mes"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        return (
            f"Propuesta servicio no-catálogo: {'Voy a programar la suscripción a Gimnasio del barrio: $15.000 mensual' in resp}"
        )
    return run_isolated(test)


# ==============================================================================
# RUNNER GENERAL DE SUITE
# ==============================================================================



# ==============================================================================
# ESCENARIOS PUNTO 11: Lotes y Multi-Operación (10 casos)
# ==============================================================================

def p11_caso_1(datos):
    """Dos gastos con billeteras distintas nombradas explícitamente"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco con Galicia y 8000 en la verdulería con Santander"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        ok_prop = "Listo," in resp_prop and "5.000 en Kiosco desde Galicia" in resp_prop and "8.000 en Verdulería desde Santander" in resp_prop
        ok_conf = (resp_conf.strip() == "Ya quedó anotado. Si hay algo mal, decime qué corregir.")
        return (
            f"Propuesta: {ok_prop} | "
            f"Confirmacion: {ok_conf}"
        )
    return run_isolated(test)

def p11_caso_2(datos):
    """Dos gastos sin billetera, con el usuario teniendo principal"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Propuesta principal: {'2 movimientos desde Galicia:\n$5.000 en Kiosco\n$8.000 en Verdulería' in resp_prop and 'Si fue con otra, decime cuál.' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_3(datos):
    """Dos gastos sin billetera, sin principal: pregunta una vez"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        resp_preg = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Pregunta una vez: {'¿Desde qué billetera salieron los gastos?' in resp_preg} | "
            f"Propuesta resuelta: {'2 movimientos desde Galicia:\n$5.000 en Kiosco\n$8.000 en Verdulería' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_4(datos):
    """Tres gastos donde solo uno nombra billetera"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco con Galicia, 3000 en la panadería y 4000 en la verdulería"), time.perf_counter())
        resp_preg = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "3"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Pregunta faltantes: {'¿Desde qué billetera salieron los gastos?' in resp_preg} | "
            f"Propuesta mixta: {'5.000 en Kiosco desde Galicia' in resp_prop and 'Santander' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_5(datos):
    """Un gasto y un ingreso en el mismo mensaje"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cobré 100000 de sueldo y pagué 30000 de alquiler"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""
        ok_prop = "Listo," in resp_prop and "+$100.000" in resp_prop and "-$30.000" in resp_prop
        ok_conf = (resp_conf.strip() == "Ya quedó anotado. Si hay algo mal, decime qué corregir.")
        return (
            f"Propuesta signos: {ok_prop} | "
            f"Confirmacion signos: {ok_conf}"
        )
    return run_isolated(test)

def p11_caso_6(datos):
    """Un lote con un consumo de tarjeta y un gasto normal"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco con Galicia y 30000 en zapatillas con la Amex"), time.perf_counter())
        resp_prop = respuestas[-1][1] if respuestas else ""
        return (
            f"Lote tarjeta y gasto: {'$5.000 en Kiosco desde Galicia' in resp_prop and 'con tarjeta •••• 2745' in resp_prop}"
        )
    return run_isolated(test)

def p11_caso_7(datos):
    """Doce movimientos: rechaza con mensaje claro"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        msg_12 = "gasté 100 en kiosco, 200 en pan, 300 en leche, 400 en carne, 500 en verdura, 600 en cafe, 700 en taxi, 800 en bar, 900 en cena, 1000 en cine, 1100 en nafta y 1200 en peaje"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, msg_12), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        return (
            f"Rechazo tope: {'El límite es de 10 movimientos por mensaje' in resp and 'web de Argentum' in resp}"
        )
    return run_isolated(test)

def p11_caso_8(datos):
    """Un lote donde una operación está en otra moneda sin billetera de esa moneda"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 50 dólares en un libro"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        return (
            f"Aviso descarte y propuesta: {'No se pudo registrar' in resp and 'dólares' in resp and 'Listo. $5.000 en Kiosco desde Galicia' in resp}"
        )
    return run_isolated(test)

def p11_caso_9(datos):
    """Un lote con dos movimientos idénticos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 5000 en el kiosco"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        return (
            f"Deteccion duplicado interno: {'Mandaste 2 movimientos iguales de $5.000 en Kiosco' in resp and '¿Son dos gastos distintos o se te repitió?' in resp}"
        )
    return run_isolated(test)

def p11_caso_10(datos):
    """Un lote seguido de 'borrá eso'"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        resp_prop_undo = respuestas[-1][1] if respuestas else ""
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf_undo = respuestas[-1][1] if respuestas else ""
        return (
            f"Propuesta deshacer lote: {'eliminar los 2 movimientos' in resp_prop_undo} | "
            f"Confirmacion deshacer lote: {'Listo, 2 movimientos eliminados.' in resp_conf_undo}"
        )
    return run_isolated(test)

def p11_caso_11(datos):
    """Lote de 3 gastos en un solo mensaje: confirma, crea 3 txs, valida accion_ejecutada > 100 caracteres"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Enviar mensaje con 3 gastos
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco, 8000 en la verdulería y 3000 en la panadería"),
            time.perf_counter()
        )

        # Confirmar con "sí"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "sí"),
            time.perf_counter()
        )
        resp_final = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        tx_creadas = tx_despues - tx_antes

        row = conn.execute(
            text("SELECT accion_ejecutada, length(accion_ejecutada) as largo FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        accion_len = row["largo"] if row and row["largo"] is not None else 0

        return (
            f"Txs creadas: {tx_creadas} | "
            f"Accion len ok: {accion_len > 100} | "
            f"Sin error: {'Hubo un problema' not in resp_final}"
        )
    return run_isolated(test)

# ==============================================================================
# ESCENARIOS PUNTO 12: Consultas y Dashboard (Balance y Cotización)
# ==============================================================================

def p12_caso_1(datos):
    """consultar_balance: 'cuál es mi balance' detecta intent y devuelve balance real del dashboard"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi balance"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        tiene_datos_reales = "En este ciclo llevás ingresados" in resp and "(balance:" in resp
        return f"Intent: {intent} | Datos reales: {tiene_datos_reales}"
    return run_isolated(test)

def p12_caso_2(datos):
    """consultar_cotizacion: 'a cuánto está el dólar' detecta intent y devuelve cotizaciones reales con mock"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    mock_cotizaciones = {
        "cotizaciones": {
            "blue": {"venta": 1450.0},
            "oficial": {"venta": 1050.0},
            "mep": {"venta": 1400.0},
        }
    }
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value=mock_cotizaciones):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "a cuánto está el dólar"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        tiene_cotizacion = (
            "Cotizaciones del dólar:" in resp
            and "Dólar Blue: $1.450" in resp
            and "MEP: $1.400" in resp
            and "Oficial: $1.050" in resp
        )
        return f"Intent: {intent} | Cotizacion fija ok: {tiene_cotizacion}"
    return run_isolated(test)

def p12_caso_3(datos):
    """consultar_saldo: 'cuánto tengo' detecta intent y devuelve saldo real del dashboard"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto tengo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.dashboard_service import get_dashboard_resumen
            from app.routers.whatsapp.parsers import _fmt
            resumen = get_dashboard_resumen(db, u)
            disp = resumen["disponible_real"]
            ars_total_str = _fmt(disp["ars"]["saldo_billeteras"])
            ars_disp_str = _fmt(disp["ars"]["disponible"])
            datos_reales = (
                "LLM_INVENTADO_999" not in resp
                and ars_total_str in resp
                and ars_disp_str in resp
            )
            if disp["usd"]["saldo_billeteras"] > 0 or disp["usd"]["disponible"] > 0:
                usd_tot_str = _fmt(disp["usd"]["saldo_billeteras"], Moneda.USD)
                usd_disp_str = _fmt(disp["usd"]["disponible"], Moneda.USD)
                datos_reales = datos_reales and (usd_tot_str in resp) and (usd_disp_str in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Datos reales: {datos_reales}"
    return run_isolated(test)

def p12_caso_4(datos):
    """consultar_proyeccion: 'cuál es mi proyección financiera' detecta intent y devuelve proyección real"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi proyección financiera"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_msg = "No pude calcular tu proyección en este momento. Probá de nuevo en unos minutos."
        proyeccion_ok = bool(resp) and ("LLM_INVENTADO_999" not in resp) and (falla_msg not in resp)
        return f"Intent: {intent} | Proyeccion ok: {proyeccion_ok}"
    return run_isolated(test)

def p12_caso_5(datos):
    """consultar_saldo: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.contexto_financiero_service._calcular_saldo_disponible_sync", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto tengo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tu saldo en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p12_caso_6(datos):
    """consultar_balance: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dashboard_service.calcular_balance_ciclo", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi balance mensual"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude calcular tu balance en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p12_caso_7(datos):
    """consultar_proyeccion: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.proyeccion_service.calcular_proyeccion", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuál es mi proyección financiera"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude calcular tu proyección en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p12_caso_8(datos):
    """consultar_cotizacion: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dolar_service.get_cotizaciones_dolar", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "a cuánto cotiza el dólar hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude obtener la cotización del dólar en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p12_caso_9(datos):
    """consultar_cotizacion: cotizaciones vacías devuelve mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.dolar_service.get_cotizaciones_dolar", return_value={"cotizaciones": {}}):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "a cuánto cotiza el dólar hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude obtener la cotización del dólar en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p12_caso_10(datos):
    """consultar_meta: 'cómo va mi meta' detecta intent y devuelve metas reales"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi meta"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.ai_service import construir_contexto_financiero
            from app.routers.whatsapp.parsers import _fmt
            ctx = construir_contexto_financiero(u, db)
            metas = sorted(ctx.get("metas_activas", []), key=lambda x: x["nombre"])
            if not metas:
                datos_reales = (resp == "No tenés metas activas.") and ("LLM_INVENTADO_999" not in resp)
            else:
                datos_reales = ("LLM_INVENTADO_999" not in resp) and resp.startswith("Tus metas activas:")
                for m in metas[:8]:
                    mon = Moneda.USD if m["moneda"] == "USD" else Moneda.ARS
                    obj_str = _fmt(m["objetivo"], mon)
                    acum_str = _fmt(m["acumulado"], mon)
                    datos_reales = datos_reales and (m["nombre"] in resp) and (obj_str in resp) and (acum_str in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Datos reales: {datos_reales}"
    return run_isolated(test)

def p12_caso_11(datos):
    """consultar_presupuesto: 'cómo va mi presupuesto' detecta intent y devuelve presupuestos reales"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi presupuesto"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.ai_service import construir_contexto_financiero
            from app.routers.whatsapp.parsers import _fmt
            ctx = construir_contexto_financiero(u, db)
            presupuestos = sorted(ctx.get("presupuestos_activos", []), key=lambda x: x["nombre"])
            if not presupuestos:
                datos_reales = (resp == "No tenés presupuestos activos.") and ("LLM_INVENTADO_999" not in resp)
            else:
                datos_reales = ("LLM_INVENTADO_999" not in resp) and resp.startswith("Tus presupuestos activos:")
                for p in presupuestos[:8]:
                    mon = Moneda.USD if p["moneda"] == "USD" else Moneda.ARS
                    lim_str = _fmt(p["limite"], mon)
                    usado_str = _fmt(p["monto_usado"], mon)
                    datos_reales = datos_reales and (p["nombre"] in resp) and ("usaste" in resp) and (usado_str in resp) and (lim_str in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Datos reales: {datos_reales}"
    return run_isolated(test)

def p12_caso_12(datos):
    """consultar_meta: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.contexto_financiero_service._resumen_metas_activas_sync", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi meta"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tus metas en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p12_caso_13(datos):
    """consultar_presupuesto: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.contexto_financiero_service._resumen_presupuestos_activos_sync", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cómo va mi presupuesto"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tus presupuestos en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)

def p13_caso_1(datos):
    """consultar_gastos: 'cuánto gasté hoy' detecta intent y calcula gastos de hoy"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services import gastos_consulta_service
            from app.routers.whatsapp.gastos import _formatear_respuesta_gastos
            from app.utils.fecha import hoy_argentina
            hoy = hoy_argentina()
            res = gastos_consulta_service.calcular_gastos_periodo(db, u.id, hoy, hoy, top_n=3)
            msg_esp = _formatear_respuesta_gastos(
                "Hoy", None, False,
                float(res["ars"]["total"]), res["ars"]["cantidad"],
                float(res["usd"]["total"]), res["usd"]["cantidad"],
                res["top_categorias_ars"]
            )
            resp_ok = (resp == msg_esp) and ("LLM_INVENTADO_999" not in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Respuesta ok: {resp_ok}"
    return run_isolated(test)

def p13_caso_2(datos):
    """consultar_gastos: 'cuánto gasté este ciclo' coincide con balance de ciclo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté este ciclo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        db = Session()
        try:
            from app.services.dashboard_service import calcular_balance_ciclo
            from app.routers.whatsapp.parsers import _fmt
            from app.models.usuario import Moneda
            bal = calcular_balance_ciclo(db, u)
            egr_ars = bal["ars"]["egresos"]
            egr_usd = bal["usd"]["egresos"]
            sin_marcador = "LLM_INVENTADO_999" not in resp
            if egr_ars == 0 and egr_usd == 0:
                coincide = (resp == "En este ciclo no registraste gastos.") and sin_marcador
            else:
                coincide = resp.startswith("En este ciclo gastaste ") and sin_marcador
                if egr_ars > 0:
                    coincide = coincide and (_fmt(egr_ars) in resp)
                if egr_usd > 0:
                    coincide = coincide and (_fmt(egr_usd, Moneda.USD) in resp)
        finally:
            db.close()
        return f"Intent: {intent} | Coincide con balance: {coincide}"
    return run_isolated(test)

def p13_caso_3(datos):
    """consultar_gastos: 'cuánto gasté en pizza esta semana' filtra por descripción"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté en pizza esta semana"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        sin_marcador = "LLM_INVENTADO_999" not in resp
        ok = (
            (resp.startswith("Esta semana gastaste ") and resp.endswith(" en «pizza»."))
            or (resp == "Esta semana no encontré gastos que mencionen «pizza».")
        ) and sin_marcador
        return f"Intent: {intent} | Filtro por descripcion: {ok}"
    return run_isolated(test)

def p13_caso_4(datos):
    """consultar_gastos: 'cuánto gasté en supermercado el mes pasado' filtra por catálogo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté en supermercado el mes pasado"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        sin_marcador = "LLM_INVENTADO_999" not in resp
        ok = (
            (resp.startswith("El mes pasado gastaste ") and resp.endswith(" en Supermercado."))
            or (resp == "El mes pasado no registraste gastos en Supermercado.")
        ) and sin_marcador
        return f"Intent: {intent} | Filtro por catalogo: {ok}"
    return run_isolated(test)

def p13_caso_5(datos):
    """consultar_gastos: falla de servicio maneja error con mensaje amigable"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        respuestas.clear()
        with patch("app.services.gastos_consulta_service.calcular_gastos_periodo", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cuánto gasté hoy"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""
        row = conn.execute(
            text("SELECT intent_detectado FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()
        intent = row["intent_detectado"] if row else None
        falla_esperada = "No pude consultar tus gastos en este momento. Probá de nuevo en unos minutos."
        falla_ok = (resp == falla_esperada) and ("LLM_INVENTADO_999" not in resp)
        return f"Intent: {intent} | Falla manejada: {falla_ok}"
    return run_isolated(test)


# ==============================================================================
# ESCENARIOS PUNTO 14: Registro Directo WhatsApp (14 casos)
# ==============================================================================

def p14_caso_1(datos):
    """P14.1 'gasté 5000 en el kiosco': registro directo en el acto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        
        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        db.refresh(b_db)
        saldo_diff = b_db.saldo_actual - s0

        row = conn.execute(
            text("SELECT id, accion_ejecutada FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()

        tx_nueva = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP).order_by(Transaccion.id.desc())
        ).scalars().first()

        accion_ok = (row and tx_nueva and row["accion_ejecutada"] == str(tx_nueva.id))

        from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        resp_ok = resp.startswith("Listo.") and ("¿Va?" not in resp)

        return (
            f"Resp ok: {resp_ok} | Txs: {txs_creadas} | Saldo: {saldo_diff} | "
            f"Accion ejecutada: {accion_ok} | Propuesta pendiente: {prop_pendiente is None}"
        )
    return run_isolated(test)


def p14_caso_2(datos):
    """P14.2 lote de 2: registra directo 2 movimientos"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 8000 en la verdulería"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        row = conn.execute(
            text("SELECT accion_ejecutada FROM conversaciones_wpp WHERE usuario_id = :uid ORDER BY fecha DESC, id DESC LIMIT 1"),
            {"uid": u.id}
        ).mappings().first()

        accion_val = row["accion_ejecutada"] if row else ""
        es_lote_accion = accion_val.startswith("lote:") and len(accion_val.replace("lote:", "").split(",")) == 2
        resp_ok = resp.startswith("Listo, 2 movimientos")

        return f"Resp ok: {resp_ok} | Txs: {txs_creadas} | Accion lote: {es_lote_accion}"
    return run_isolated(test)


def p14_caso_3(datos):
    """P14.3 ingreso simple: registra en el acto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "cobré 800000 de sueldo"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        db.refresh(b_db)
        saldo_diff = b_db.saldo_actual - s0
        resp_ok = resp.startswith("Listo.") and ("¿Va?" not in resp)

        return f"Resp ok: {resp_ok} | Txs: {txs_creadas} | Saldo: +{saldo_diff}"
    return run_isolated(test)


def p14_caso_4(datos):
    """P14.4 compra con tarjeta de crédito en cuotas: NO registra directo, pide confirmación"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "compré una tele en 12 cuotas de 80000"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_intermedio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_si = tx_intermedio - tx_antes

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues_si = tx_final - tx_intermedio

        pide_conf = ("¿Va?" in resp1 or "¿Confirmás?" in resp1 or "Voy a anotar" in resp1)
        return (
            f"Pide conf: {pide_conf} | Txs antes de si: {txs_antes_si} | "
            f"Txs despues de si: {txs_despues_si > 0} | Confirma ok: {'Listo' in resp2}"
        )
    return run_isolated(test)


def p14_caso_5(datos):
    """P14.5 el mismo gasto dos veces seguidas: el segundo pregunta por duplicado y no registra"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Primer gasto -> registro directo
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Segundo gasto idéntico -> detección duplicado
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp_dup = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        pregunta_dup = "¿Es un movimiento nuevo o se te repitió?" in resp_dup or "ya registraste" in resp_dup
        txs_totales = tx_final - tx_antes

        return f"Pregunta duplicado: {pregunta_dup} | Txs totales: {txs_totales}"
    return run_isolated(test)


def p14_caso_6(datos):
    """P14.6 confianza 0.70: resultado idéntico a HEAD y 0 transacciones"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        resp_head_esperada = "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nSi fue con otra, decime cuál."
        coincide_head = (resp.strip() == resp_head_esperada.strip())

        return f"Coincide con HEAD: {coincide_head} | Txs creadas: {txs_creadas}"
    return run_isolated(test)


def p14_caso_7(datos):
    """P14.7 'sí' justo después de un registro directo: mensaje claro y 0 transacciones nuevas"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        # Registro directo
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Usuario manda "sí"
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_si = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_nuevas = tx_final - tx_medio

        esperado = "Ya quedó anotado. Si hay algo mal, decime qué corregir."
        resp_ok = (resp_si.strip() == esperado.strip())

        return f"Resp ok: {resp_ok} | Txs nuevas: {txs_nuevas}"
    return run_isolated(test)


def p14_caso_8(datos):
    """P14.8 registro directo, 'deshacer' y 'sí': borra transacción y restaura saldo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Registro directo
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        # Deshacer
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "deshacer"), time.perf_counter())
        resp_deshacer = respuestas[-1][1] if respuestas else ""

        # Sí
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        db.refresh(b_db)

        txs_restantes = tx_despues - tx_antes
        saldo_restaurado = (b_db.saldo_actual == s0)
        conf_ok = "Listo" in resp_conf and ("eliminad" in resp_conf or "borrad" in resp_conf or "anulad" in resp_conf)

        return f"Deshacer ok: {conf_ok} | Txs restantes: {txs_restantes} | Saldo restaurado: {saldo_restaurado}"
    return run_isolated(test)


def p14_caso_9(datos):
    """P14.9 registro directo y 'eran 3000 no 5000': corrige monto y saldo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual

        # Registro directo de 5000
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())

        # Corregir monto a 3000
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "eran 3000 no 5000"), time.perf_counter())
        resp_corr = respuestas[-1][1] if respuestas else ""

        # Sí a la propuesta de corrección
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf = respuestas[-1][1] if respuestas else ""

        db.refresh(b_db)
        tx = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.origen == OrigenTransaccion.IA_WPP)
        ).scalars().first()

        monto_corregido = (tx.monto == Decimal("3000")) if tx else False
        saldo_ok = (b_db.saldo_actual == s0 - Decimal("3000"))

        return f"Monto corregido: {monto_corregido} | Saldo ok: {saldo_ok} | Confirmacion: {'Listo' in resp_conf}"
    return run_isolated(test)


def p14_caso_10(datos):
    """P14.10 falla forzada: rollback limpio, 0 transacciones, saldo intacto, ninguna propuesta y sí posterior inocuo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        b_db = db.get(Billetera, b_gal.id)
        s0 = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        with patch("app.routers.whatsapp_ia._confirmar_propuesta_transaccion", side_effect=RuntimeError("boom")):
            _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        sin_listo = "Listo" not in resp1
        db.refresh(b_db)
        saldo_intacto = (b_db.saldo_actual == s0)

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_medio - tx_antes

        from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        # Enviar "sí" posterior
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues_si = tx_final - tx_medio

        return (
            f"Sin listo: {sin_listo} | Txs creadas: {txs_creadas} | Saldo intacto: {saldo_intacto} | "
            f"Propuesta pendiente: {prop_pendiente is None} | Txs despues si: {txs_despues_si}"
        )
    return run_isolated(test)


def p14_caso_11(datos):
    """P14.11 idempotencia: el mismo wamid dos veces resulta en 1 sola transacción"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        fixed_wamid = f"wamid_idemp_{uuid.uuid4().hex[:12]}"
        payload = make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco", wamid=fixed_wamid)

        # Primer envío
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

        # Segundo envío idéntico con el mismo wamid
        _procesar_webhook_whatsapp_sync(payload, time.perf_counter())

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        return f"Txs creadas: {txs_creadas}"
    return run_isolated(test)


def p14_caso_12(datos):
    """P14.12 billetera ambigua: pregunta cuál; al responder registra en el acto"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = false WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        # Primer mensaje sin principal -> pregunta billetera
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco"), time.perf_counter())
        resp_preg = respuestas[-1][1] if respuestas else ""

        tx_medio = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_eleccion = tx_medio - tx_antes

        # Responder eligiendo opción 2 (Galicia) -> registro directo
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp_reg = respuestas[-1][1] if respuestas else ""

        tx_final = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues_eleccion = tx_final - tx_medio

        b_db = db.get(Billetera, b_gal.id)
        db.refresh(b_db)

        pregunta_ok = "¿Desde qué billetera" in resp_preg
        registro_ok = resp_reg.startswith("Listo.") and ("¿Va?" not in resp_reg)

        return (
            f"Pregunta ok: {pregunta_ok} | Txs antes eleccion: {txs_antes_eleccion} | "
            f"Registro ok: {registro_ok} | Txs creadas: {txs_despues_eleccion}"
        )
    return run_isolated(test)


def p14_caso_14(datos):
    """P14.14 lote con todos los ítems inválidos: mensaje claro, 0 transacciones y sin propuesta"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 50 dólares en un libro y 20 dólares en café"),
            time.perf_counter()
        )
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        msg_esperado = "No se puede registrar ningún movimiento." in resp

        return f"Msg ok: {msg_esperado} | Txs creadas: {txs_creadas} | Propuesta pendiente: {prop_pendiente is None}"
    return run_isolated(test)


def p14_caso_15(datos):
    """P14.15 lote con un ítem inválido y uno válido: registra el válido con aviso previo al Listo"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE billeteras SET estado = 'archivada' WHERE usuario_id = :uid AND moneda = 'USD'"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(
            make_payload(TELEFONO_TEST, "gasté 5000 en el kiosco y 50 dólares en un libro"),
            time.perf_counter()
        )
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues - tx_antes

        aviso_descarte = "No se pudo registrar" in resp and "dólares" in resp
        listo_ok = "Listo." in resp and "¿Va?" not in resp
        orden_ok = resp.find("No se pudo registrar") < resp.find("Listo.") if (aviso_descarte and listo_ok) else False

        return f"Aviso descarte: {aviso_descarte} | Listo ok: {listo_ok} | Orden ok: {orden_ok} | Txs: {txs_creadas}"
    return run_isolated(test)


def p14_caso_13(datos):
    """P14.13 comprobante imagen con billetera pendiente: tras elegir billetera pide confirmación (0 txs) y recién con sí registra (1 tx)"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        conv_sf = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_sf_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="comprobante",
            tipo_mensaje=TipoMensajeWpp.IMAGEN,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "origen_imagen": True},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "origen_imagen": True},
            confianza=Decimal("0.900"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_sf)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_despues_menu = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_si = tx_despues_menu - tx_antes

        pide_conf = ("¿Va?" in resp1 or "¿Confirmás?" in resp1) and "Listo." not in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_despues_si = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues = tx_despues_si - tx_antes
        confirma_ok = "Listo." in resp2

        return f"Pide conf: {pide_conf} | Txs antes si: {txs_antes_si} | Txs despues si: {txs_despues} | Confirma ok: {confirma_ok}"
    return run_isolated(test)


def p14_caso_16(datos):
    """P14.16 confianza baja con billetera pendiente: tras elegir billetera pide confirmación (0 txs) y recién con sí registra (1 tx)"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        conv_sf = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_sf_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "confianza_baja": True},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"], "confianza_baja": True},
            confianza=Decimal("0.900"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_sf)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_despues_menu = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_antes_si = tx_despues_menu - tx_antes

        pide_conf = ("¿Va?" in resp1 or "¿Confirmás?" in resp1) and "Listo." not in resp1

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp2 = respuestas[-1][1] if respuestas else ""

        tx_despues_si = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_despues = tx_despues_si - tx_antes
        confirma_ok = "Listo." in resp2

        return f"Pide conf: {pide_conf} | Txs antes si: {txs_antes_si} | Txs despues si: {txs_despues} | Confirma ok: {confirma_ok}"
    return run_isolated(test)


def p14_caso_17(datos):
    """P14.17 control sin marcas con billetera pendiente: tras elegir billetera registra directo (1 tx)"""
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        conv_sf = ConversacionWpp(
            usuario_id=u.id,
            wamid=f"wamid_sf_{uuid.uuid4().hex[:8]}",
            mensaje_usuario="gasté 5000 en el kiosco",
            tipo_mensaje=TipoMensajeWpp.TEXTO,
            mensaje_bot="¿Desde qué billetera salió la plata?\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            intent_detectado="slot_filling",
            entidades={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            slot_filling_activo=True,
            slot_filling_estado={"monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "datos_faltantes": ["billetera_origen"]},
            confianza=Decimal("0.900"),
            fecha=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        db.add(conv_sf)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "2"), time.perf_counter())
        resp1 = respuestas[-1][1] if respuestas else ""

        tx_despues_menu = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        txs_creadas = tx_despues_menu - tx_antes

        registra_directo = "Listo." in resp1 and ("¿Va?" not in resp1 and "¿Confirmás?" not in resp1)

        return f"Registra directo: {registra_directo} | Txs: {txs_creadas}"
    return run_isolated(test)


# --- CASOS PUNTO 16 (Aporte a metas) ---

GRABACIONES_P16 = {
    "P16.1": {
        "escenario_id": "P16.1",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 15000 a mi meta Fondo de Emergencia",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 15000,
                        "moneda": "ARS",
                        "meta": "Fondo de Emergencia",
                        "descripcion": "Fondo de Emergencia",
                        "billetera": None,
                        "billetera_origen": None,
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $15.000 a tu meta 'Fondo de Emergencia' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P16.2": {
        "escenario_id": "P16.2",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 10000 a la meta Vacaciones",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 10000,
                        "moneda": "ARS",
                        "meta": "Vacaciones",
                        "descripcion": "Vacaciones",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Tenés más de una meta que coincide con 'Vacaciones'. ¿A cuál te referís?",
                },
            }
        ],
    },
    "P16.3": {
        "escenario_id": "P16.3",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 5000 a la meta Casa en la playa",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 5000,
                        "moneda": "ARS",
                        "meta": "Casa en la playa",
                        "descripcion": "Casa en la playa",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "No encontré ninguna meta con el nombre 'Casa en la playa'.",
                },
            }
        ],
    },
    "P16.4": {
        "escenario_id": "P16.4",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 5000 a la meta Curso de Inglés",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 5000,
                        "moneda": "ARS",
                        "meta": "Curso de Inglés",
                        "descripcion": "Curso de Inglés",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $5.000 a tu meta 'Curso de Inglés' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P16.5": {
        "escenario_id": "P16.5",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 15000 a mi meta Fondo de Emergencia",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 15000,
                        "moneda": "ARS",
                        "meta": "Fondo de Emergencia",
                        "descripcion": "Fondo de Emergencia",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $15.000 a tu meta 'Fondo de Emergencia' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
    "P16.6": {
        "escenario_id": "P16.6",
        "llamadas": [
            {
                "call_idx": 0,
                "mensaje": "aportar 15000 a mi meta Fondo de Emergencia",
                "respuesta": {
                    "intent": "aportar_meta",
                    "entidades": {
                        "monto": 15000,
                        "moneda": "ARS",
                        "meta": "Fondo de Emergencia",
                        "descripcion": "Fondo de Emergencia",
                    },
                    "confianza": 0.95,
                    "slot_filling": False,
                    "datos_faltantes": [],
                    "respuesta_usuario": "Voy a aportar $15.000 a tu meta 'Fondo de Emergencia' desde Galicia. ¿Confirmás?",
                },
            }
        ],
    },
}


def p16_caso_1(datos):
    """P16.1 Aporte simple con meta y monto claros: propone, confirma, aumenta monto_actual y crea tx"""
    from app.models.meta import Meta
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        meta = db.execute(select(Meta).where(Meta.usuario_id == u.id, Meta.nombre == "Fondo de Emergencia")).scalar_one()
        m_ini = meta.monto_actual
        b_db = db.get(Billetera, b_gal.id)
        s_ini = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 15000 a mi meta Fondo de Emergencia"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""

        db.refresh(meta)
        db.refresh(b_db)
        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        tx_nueva = db.execute(
            select(Transaccion).where(Transaccion.usuario_id == u.id, Transaccion.movimiento_meta_id.is_not(None)).order_by(Transaccion.fecha_creacion.desc())
        ).scalars().first()

        prop_ok = "¿Confirmás?" in prop and "15.000" in prop and "Fondo de Emergencia" in prop
        conf_ok = "Listo." in conf and "Aporté $15.000" in conf and "Fondo de Emergencia" in conf
        monto_sumado = int(meta.monto_actual - m_ini)
        tx_ok = (tx_despues == tx_antes + 1) and (tx_nueva.descripcion == "Aporte a la meta: Fondo de Emergencia") and (tx_nueva.movimiento_meta_id is not None)
        saldo_ok = (b_db.saldo_actual == s_ini - Decimal("15000"))

        return f"Propuesta ok: {prop_ok} | Confirmado ok: {conf_ok} | Monto sumado: {monto_sumado} | Tx desc ok: {tx_ok} | Saldo Galicia ok: {saldo_ok}"
    return run_isolated(test)


def p16_caso_2(datos):
    """P16.2 Meta ambigua: pregunta cuál antes de proponer"""
    from app.models.meta import Meta
    from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        m1 = Meta(usuario_id=u.id, nombre="Vacaciones Brasil", monto_objetivo=100000, moneda="ARS", monto_actual=0, estado="activa")
        m2 = Meta(usuario_id=u.id, nombre="Vacaciones Bariloche", monto_objetivo=100000, moneda="ARS", monto_actual=0, estado="activa")
        db.add_all([m1, m2])
        db.commit()

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 10000 a la meta Vacaciones"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)
        es_ambigua = "Encontré más de una meta parecida:" in resp and "Vacaciones Bariloche" in resp and "Vacaciones Brasil" in resp

        return f"Pregunta ambigua: {es_ambigua} | Txs creadas: {tx_despues - tx_antes} | Sin propuesta pendiente: {prop_pendiente is None}"
    return run_isolated(test)


def p16_caso_3(datos):
    """P16.3 Meta inexistente: mensaje claro, no propone nada"""
    from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 5000 a la meta Casa en la playa"), time.perf_counter())
        resp = respuestas[-1][1] if respuestas else ""

        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)
        msg_claro = "No encontré ninguna meta con el nombre 'Casa en la playa'" in resp

        return f"Mensaje claro: {msg_claro} | Txs creadas: {tx_despues - tx_antes} | Sin propuesta pendiente: {prop_pendiente is None}"
    return run_isolated(test)


def p16_caso_4(datos):
    """P16.4 Monto que completa o supera el objetivo: confirma y felicita"""
    from app.models.meta import Meta
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        m_curso = Meta(usuario_id=u.id, nombre="Curso de Inglés", monto_objetivo=10000, moneda="ARS", monto_actual=8000, estado="activa")
        db.add(m_curso)
        db.commit()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 5000 a la meta Curso de Inglés"), time.perf_counter())
        prop = respuestas[-1][1] if respuestas else ""

        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        conf = respuestas[-1][1] if respuestas else ""

        db.refresh(m_curso)
        prop_ok = "¿Confirmás?" in prop and "Curso de Inglés" in prop
        felicita = "¡Felicitaciones! Completaste tu meta." in conf
        monto_actual_ok = (m_curso.monto_actual == Decimal("13000"))
        completada_ok = (m_curso.estado == "completada")

        return f"Propuesta ok: {prop_ok} | Felicita: {felicita} | Monto actual: {int(m_curso.monto_actual)} | Estado completada: {completada_ok}"
    return run_isolated(test)


def p16_caso_5(datos):
    """P16.5 'No' cancela la propuesta sin tocar la meta"""
    from app.models.meta import Meta
    from app.routers.whatsapp.db_lookups import _buscar_propuesta_confirmable_mas_reciente
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        meta = db.execute(select(Meta).where(Meta.usuario_id == u.id, Meta.nombre == "Fondo de Emergencia")).scalar_one()
        m_ini = meta.monto_actual
        b_db = db.get(Billetera, b_gal.id)
        s_ini = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 15000 a mi meta Fondo de Emergencia"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "no"), time.perf_counter())
        resp_canc = respuestas[-1][1] if respuestas else ""

        db.refresh(meta)
        db.refresh(b_db)
        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()
        prop_pendiente = _buscar_propuesta_confirmable_mas_reciente(u.id, db)

        cancelado_ok = "Listo, cancelado." in resp_canc
        meta_intacta = (meta.monto_actual == m_ini)
        saldo_intacta = (b_db.saldo_actual == s_ini)
        txs_creadas = tx_despues - tx_antes

        return f"Cancelado: {cancelado_ok} | Meta intacta: {meta_intacta} | Txs creadas: {txs_creadas} | Saldo intacto: {saldo_intacta}"
    return run_isolated(test)


def p16_caso_6(datos):
    """P16.6 Deshacer un aporte recién confirmado: revierte monto_actual y borra la transacción"""
    from app.models.meta import Meta
    u = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    b_gal = datos[USUARIO_PRUEBAS_EMAIL]["billeteras"]["Galicia"]
    def test(conn, Session, respuestas):
        db = Session()
        conn.execute(text("UPDATE billeteras SET es_principal = (nombre = 'Galicia') WHERE usuario_id = :uid"), {"uid": u.id})
        conn.execute(text("UPDATE conversaciones_wpp SET slot_filling_activo = false, accion_ejecutada = 'test' WHERE usuario_id = :uid"), {"uid": u.id})

        meta = db.execute(select(Meta).where(Meta.usuario_id == u.id, Meta.nombre == "Fondo de Emergencia")).scalar_one()
        m_ini = meta.monto_actual
        b_db = db.get(Billetera, b_gal.id)
        s_ini = b_db.saldo_actual
        tx_antes = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "aportar 15000 a mi meta Fondo de Emergencia"), time.perf_counter())
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())

        # Pedir deshacer
        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "borrá eso"), time.perf_counter())
        resp_prop_deshacer = respuestas[-1][1] if respuestas else ""

        respuestas.clear()
        _procesar_webhook_whatsapp_sync(make_payload(TELEFONO_TEST, "sí"), time.perf_counter())
        resp_conf_deshacer = respuestas[-1][1] if respuestas else ""

        db.refresh(meta)
        db.refresh(b_db)
        tx_despues = conn.execute(select(func.count(Transaccion.id)).where(Transaccion.usuario_id == u.id)).scalar()

        prop_desh_ok = "¿Querés eliminar el aporte" in resp_prop_deshacer and "Fondo de Emergencia" in resp_prop_deshacer
        conf_desh_ok = "Listo, movimiento eliminado." in resp_conf_deshacer
        meta_revertida = (meta.monto_actual == m_ini)
        tx_borrada = (tx_despues == tx_antes)
        saldo_restaurado = (b_db.saldo_actual == s_ini)

        return f"Propuesta deshacer ok: {prop_desh_ok} | Confirmacion deshacer ok: {conf_desh_ok} | Meta revertida: {meta_revertida} | Tx borrada: {tx_borrada} | Saldo restaurado: {saldo_restaurado}"
    return run_isolated(test)


def _ejecutar_suite(verbose: bool = False, ia_real: bool = False, regrabar: bool = False, forzar_grabadas: bool = False, solo_escenario: str | None = None):
    global _gestor_actual
    _gestor_actual = GestorGrabacionesIA(
        dir_grabaciones=DIR_GRABACIONES,
        ia_real=ia_real,
        regrabar=regrabar,
        forzar_grabadas=forzar_grabadas,
    )
    _gestor_actual._cache_grabaciones.update(GRABACIONES_P16)

    t0_suite = time.perf_counter()

    ok_ant, msg_ant = verificar_antiguedad_grabaciones(DIR_GRABACIONES, PROMPT_FILE)
    if not ok_ant:
        print(f"[AVISO] {msg_ant}")

    db = SessionLocal()
    datos = resolver_datos_base(db)
    u_admin = datos[USUARIO_PRUEBAS_EMAIL]["usuario"]
    if u_admin.email != USUARIO_PRUEBAS_EMAIL:
        raise RuntimeError(f"ABORT CRITICO: Verificación de usuario fallida. Resuelto: {u_admin.email}")
    
    conteos_inicio = obtener_conteos_base(db)
    saldos_inicio_21 = obtener_saldos_21(db)
    total_billeteras = len(saldos_inicio_21)
    db.close()

    from app.utils.fecha import hoy_argentina
    hoy = hoy_argentina()
    ayer = hoy - timedelta(days=1)

    escenarios = [
        # --- PUNTO 3 ---
        {
            "id": "P3.1",
            "punto": "Punto 3",
            "nombre": "Usuario con billetera principal dice 'gasté 5000 en el kiosco' sin nombrar billetera",
            "ejecutar": lambda: p3_caso_1(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },
        {
            "id": "P3.2",
            "punto": "Punto 3",
            "nombre": "Usuario sin billetera principal, lo mismo",
            "ejecutar": lambda: p3_caso_2(datos),
            "esperado": "¿Desde qué billetera salió la plata?\n\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            "match": "exacto",
        },
        {
            "id": "P3.3",
            "punto": "Punto 3",
            "nombre": "Responde '2' a un menú de billeteras",
            "ejecutar": lambda: p3_caso_3(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P3.4",
            "punto": "Punto 3",
            "nombre": "Responde con el nombre de la billetera en vez del número",
            "ejecutar": lambda: p3_caso_4(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Santander — registrado.",
            "match": "exacto",
        },
        {
            "id": "P3.5",
            "punto": "Punto 3",
            "nombre": "Responde un número fuera de rango",
            "ejecutar": lambda: p3_caso_5(datos),
            "esperado": "Opción inválida. Elegí un número del 1 al 3.",
            "match": "exacto",
        },
        {
            "id": "P3.6",
            "punto": "Punto 3",
            "nombre": "Manda un número sin ninguna pregunta pendiente",
            "ejecutar": lambda: p3_caso_6(datos),
            "esperado": "Mandaste solo un número. Si querés registrar un movimiento, escribí el monto y el concepto (por ejemplo: 'gasté 5000 en el kiosco').",
            "match": "exacto",
        },
        {
            "id": "P3.7",
            "punto": "Punto 3",
            "nombre": "Responde a un menú 31 minutos después",
            "ejecutar": lambda: p3_caso_7(datos),
            "esperado": "Esa operación ya venció. Podés volver a mandarla.",
            "match": "exacto",
        },
        {
            "id": "P3.8",
            "punto": "Punto 3",
            "nombre": "Recibe una propuesta y responde 'no, fue en Santander'",
            "ejecutar": lambda: p3_caso_8(datos),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Santander. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P3.9",
            "punto": "Punto 3",
            "nombre": "Dice 'cobré 800000 de sueldo' y elige billetera de destino",
            "ejecutar": lambda: p3_caso_9(datos),
            "esperado": "¿A qué billetera entró la plata?\n\n1. Efectivo ARS\n2. Galicia\n3. Santander\n---\nListo. Ingreso de $800.000 en Sueldo a Efectivo ARS — registrado.",
            "match": "exacto",
        },
        {
            "id": "P3.10",
            "punto": "Punto 3",
            "nombre": "Usuario con una sola billetera en pesos dice 'gasté 5000'",
            "ejecutar": lambda: p3_caso_10(datos),
            "esperado": "Listo. $5.000 en Otros desde Efectivo ARS — registrado.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },

        # --- PUNTO 4 ---
        {
            "id": "P4.1",
            "punto": "Punto 4",
            "nombre": "Operación a medias y manda 'hola'",
            "ejecutar": lambda: p4_caso_1(datos),
            "esperado": "Hola. Tenías una operación a medias (anotar $5.000 en Kiosco). Podés completarla o empezar de nuevo.\nTambién podés registrar otro gasto, ingreso o consultar tus saldos.",
            "match": "exacto",
        },
        {
            "id": "P4.2",
            "punto": "Punto 4",
            "nombre": "Operación a medias y manda un gasto distinto",
            "ejecutar": lambda: p4_caso_2(datos),
            "esperado": "Descarté la de $5.000 en Kiosco. Para los $12.000 en Verdulería:\n\n¿Desde qué billetera salió la plata?\n\n1. Efectivo ARS\n2. Galicia\n3. Santander",
            "match": "exacto",
        },
        {
            "id": "P4.3",
            "punto": "Punto 4",
            "nombre": "Recibe una propuesta y responde 'no'",
            "ejecutar": lambda: p4_caso_3(datos),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.4",
            "punto": "Punto 4",
            "nombre": "Tras cancelar manda 'dale' (verifica 0 txs en BD)",
            "ejecutar": lambda: p4_caso_4(datos),
            "esperado": "No tenés ninguna operación pendiente para confirmar. (txs_creadas=0)",
            "match": "exacto",
        },
        {
            "id": "P4.5",
            "punto": "Punto 4",
            "nombre": "Manda 'buenas' sin nada pendiente",
            "ejecutar": lambda: p4_caso_5(datos),
            "esperado": "Hola. Podés registrar gastos, ingresos o consultar tus saldos y proyecciones. Por ejemplo: 'gasté 5000 en el kiosco'.",
            "match": "exacto",
        },
        {
            "id": "P4.6",
            "punto": "Punto 4",
            "nombre": "Manda 'cuánto gasté en pizza'",
            "ejecutar": lambda: p4_caso_6(datos),
            "esperado": "Intent: consultar_gastos | Respuesta ok: True",
            "match": "exacto",
        },
        {
            "id": "P4.7",
            "punto": "Punto 4",
            "nombre": "Responde 31 minutos después",
            "ejecutar": lambda: p4_caso_7(datos),
            "esperado": "Esa operación ya venció. Podés volver a mandarla.",
            "match": "exacto",
        },
        {
            "id": "P4.8",
            "punto": "Punto 4",
            "nombre": "Manda 'sí' sin nada pendiente",
            "ejecutar": lambda: p4_caso_8(datos),
            "esperado": "No tenés ninguna operación pendiente para confirmar.",
            "match": "exacto",
        },
        # Variantes de no
        {
            "id": "P4.VAR1",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no'",
            "ejecutar": lambda: p4_variante_no(datos, "no"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.VAR2",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no, fue en Santander'",
            "ejecutar": lambda: p4_variante_no(datos, "no, fue en Santander", billetera_propuesta="Galicia"),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Santander. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P4.VAR3",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no fue en galicia'",
            "ejecutar": lambda: p4_variante_no(datos, "no fue en galicia", billetera_propuesta="Santander"),
            "esperado": "Voy a anotar $5.000 en Kiosco desde Galicia. ¿Va?",
            "match": "exacto",
        },
        {
            "id": "P4.VAR4",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no, cancelá'",
            "ejecutar": lambda: p4_variante_no(datos, "no, cancelá"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.VAR5",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'nooo'",
            "ejecutar": lambda: p4_variante_no(datos, "nooo"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },
        {
            "id": "P4.VAR6",
            "punto": "Punto 4",
            "nombre": "Variante de no: 'no gracias'",
            "ejecutar": lambda: p4_variante_no(datos, "no gracias"),
            "esperado": "Listo, cancelado.",
            "match": "exacto",
        },

        # --- PUNTO 5 ---
        {
            "id": "P5.1",
            "punto": "Punto 5",
            "nombre": "Gasto repetido a los cinco minutos",
            "ejecutar": lambda: p5_caso_1(datos),
            "esperado": "¿Es un movimiento nuevo o se te repitió?",
            "match": "contiene",
        },
        {
            "id": "P5.2",
            "punto": "Punto 5",
            "nombre": "Ante la pregunta de duplicado, responde que es nuevo",
            "ejecutar": lambda: p5_caso_2(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "contiene",
        },
        {
            "id": "P5.3",
            "punto": "Punto 5",
            "nombre": "Ante la pregunta de duplicado, responde que es un error",
            "ejecutar": lambda: p5_caso_3(datos),
            "esperado": "Listo, no anoto nada.",
            "match": "exacto",
        },
        {
            "id": "P5.4",
            "punto": "Punto 5",
            "nombre": "Gasto igual de hace dos horas (>1h)",
            "ejecutar": lambda: p5_caso_4(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },
        {
            "id": "P5.5",
            "punto": "Punto 5",
            "nombre": "Mismo monto, otra categoría (sin advertencia)",
            "ejecutar": lambda: p5_caso_5(datos),
            "esperado": "Listo. $5.000 en Farmacia desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P5.6",
            "punto": "Punto 5",
            "nombre": "Dos confirmaciones concurrentes",
            "ejecutar": lambda: p5_caso_6_concurrente(datos),
            "esperado": "Exitos=1, Rechazados_por_concurrencia=1",
            "match": "exacto",
        },
        {
            "id": "P5.7",
            "punto": "Punto 5",
            "nombre": "Lote con dos movimientos idénticos",
            "ejecutar": lambda: p5_caso_7(datos),
            "esperado": "Mandaste 2 movimientos iguales de $5.000 en Kiosco desde Galicia. ¿Son dos gastos distintos o se te repitió?",
            "match": "exacto",
        },
        {
            "id": "P5.CUOTAS",
            "punto": "Punto 5",
            "nombre": "Cuotas de tarjeta no disparan falso positivo de duplicado",
            "ejecutar": lambda: p5_caso_cuotas(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.\nSi fue con otra, decime cuál.",
            "match": "exacto",
        },

        # --- PUNTO 6 ---
        {
            "id": "P6.1",
            "punto": "Punto 6",
            "nombre": "Gasto de hoy",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto hoy", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.2",
            "punto": "Punto 6",
            "nombre": "Gasto de ayer",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto ayer", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": ayer.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Galicia (ayer). ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia (ayer) — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.3",
            "punto": "Punto 6",
            "nombre": "Gasto del 31 de agosto",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 31 agosto", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2026-08-31"
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000 en Kiosco desde Galicia (el 31 de agosto). ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia (el 31 de agosto) — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.4",
            "punto": "Punto 6",
            "nombre": "Gasto de hace tres meses (>60 días)",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto 3 meses", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2026-06-03"
            }),
            "esperado": "Propuesta:\nNo puedo registrar movimientos de más de 60 días atrás. Va a quedar con fecha de hoy.\nVoy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.5",
            "punto": "Punto 6",
            "nombre": "Gasto con fecha futura",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto futuro", {
                "monto": 5000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Galicia", "fecha": "2030-03-15"
            }),
            "esperado": "Propuesta:\nNo puedo registrar movimientos con fecha futura porque todavía no ocurrieron. Va a quedar con fecha de hoy.\nVoy a anotar $5.000 en Kiosco desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000 en Kiosco desde Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.6",
            "punto": "Punto 6",
            "nombre": "Gasto en dólares",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto USD", {
                "monto": 50, "moneda": "USD", "tipo": "egreso", "categoria": "Otros", "billetera_origen": "Efectivo USD", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar US$50 en Otros desde Efectivo USD. ¿Va?\nConfirmación:\nListo. US$50 en Otros desde Efectivo USD — registrado.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },
        {
            "id": "P6.7",
            "punto": "Punto 6",
            "nombre": "Lote con uno descartado",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Lote descalce", {
                "monto": 1000, "moneda": "ARS", "tipo": "egreso", "categoria": "Kiosco", "billetera_origen": "Efectivo ARS", "fecha": hoy.isoformat(),
                "transacciones_adicionales": [
                    {"monto": 2000, "moneda": "ARS", "tipo": "egreso", "categoria": "Panadería", "fecha": ayer.isoformat()},
                    {"monto": 10, "moneda": "USD", "tipo": "egreso", "categoria": "Farmacia", "fecha": hoy.isoformat()}
                ]
            }),
            "esperado": "Propuesta:\nNo se pudo registrar Farmacia de US$10 porque es en dólares y la billetera Efectivo ARS es en pesos.\nVoy a anotar 2 movimientos desde Efectivo ARS:\n$1.000 en Kiosco\n$2.000 en Panadería (ayer)\n¿Va?\nConfirmación:\nListo, 2 movimientos desde Efectivo ARS:\n$1.000 en Kiosco\n$2.000 en Panadería (ayer)\nRegistrados.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },
        {
            "id": "P6.8",
            "punto": "Punto 6",
            "nombre": "Gasto que deja la billetera en negativo",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Gasto negativo", {
                "monto": 5000000, "moneda": "ARS", "tipo": "egreso", "categoria": "Supermercado", "billetera_origen": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a anotar $5.000.000 en Supermercado desde Galicia. ¿Va?\nConfirmación:\nListo. $5.000.000 en Supermercado desde Galicia — registrado.\nLa billetera quedó en negativo.",
            "match": "exacto",
        },
        {
            "id": "P6.9",
            "punto": "Punto 6",
            "nombre": "Ingreso",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Ingreso", {
                "monto": 80000, "moneda": "ARS", "tipo": "ingreso", "categoria": "Sueldo", "billetera_destino": "Galicia", "fecha": hoy.isoformat()
            }),
            "esperado": "Propuesta:\nVoy a registrar un ingreso de $80.000 en Sueldo a Galicia. ¿Va?\nConfirmación:\nListo. Ingreso de $80.000 en Sueldo a Galicia — registrado.",
            "match": "exacto",
        },
        {
            "id": "P6.10",
            "punto": "Punto 6",
            "nombre": "Lote con todos descartados",
            "ejecutar": lambda: p6_ejecutar_caso(datos, "Lote todos descartados", {
                "monto": 10, "moneda": "USD", "tipo": "egreso", "categoria": "Farmacia", "billetera_origen": "Efectivo ARS", "fecha": hoy.isoformat(),
                "transacciones_adicionales": [
                    {"monto": 20, "moneda": "USD", "tipo": "egreso", "categoria": "Supermercado", "fecha": hoy.isoformat()}
                ]
            }),
            "esperado": "Propuesta:\nNo se pudo registrar Farmacia de US$10 porque es en dólares y la billetera Efectivo ARS es en pesos.\nNo se pudo registrar Supermercado de US$20 porque es en dólares y la billetera Efectivo ARS es en pesos.\nNo se puede registrar ningún movimiento.\nConfirmación:\nNO_APLICA",
            "match": "exacto",
        },

        # --- PUNTO 7: Categorías estructuradas, jerga argentina y descripciones ---
        {
            "id": "P7.1",
            "punto": "Punto 7",
            "nombre": "Golosinas -> Kiosco (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 5000 en golosinas", "Kiosco", "Golosinas"),
            "esperado": "Cat: Kiosco | Desc: Golosinas",
            "match": "exacto",
        },
        {
            "id": "P7.2",
            "punto": "Punto 7",
            "nombre": "Verdulería -> Verdulería (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 8000 en la verdulería", "Verdulería", "Verdulería"),
            "esperado": "Cat: Verdulería | Desc: Verdulería",
            "match": "exacto",
        },
        {
            "id": "P7.3",
            "punto": "Punto 7",
            "nombre": "Nafta -> Combustible (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "cargué 30000 de nafta", "Combustible", "Nafta"),
            "esperado": "Cat: Combustible | Desc: Nafta",
            "match": "exacto",
        },
        {
            "id": "P7.4",
            "punto": "Punto 7",
            "nombre": "Prepaga -> Obra social / Prepaga (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "pagué 12000 de la prepaga", "Obra social / Prepaga", "Prepaga"),
            "esperado": "Cat: Obra social / Prepaga | Desc: Prepaga",
            "match": "exacto",
        },
        {
            "id": "P7.5",
            "punto": "Punto 7",
            "nombre": "Bondi -> Transporte público (jerga argentina + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 4000 en el bondi", "Transporte público", "Bondi"),
            "esperado": "Cat: Transporte público | Desc: Bondi",
            "match": "exacto",
        },
        {
            "id": "P7.6",
            "punto": "Punto 7",
            "nombre": "Corte de pelo -> Cuidado personal (jerga argentina + descripción preservada)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "me corté el pelo, 15000", "Cuidado personal", "Corte de pelo"),
            "esperado": "Cat: Cuidado personal | Desc: Corte de pelo",
            "match": "exacto",
        },
        {
            "id": "P7.7",
            "punto": "Punto 7",
            "nombre": "Concepto raro -> Otros (prohibición de categorías inventadas + descripción)",
            "ejecutar": lambda: p7_ejecutar_caso(datos, "gasté 2500 en un coso cuántico intergaláctico", "Otros", "Coso cuántico intergaláctico"),
            "esperado": "Cat: Otros | Desc: Coso cuántico intergaláctico",
            "match": "exacto",
        },

        # --- PUNTO 8: Deshacer y Corregir movimientos por WhatsApp ---
        {
            "id": "P8.1",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'borrá eso', confirmar, verificar borrado y reversión de saldo",
            "ejecutar": lambda: p8_caso_1(datos),
            "esperado": "Propuesta:\n¿Querés eliminar el último movimiento de $5.000 en Kiosco desde Galicia? ¿Confirmás?\nConfirmación:\nListo, movimiento eliminado.\nBorrado: True | Saldo restaurado: True",
            "match": "exacto",
        },
        {
            "id": "P8.2",
            "punto": "Punto 8",
            "nombre": "Decir 'borrá eso' sin nada registrado",
            "ejecutar": lambda: p8_caso_2(datos),
            "esperado": "No tenés ningún movimiento reciente registrado por WhatsApp para deshacer. Podés gestionarlo desde la web de Argentum.",
            "match": "exacto",
        },
        {
            "id": "P8.3",
            "punto": "Punto 8",
            "nombre": "Decir 'borrá eso' dos veces seguidas",
            "ejecutar": lambda: p8_caso_3(datos),
            "esperado": "No hay nada para deshacer.",
            "match": "exacto",
        },
        {
            "id": "P8.4",
            "punto": "Punto 8",
            "nombre": "Registrar gasto y decir 'eran 3.000 no 30.000', confirmar, verificar monto y saldo",
            "ejecutar": lambda: p8_caso_4(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $30.000 en Supermercado desde Galicia\nAhora: $3.000 en Supermercado desde Galicia\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nMonto corregido: True | Saldo ajustado (+27k): True",
            "match": "exacto",
        },
        {
            "id": "P8.5",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'eso era supermercado', verificar la categoría",
            "ejecutar": lambda: p8_caso_5(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Supermercado desde Galicia\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nCategoría final: Supermercado",
            "match": "exacto",
        },
        {
            "id": "P8.6",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'fue con Santander', verificar billetera y los dos saldos",
            "ejecutar": lambda: p8_caso_6(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Kiosco desde Santander\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nBilletera Santander: True | Saldo Galicia revertido: True | Saldo Santander descontado: True",
            "match": "exacto",
        },
        {
            "id": "P8.7",
            "punto": "Punto 8",
            "nombre": "Registrar un gasto y decir 'fue ayer', verificar la fecha",
            "ejecutar": lambda: p8_caso_7(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Kiosco desde Galicia (ayer)\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nFecha ayer: True",
            "match": "exacto",
        },
        {
            "id": "P8.8",
            "punto": "Punto 8",
            "nombre": "Corregir dos campos en un mismo mensaje (monto y billetera)",
            "ejecutar": lambda: p8_caso_8(datos),
            "esperado": "Propuesta:\nVoy a corregir el último movimiento:\nAntes: $10.000 en Kiosco desde Galicia\nAhora: $3.000 en Kiosco desde Santander\n¿Confirmás?\nConfirmación:\nListo, movimiento corregido.\nMonto 3000: True | Santander: True",
            "match": "exacto",
        },
        {
            "id": "P8.9",
            "punto": "Punto 8",
            "nombre": "Intentar deshacer una cuota de tarjeta y verificar que se rechaza",
            "ejecutar": lambda: p8_caso_9(datos),
            "esperado": "Ese movimiento corresponde a una cuota de tarjeta y no se puede deshacer por WhatsApp. Podés gestionarlo desde la web de Argentum.",
            "match": "exacto",
        },
        {
            "id": "P8.10",
            "punto": "Punto 8",
            "nombre": "Intentar corregir pasado el plazo (>30 min)",
            "ejecutar": lambda: p8_caso_10(datos),
            "esperado": "El último movimiento fue hace más de 30 minutos. Para modificarlo, ingresá a la web de Argentum.",
            "match": "exacto",
        },
        {
            "id": "P8.11",
            "punto": "Punto 8",
            "nombre": "Con propuesta pendiente, 'no, fue en Santander' corrige propuesta y no movimiento anterior",
            "ejecutar": lambda: p8_caso_11(datos),
            "esperado": "Respuesta:\nVoy a corregir el último movimiento:\nAntes: $5.000 en Kiosco desde Galicia\nAhora: $5.000 en Kiosco desde Santander\n¿Confirmás?\nMovimiento anterior intacto en Efectivo ARS: True",
            "match": "exacto",
        },

        # --- PUNTO 9A: Tarjetas de crédito y cuotas ---
        {
            "id": "P9.1",
            "punto": "Punto 9A",
            "nombre": "gasté 30000 con la Amex: resuelve la tarjeta única, no descuenta saldo, crea una cuota",
            "ejecutar": lambda: p9_caso_1(datos),
            "esperado": "Saldo intacto: True | Es padre: True | Cuotas creadas: True",
            "match": "contiene",
        },
        {
            "id": "P9.2",
            "punto": "Punto 9A",
            "nombre": "gasté 30000 con la Visa: pregunta cuál de las dos",
            "ejecutar": lambda: p9_caso_2(datos),
            "esperado": "¿Con qué tarjeta de crédito fue?\n1. •••• 1506 (Visa - Galicia)\n2. •••• 5077 (Visa - Santander)",
            "match": "exacto",
        },
        {
            "id": "P9.3",
            "punto": "Punto 9A",
            "nombre": "gasté 30000 con la del Santander: resuelve la 5077",
            "ejecutar": lambda: p9_caso_3(datos),
            "esperado": "con tarjeta •••• 5077",
            "match": "contiene",
        },
        {
            "id": "P9.4",
            "punto": "Punto 9A",
            "nombre": "compré una tele en 12 cuotas de 80000: propone 12 cuotas de 80.000, total 960.000",
            "ejecutar": lambda: p9_caso_4(datos),
            "esperado": "12 cuotas de $80.000 (total $960.000)",
            "match": "contiene",
        },
        {
            "id": "P9.5",
            "punto": "Punto 9A",
            "nombre": "gasté 80000 en 12 cuotas: propone 12 cuotas de 6.666,67, total 80.000",
            "ejecutar": lambda: p9_caso_5(datos),
            "esperado": "12 cuotas de $6.666,67 (total $80.000)",
            "match": "contiene",
        },
        {
            "id": "P9.6",
            "punto": "Punto 9A",
            "nombre": "gasté 5000 con Galicia: sigue siendo la billetera, no la tarjeta",
            "ejecutar": lambda: p9_caso_6(datos),
            "esperado": "desde Galicia",
            "match": "contiene",
        },
        {
            "id": "P9.7",
            "punto": "Punto 9A",
            "nombre": "gasté 5000 con la Visa del Galicia: resuelve la tarjeta 1506",
            "ejecutar": lambda: p9_caso_7(datos),
            "esperado": "con tarjeta •••• 1506",
            "match": "contiene",
        },
        {
            "id": "P9.8",
            "punto": "Punto 9A",
            "nombre": "pagué el resumen de la tarjeta: explica que se hace desde la web, no registra nada",
            "ejecutar": lambda: p9_caso_8(datos),
            "esperado": "El pago del resumen de la tarjeta se gestiona desde la web de Argentum. No se puede realizar por WhatsApp. | Txs creadas: 0",
            "match": "exacto",
        },
        {
            "id": "P9.9",
            "punto": "Punto 9A",
            "nombre": "gasté 5000 con la tarjeta de débito: NO es crédito",
            "ejecutar": lambda: p9_caso_9(datos),
            "esperado": "desde Galicia",
            "match": "contiene",
        },
        {
            "id": "P9.10",
            "punto": "Punto 9A",
            "nombre": "Registrar un consumo con tarjeta y deshacerlo: verifica que se borren padre, grupo y cuotas",
            "ejecutar": lambda: p9_caso_10(datos),
            "esperado": "Listo, movimiento eliminado.\nPadres restantes: 0",
            "match": "contiene",
        },
        {
            "id": "P9.11",
            "punto": "Punto 9A",
            "nombre": "Un usuario sin tarjetas dice 'gasté 5000 con la tarjeta': mensaje claro",
            "ejecutar": lambda: p9_caso_11(datos),
            "esperado": "No tenés ninguna tarjeta de crédito cargada en Argentum. Podés agregarla desde la web, o registrar este movimiento como un gasto común con alguna de tus billeteras.",
            "match": "exacto",
        },
        {
            "id": "P9.12",
            "punto": "Punto 9A",
            "nombre": "gasté 3 gambas en el remis: ya no debe interpretarse como 3.000",
            "ejecutar": lambda: p9_caso_12(datos),
            "esperado": "No interpretado como 3000: True",
            "match": "contiene",
        },

        # --- PUNTO 9B: Transferencias, Extracciones y Dólares ---
        {
            "id": "P9B.1",
            "punto": "Punto 9B",
            "nombre": "pasé 10 mil de Galicia a Santander: crea transferencia, ajusta los dos saldos, cero gastos",
            "ejecutar": lambda: p9b_caso_1(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.2",
            "punto": "Punto 9B",
            "nombre": "me transferí 20000 a Santander: pregunta el origen o usa la principal, según corresponda",
            "ejecutar": lambda: p9b_caso_2(datos),
            "esperado": "Voy a transferir $20.000 de Galicia a Santander. ¿Confirmás?",
            "match": "exacto",
        },
        {
            "id": "P9B.3",
            "punto": "Punto 9B",
            "nombre": "saqué 50000 del cajero: transfiere de la cuenta al efectivo, cero gastos",
            "ejecutar": lambda: p9b_caso_3(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.4",
            "punto": "Punto 9B",
            "nombre": "saqué 50000 del cajero con un usuario sin billetera de efectivo: mensaje claro, no registra",
            "ejecutar": lambda: p9b_caso_4(datos),
            "esperado": "No tenés ninguna billetera de efectivo en pesos. Podés crearla desde la web de Argentum. | No registra: True",
            "match": "contiene",
        },
        {
            "id": "P9B.5",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 1500: transfiere 150.000 pesos y suma 100 dólares",
            "ejecutar": lambda: p9b_caso_5(datos),
            "esperado": "Saldos ajustados: True | Cero gastos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.6",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares: pregunta la cotización o los pesos",
            "ejecutar": lambda: p9b_caso_6(datos),
            "esperado": "¿A qué cotización compraste o cuántos pesos pagaste?",
            "match": "exacto",
        },
        {
            "id": "P9B.7",
            "punto": "Punto 9B",
            "nombre": "vendí 50 dólares a 1450: transfiere al revés",
            "ejecutar": lambda: p9b_caso_7(datos),
            "esperado": "Saldos ajustados: True",
            "match": "contiene",
        },
        {
            "id": "P9B.8",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 5: advierte que la cotización es absurda",
            "ejecutar": lambda: p9b_caso_8(datos),
            "esperado": "La cotización de $5 por dólar no parece razonable",
            "match": "contiene",
        },
        {
            "id": "P9B.9",
            "punto": "Punto 9B",
            "nombre": "le transferí 5000 a mi hermano: es un gasto, no una transferencia",
            "ejecutar": lambda: p9b_caso_9(datos),
            "esperado": "Es gasto: True",
            "match": "contiene",
        },
        {
            "id": "P9B.10",
            "punto": "Punto 9B",
            "nombre": "gasté 5000 en el kiosco: sigue siendo un gasto",
            "ejecutar": lambda: p9b_caso_10(datos),
            "esperado": "Listo. $5.000 en Kiosco desde Galicia — registrado.\nSi fue con otra, decime cuál.",
            "match": "contiene",
        },
        {
            "id": "P9B.11",
            "punto": "Punto 9B",
            "nombre": "Registrar una transferencia y deshacerla: los dos saldos vuelven",
            "ejecutar": lambda: p9b_caso_11(datos),
            "esperado": "Saldos intactos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.12",
            "punto": "Punto 9B",
            "nombre": "Transferencia con origen y destino iguales: se rechaza",
            "ejecutar": lambda: p9b_caso_12(datos),
            "esperado": "La billetera de origen y destino no pueden ser la misma.",
            "match": "exacto",
        },
        {
            "id": "P9B.13",
            "punto": "Punto 9B",
            "nombre": "compré 5 dólares y responder 7500: debe registrar 5 dólares a 1.500, no rechazar",
            "ejecutar": lambda: p9b_caso_13(datos),
            "esperado": "Saldos: True",
            "match": "contiene",
        },
        {
            "id": "P9B.14",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares y responder 1500: cotización unitaria",
            "ejecutar": lambda: p9b_caso_14(datos),
            "esperado": "compra de USD 100 a $1.500: salen $150.000",
            "match": "contiene",
        },
        {
            "id": "P9B.15",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares y responder 150000: monto total",
            "ejecutar": lambda: p9b_caso_15(datos),
            "esperado": "compra de USD 100 a $1.500: salen $150.000",
            "match": "contiene",
        },
        {
            "id": "P9B.16",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 15: debe advertir, con la cotización de referencia en el mensaje",
            "ejecutar": lambda: p9b_caso_16(datos),
            "esperado": "la cotización de referencia es de",
            "match": "contiene",
        },
        {
            "id": "P9B.17",
            "punto": "Punto 9B",
            "nombre": "compré 100 dólares a 1500 con la tabla de cotizaciones vacía: no rechaza, pide confirmación",
            "ejecutar": lambda: p9b_caso_17(datos),
            "esperado": "Voy a registrar una compra de USD 100 a $1.500: salen $150.000",
            "match": "contiene",
        },

        # --- PUNTO 10: Suscripciones (Etapa B) ---
        {
            "id": "P10.1",
            "punto": "Punto 10",
            "nombre": "empecé a pagar 5000 de Disney+: pregunta la frecuencia, crea la suscripción, no cobra nada",
            "ejecutar": lambda: p10_caso_1(datos),
            "esperado": "Pregunta frecuencia: True\nPropuesta: True\nConfirmación: True\nSub creada: True\nTxs cobro generadas: 0",
            "match": "exacto",
        },
        {
            "id": "P10.2",
            "punto": "Punto 10",
            "nombre": "gasté 5000 en Disney+: sigue siendo un gasto suelto",
            "ejecutar": lambda: p10_caso_2(datos),
            "esperado": "Listo. $5.000 en Otros desde Galicia — registrado.\nSi fue con otra, decime cuál.",
            "match": "contiene",
        },
        {
            "id": "P10.3",
            "punto": "Punto 10",
            "nombre": "me suscribí a Netflix por 9000 por mes: crea con frecuencia mensual confirmada",
            "ejecutar": lambda: p10_caso_3(datos),
            "esperado": "Propuesta: True\nConfirmación: True\nFrecuencia mensual: True",
            "match": "exacto",
        },
        {
            "id": "P10.4",
            "punto": "Punto 10",
            "nombre": "pagué el Spotify: pregunta si es gasto único o suscripción",
            "ejecutar": lambda: p10_caso_4(datos),
            "esperado": "¿Es un gasto único o una suscripción a Spotify?",
            "match": "exacto",
        },
        {
            "id": "P10.5",
            "punto": "Punto 10",
            "nombre": "me suscribí a ChatGPT por 20 dólares por mes con medio de pago en pesos: crea la suscripción en dólares",
            "ejecutar": lambda: p10_caso_5(datos),
            "esperado": "Propuesta: True\nSub creada: True\nMoneda sub: USD\nMedio de pago pesos: True",
            "match": "exacto",
        },
        {
            "id": "P10.6",
            "punto": "Punto 10",
            "nombre": "di de baja Netflix sin tenerla: mensaje claro",
            "ejecutar": lambda: p10_caso_6(datos),
            "esperado": "No tenés ninguna suscripción activa a Netflix.",
            "match": "exacto",
        },
        {
            "id": "P10.7",
            "punto": "Punto 10",
            "nombre": "di de baja la suscripción existente: confirma y la da de baja",
            "ejecutar": lambda: p10_caso_7(datos),
            "esperado": "Pregunta confirmación: True\nConfirmación: True\nEstado final: cancelada",
            "match": "exacto",
        },
        {
            "id": "P10.8",
            "punto": "Punto 10",
            "nombre": "aumentó Netflix, ahora son 12000: muestra el precio anterior y el nuevo",
            "ejecutar": lambda: p10_caso_8(datos),
            "esperado": "Propuesta muestra ambos: True\nConfirmación: True\nPrecio en base: 12000.00",
            "match": "exacto",
        },
        {
            "id": "P10.9",
            "punto": "Punto 10",
            "nombre": "cuánto gasto en suscripciones: lista y total mensual",
            "ejecutar": lambda: p10_caso_9(datos),
            "esperado": "Lista activa: True\nTotal mensual: True\nSin saldos billetera: True",
            "match": "exacto",
        },
        {
            "id": "P10.10",
            "punto": "Punto 10",
            "nombre": "Registrar un gasto igual a una suscripción ya cobrada este período: avisa antes",
            "ejecutar": lambda: p10_caso_10(datos),
            "esperado": "Aviso cobro previo: True\nRegistro tras confirmación: True",
            "match": "exacto",
        },
        {
            "id": "P10.11",
            "punto": "Punto 10",
            "nombre": "Crear una suscripción de un servicio que ya tiene activa: avisa",
            "ejecutar": lambda: p10_caso_11(datos),
            "esperado": "Aviso existente: True",
            "match": "exacto",
        },
        {
            "id": "P10.12",
            "punto": "Punto 10",
            "nombre": "Un servicio que no está en el catálogo: lo acepta igual",
            "ejecutar": lambda: p10_caso_12(datos),
            "esperado": "Propuesta servicio no-catálogo: True",
            "match": "exacto",
        },
        # --- PUNTO 11: Lotes y Multi-Operación (10 casos) ---
        {
            "id": "P11.1",
            "punto": "Punto 11",
            "nombre": "Dos gastos con billeteras distintas nombradas explícitamente",
            "ejecutar": lambda: p11_caso_1(datos),
            "esperado": "Propuesta: True | Confirmacion: True",
            "match": "exacto",
        },
        {
            "id": "P11.2",
            "punto": "Punto 11",
            "nombre": "Dos gastos sin billetera, con el usuario teniendo principal",
            "ejecutar": lambda: p11_caso_2(datos),
            "esperado": "Propuesta principal: True",
            "match": "exacto",
        },
        {
            "id": "P11.3",
            "punto": "Punto 11",
            "nombre": "Dos gastos sin billetera, sin principal: pregunta una vez",
            "ejecutar": lambda: p11_caso_3(datos),
            "esperado": "Pregunta una vez: True | Propuesta resuelta: True",
            "match": "exacto",
        },
        {
            "id": "P11.4",
            "punto": "Punto 11",
            "nombre": "Tres gastos donde solo uno nombra billetera",
            "ejecutar": lambda: p11_caso_4(datos),
            "esperado": "Pregunta faltantes: True | Propuesta mixta: True",
            "match": "exacto",
        },
        {
            "id": "P11.5",
            "punto": "Punto 11",
            "nombre": "Un gasto y un ingreso en el mismo mensaje",
            "ejecutar": lambda: p11_caso_5(datos),
            "esperado": "Propuesta signos: True | Confirmacion signos: True",
            "match": "exacto",
        },
        {
            "id": "P11.6",
            "punto": "Punto 11",
            "nombre": "Un lote con un consumo de tarjeta y un gasto normal",
            "ejecutar": lambda: p11_caso_6(datos),
            "esperado": "Lote tarjeta y gasto: True",
            "match": "exacto",
        },
        {
            "id": "P11.7",
            "punto": "Punto 11",
            "nombre": "Doce movimientos: rechaza con mensaje claro",
            "ejecutar": lambda: p11_caso_7(datos),
            "esperado": "Rechazo tope: True",
            "match": "exacto",
        },
        {
            "id": "P11.8",
            "punto": "Punto 11",
            "nombre": "Un lote donde una operación está en otra moneda sin billetera de esa moneda",
            "ejecutar": lambda: p11_caso_8(datos),
            "esperado": "Aviso descarte y propuesta: True",
            "match": "exacto",
        },
        {
            "id": "P11.9",
            "punto": "Punto 11",
            "nombre": "Un lote con dos movimientos idénticos",
            "ejecutar": lambda: p11_caso_9(datos),
            "esperado": "Deteccion duplicado interno: True",
            "match": "exacto",
        },
        {
            "id": "P11.10",
            "punto": "Punto 11",
            "nombre": "Un lote seguido de 'borrá eso'",
            "ejecutar": lambda: p11_caso_10(datos),
            "esperado": "Propuesta deshacer lote: True | Confirmacion deshacer lote: True",
            "match": "exacto",
        },
        {
            "id": "P11.11",
            "punto": "Punto 11",
            "nombre": "Lote de 3 gastos en un solo mensaje: confirma, crea 3 txs, valida accion_ejecutada > 100 caracteres",
            "ejecutar": lambda: p11_caso_11(datos),
            "esperado": "Txs creadas: 3 | Accion len ok: True | Sin error: True",
            "match": "exacto",
        },
        # --- PUNTO 12: Consultas y Dashboard (Balance y Cotización) ---
        {
            "id": "P12.1",
            "punto": "Punto 12",
            "nombre": "consultar_balance: 'cuál es mi balance' detecta intent y devuelve balance real del dashboard",
            "ejecutar": lambda: p12_caso_1(datos),
            "esperado": "Intent: consultar_balance | Datos reales: True",
            "match": "exacto",
        },
        {
            "id": "P12.2",
            "punto": "Punto 12",
            "nombre": "consultar_cotizacion: 'a cuánto está el dólar' detecta intent y devuelve cotizaciones reales",
            "ejecutar": lambda: p12_caso_2(datos),
            "esperado": "Intent: consultar_cotizacion | Cotizacion fija ok: True",
            "match": "exacto",
        },
        {
            "id": "P12.3",
            "punto": "Punto 12",
            "nombre": "consultar_saldo: 'cuánto tengo' detecta intent y devuelve saldo real del dashboard",
            "ejecutar": lambda: p12_caso_3(datos),
            "esperado": "Intent: consultar_saldo | Datos reales: True",
            "match": "exacto",
        },
        {
            "id": "P12.4",
            "punto": "Punto 12",
            "nombre": "consultar_proyeccion: 'cuál es mi proyección financiera' detecta intent y devuelve proyección real",
            "ejecutar": lambda: p12_caso_4(datos),
            "esperado": "Intent: consultar_proyeccion | Proyeccion ok: True",
            "match": "exacto",
        },
        {
            "id": "P12.5",
            "punto": "Punto 12",
            "nombre": "consultar_saldo: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_5(datos),
            "esperado": "Intent: consultar_saldo | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P12.6",
            "punto": "Punto 12",
            "nombre": "consultar_balance: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_6(datos),
            "esperado": "Intent: consultar_balance | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P12.7",
            "punto": "Punto 12",
            "nombre": "consultar_proyeccion: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_7(datos),
            "esperado": "Intent: consultar_proyeccion | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P12.8",
            "punto": "Punto 12",
            "nombre": "consultar_cotizacion: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_8(datos),
            "esperado": "Intent: consultar_cotizacion | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P12.9",
            "punto": "Punto 12",
            "nombre": "consultar_cotizacion: cotizaciones vacías devuelve mensaje amigable",
            "ejecutar": lambda: p12_caso_9(datos),
            "esperado": "Intent: consultar_cotizacion | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P12.10",
            "punto": "Punto 12",
            "nombre": "consultar_meta: 'cómo va mi meta' detecta intent y devuelve metas reales",
            "ejecutar": lambda: p12_caso_10(datos),
            "esperado": "Intent: consultar_meta | Datos reales: True",
            "match": "exacto",
        },
        {
            "id": "P12.11",
            "punto": "Punto 12",
            "nombre": "consultar_presupuesto: 'cómo va mi presupuesto' detecta intent y devuelve presupuestos reales",
            "ejecutar": lambda: p12_caso_11(datos),
            "esperado": "Intent: consultar_presupuesto | Datos reales: True",
            "match": "exacto",
        },
        {
            "id": "P12.12",
            "punto": "Punto 12",
            "nombre": "consultar_meta: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_12(datos),
            "esperado": "Intent: consultar_meta | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P12.13",
            "punto": "Punto 12",
            "nombre": "consultar_presupuesto: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p12_caso_13(datos),
            "esperado": "Intent: consultar_presupuesto | Falla manejada: True",
            "match": "exacto",
        },
        {
            "id": "P13.1",
            "punto": "Punto 13",
            "nombre": "consultar_gastos: 'cuánto gasté hoy' detecta intent y calcula gastos de hoy",
            "ejecutar": lambda: p13_caso_1(datos),
            "esperado": "Intent: consultar_gastos | Respuesta ok: True",
            "match": "exacto",
        },
        {
            "id": "P13.2",
            "punto": "Punto 13",
            "nombre": "consultar_gastos: 'cuánto gasté este ciclo' coincide con balance de ciclo",
            "ejecutar": lambda: p13_caso_2(datos),
            "esperado": "Intent: consultar_gastos | Coincide con balance: True",
            "match": "exacto",
        },
        {
            "id": "P13.3",
            "punto": "Punto 13",
            "nombre": "consultar_gastos: 'cuánto gasté en pizza esta semana' filtra por descripción",
            "ejecutar": lambda: p13_caso_3(datos),
            "esperado": "Intent: consultar_gastos | Filtro por descripcion: True",
            "match": "exacto",
        },
        {
            "id": "P13.4",
            "punto": "Punto 13",
            "nombre": "consultar_gastos: 'cuánto gasté en supermercado el mes pasado' filtra por catálogo",
            "ejecutar": lambda: p13_caso_4(datos),
            "esperado": "Intent: consultar_gastos | Filtro por catalogo: True",
            "match": "exacto",
        },
        {
            "id": "P13.5",
            "punto": "Punto 13",
            "nombre": "consultar_gastos: falla de servicio maneja error con mensaje amigable",
            "ejecutar": lambda: p13_caso_5(datos),
            "esperado": "Intent: consultar_gastos | Falla manejada: True",
            "match": "exacto",
        },
        # ==============================================================================
        # PUNTO 14: Registro Directo WhatsApp
        # ==============================================================================
        {
            "id": "P14.1",
            "punto": "Punto 14",
            "nombre": "gasté 5000 en el kiosco: registro directo en el acto",
            "ejecutar": lambda: p14_caso_1(datos),
            "esperado": "Resp ok: True | Txs: 1 | Saldo: -5000.00 | Accion ejecutada: True | Propuesta pendiente: True",
            "match": "exacto",
        },
        {
            "id": "P14.2",
            "punto": "Punto 14",
            "nombre": "lote de 2: registra directo 2 movimientos con accion_ejecutada lote:id1,id2",
            "ejecutar": lambda: p14_caso_2(datos),
            "esperado": "Resp ok: True | Txs: 2 | Accion lote: True",
            "match": "exacto",
        },
        {
            "id": "P14.3",
            "punto": "Punto 14",
            "nombre": "ingreso simple: registra en el acto",
            "ejecutar": lambda: p14_caso_3(datos),
            "esperado": "Resp ok: True | Txs: 1 | Saldo: +800000.00",
            "match": "exacto",
        },
        {
            "id": "P14.4",
            "punto": "Punto 14",
            "nombre": "compra con tarjeta de crédito en cuotas: NO registra directo, pide confirmación",
            "ejecutar": lambda: p14_caso_4(datos),
            "esperado": "Pide conf: True | Txs antes de si: 0 | Txs despues de si: True | Confirma ok: True",
            "match": "exacto",
        },
        {
            "id": "P14.5",
            "punto": "Punto 14",
            "nombre": "el mismo gasto dos veces seguidas: el segundo pregunta por duplicado y no registra",
            "ejecutar": lambda: p14_caso_5(datos),
            "esperado": "Pregunta duplicado: True | Txs totales: 1",
            "match": "exacto",
        },
        {
            "id": "P14.6",
            "punto": "Punto 14",
            "nombre": "confianza 0.70: resultado idéntico a HEAD y 0 transacciones",
            "ejecutar": lambda: p14_caso_6(datos),
            "esperado": "Coincide con HEAD: True | Txs creadas: 0",
            "match": "exacto",
        },
        {
            "id": "P14.7",
            "punto": "Punto 14",
            "nombre": "sí justo después de un registro directo: mensaje claro y 0 transacciones nuevas",
            "ejecutar": lambda: p14_caso_7(datos),
            "esperado": "Resp ok: True | Txs nuevas: 0",
            "match": "exacto",
        },
        {
            "id": "P14.8",
            "punto": "Punto 14",
            "nombre": "registro directo, deshacer y sí: borra transacción y restaura saldo",
            "ejecutar": lambda: p14_caso_8(datos),
            "esperado": "Deshacer ok: True | Txs restantes: 0 | Saldo restaurado: True",
            "match": "exacto",
        },
        {
            "id": "P14.9",
            "punto": "Punto 14",
            "nombre": "registro directo y eran 3000 no 5000: corrige monto y saldo",
            "ejecutar": lambda: p14_caso_9(datos),
            "esperado": "Monto corregido: True | Saldo ok: True | Confirmacion: True",
            "match": "exacto",
        },
        {
            "id": "P14.10",
            "punto": "Punto 14",
            "nombre": "falla forzada: rollback limpio, 0 transacciones, saldo intacto, ninguna propuesta y sí posterior inocuo",
            "ejecutar": lambda: p14_caso_10(datos),
            "esperado": "Sin listo: True | Txs creadas: 0 | Saldo intacto: True | Propuesta pendiente: True | Txs despues si: 0",
            "match": "exacto",
        },
        {
            "id": "P14.11",
            "punto": "Punto 14",
            "nombre": "idempotencia: el mismo wamid dos veces resulta en 1 sola transacción",
            "ejecutar": lambda: p14_caso_11(datos),
            "esperado": "Txs creadas: 1",
            "match": "exacto",
        },
        {
            "id": "P14.12",
            "punto": "Punto 14",
            "nombre": "billetera ambigua: pregunta cuál; al responder registra en el acto",
            "ejecutar": lambda: p14_caso_12(datos),
            "esperado": "Pregunta ok: True | Txs antes eleccion: 0 | Registro ok: True | Txs creadas: 1",
            "match": "exacto",
        },
        {
            "id": "P14.13",
            "punto": "Punto 14",
            "nombre": "origen imagen sin simular imágenes: tras elegir billetera pide confirmación y recién con sí registra",
            "ejecutar": lambda: p14_caso_13(datos),
            "esperado": "Pide conf: True | Txs antes si: 0 | Txs despues si: 1 | Confirma ok: True",
            "match": "exacto",
        },
        {
            "id": "P14.14",
            "punto": "Punto 14",
            "nombre": "lote con todos los ítems inválidos: mensaje claro, 0 transacciones y sin propuesta",
            "ejecutar": lambda: p14_caso_14(datos),
            "esperado": "Msg ok: True | Txs creadas: 0 | Propuesta pendiente: True",
            "match": "exacto",
        },
        {
            "id": "P14.15",
            "punto": "Punto 14",
            "nombre": "lote con un ítem inválido y uno válido: registra el válido con aviso previo al Listo",
            "ejecutar": lambda: p14_caso_15(datos),
            "esperado": "Aviso descarte: True | Listo ok: True | Orden ok: True | Txs: 1",
            "match": "exacto",
        },
        {
            "id": "P14.16",
            "punto": "Punto 14",
            "nombre": "confianza baja con billetera pendiente: tras elegir billetera pide confirmación y recién con sí registra",
            "ejecutar": lambda: p14_caso_16(datos),
            "esperado": "Pide conf: True | Txs antes si: 0 | Txs despues si: 1 | Confirma ok: True",
            "match": "exacto",
        },
        {
            "id": "P14.17",
            "punto": "Punto 14",
            "nombre": "control sin marcas con billetera pendiente: tras elegir billetera registra directo",
            "ejecutar": lambda: p14_caso_17(datos),
            "esperado": "Registra directo: True | Txs: 1",
            "match": "exacto",
        },

        # --- PUNTO 16 (Aportes a metas) ---
        {
            "id": "P16.1",
            "punto": "Punto 16",
            "nombre": "Aporte simple con meta y monto claros: propone, confirma, aumenta monto_actual y crea tx",
            "ejecutar": lambda: p16_caso_1(datos),
            "esperado": "Propuesta ok: True | Confirmado ok: True | Monto sumado: 15000 | Tx desc ok: True | Saldo Galicia ok: True",
            "match": "exacto",
        },
        {
            "id": "P16.2",
            "punto": "Punto 16",
            "nombre": "Meta ambigua: pregunta cuál antes de proponer",
            "ejecutar": lambda: p16_caso_2(datos),
            "esperado": "Pregunta ambigua: True | Txs creadas: 0 | Sin propuesta pendiente: True",
            "match": "exacto",
        },
        {
            "id": "P16.3",
            "punto": "Punto 16",
            "nombre": "Meta inexistente: mensaje claro, no propone nada",
            "ejecutar": lambda: p16_caso_3(datos),
            "esperado": "Mensaje claro: True | Txs creadas: 0 | Sin propuesta pendiente: True",
            "match": "exacto",
        },
        {
            "id": "P16.4",
            "punto": "Punto 16",
            "nombre": "Monto que completa o supera el objetivo: confirma y felicita",
            "ejecutar": lambda: p16_caso_4(datos),
            "esperado": "Propuesta ok: True | Felicita: True | Monto actual: 13000 | Estado completada: True",
            "match": "exacto",
        },
        {
            "id": "P16.5",
            "punto": "Punto 16",
            "nombre": "'No' cancela la propuesta sin tocar la meta",
            "ejecutar": lambda: p16_caso_5(datos),
            "esperado": "Cancelado: True | Meta intacta: True | Txs creadas: 0 | Saldo intacto: True",
            "match": "exacto",
        },
        {
            "id": "P16.6",
            "punto": "Punto 16",
            "nombre": "Deshacer un aporte recién confirmado: revierte monto_actual y borra la transacción",
            "ejecutar": lambda: p16_caso_6(datos),
            "esperado": "Propuesta deshacer ok: True | Confirmacion deshacer ok: True | Meta revertida: True | Tx borrada: True | Saldo restaurado: True",
            "match": "exacto",
        },
    ]

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

    dur_total = time.perf_counter() - t0_suite

    # Verificación estricta de rollback y conteos
    db = SessionLocal()
    conteos_fin = obtener_conteos_base(db)
    db.close()

    saldos_intactos = (conteos_inicio["saldos"] == conteos_fin["saldos"])
    sin_residuos = (
        conteos_inicio["tx"] == conteos_fin["tx"] and
        conteos_inicio["conv"] == conteos_fin["conv"] and
        conteos_inicio["msg"] == conteos_fin["msg"] and
        saldos_intactos
    )

    # Verificación de saldos contra referencia histórica de las 21 billeteras
    db_ref = SessionLocal()
    saldos_ref_ok, desvios_ref, detalles_ref = verificar_saldos_contra_referencia(db_ref, saldos_inicio_21)
    db_ref.close()

    # Verificación de reconciliación de saldos en todas las 21 billeteras
    db_rec = SessionLocal()
    rec_ok, discrepancias, detalles_rec = verificar_reconciliacion_billeteras(db_rec)
    db_rec.close()

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
        print(f"Mensajes procesados: antes={conteos_inicio['msg']} | después={conteos_fin['msg']}")
        print(f"Saldos de billeteras intactos: {'SÍ' if saldos_intactos else 'NO'}")
        print(f"¿Rollback total verificado (cero residuo)?: {'SÍ' if sin_residuos else 'NO'}")

        print(f"\n=== VERIFICACION DE SALDOS CONTRA REFERENCIA HISTORICA ({total_billeteras} BILLETERAS) ===")
        for d in detalles_ref:
            esperado = d["referencia"] if d["referencia"] is not None else d["saldo_inicial"]
            criterio = "referencia" if d["referencia"] is not None else "foto_inicio"
            st = "OK" if d["diff"] == Decimal("0.00") else f"DESVIO ({d['diff']})"
            print(f"  {d['email']} | {d['billetera']} {d['moneda']}: antes={esperado} | después={d['actual']} | diferencia={d['diff']} | criterio={criterio} -> {st}")
        print(f"¿Todos los saldos cumplen el criterio?: {'SÍ' if saldos_ref_ok else 'NO'}")
        if not saldos_ref_ok:
            print(f"ALERTA: Se detectaron {len(desvios_ref)} billeteras con saldos alterados:")
            for desv in desvios_ref:
                antes = desv["referencia"] if desv["referencia"] is not None else desv["saldo_inicial"]
                print(f"  - {desv['email']} ({desv['billetera']} {desv['moneda']}): antes={antes}, después={desv['actual']}, diferencia={desv['diff']}")

        print(f"\n=== VERIFICACION DE RECONCILIACION ({total_billeteras} BILLETERAS) ===")
        for d in detalles_rec:
            if d["ok"]:
                st = f"OK (baseline {d['esperado_diff']:+.2f})" if d["esperado_diff"] != Decimal("0.00") else "OK"
            else:
                st = f"DESVIO_NO_ESPERADO (diff={d['diferencia']:+.2f}, esperado={d['esperado_diff']:+.2f})"
            print(f"  {d['email']} | {d['billetera']}: guardado={d['guardado']} | calc={d['calculado']} | diff={d['diferencia']} -> {st}")
        print(f"¿Reconciliación de todas las billeteras dentro del baseline?: {'SÍ' if rec_ok else 'NO'}")
        if not rec_ok:
            print(f"ALERTA: Se detectaron {len(discrepancias)} billeteras con desviaciones fuera del baseline:")
            for disc in discrepancias:
                print(f"  - {disc['email']} ({disc['billetera']}): guardado={disc['guardado']}, calculado={disc['calculado']}, diff={disc['diferencia']}, esperado={disc['esperado_diff']}")
    else:
        # Modo compacto (menos de 30 líneas en verde)
        print("\n=== RESUMEN DE EJECUCION ===")
        print(f"Total: {total} | Aprobados: {aprobados} | Omitidos: {omitidos} | Fallidos: {fallidos} | Tiempo: {dur_total:.2f}s")
        print(f"Llamadas IA: {_gestor_actual.llamadas_grabadas} grabadas, {_gestor_actual.llamadas_reales} reales")
        print(f"Rollback y conteos: {'OK (cero residuo)' if sin_residuos else 'FALLO'}")
        if saldos_ref_ok:
            print(f"Saldos {total_billeteras} billeteras: OK (testingadmin contra referencia, cuentas ajenas contra foto inicial)")
        else:
            print(f"Saldos {total_billeteras} billeteras: DESVIO ({len(desvios_ref)} billeteras)")
            for desv in desvios_ref:
                antes = desv["referencia"] if desv["referencia"] is not None else desv["saldo_inicial"]
                print(f"  - {desv['email']} ({desv['billetera']} {desv['moneda']}): antes={antes}, después={desv['actual']}, diferencia={desv['diff']}")
        if rec_ok:
            print(f"Reconciliación {total_billeteras} billeteras: OK (todas dentro del baseline)")
        else:
            print(f"Reconciliación {total_billeteras} billeteras: DESVIO ({len(discrepancias)} fuera de baseline)")
            for disc in discrepancias:
                print(f"  - {disc['email']} ({disc['billetera']}): guardado={disc['guardado']}, calc={disc['calculado']}, diff={disc['diferencia']}")

    return total, aprobados, omitidos, fallidos, detalles_fallidos


def correr_suite_completa(verbose: bool = False, ia_real: bool = False, regrabar: bool = False, forzar_grabadas: bool = False, escenario: str | None = None):
    print("=== INICIANDO SUITE CONSOLIDADA DE REGRESION DE WHATSAPP ===")
    modo_str = "IA Real" if ia_real else ("Regrabar" if regrabar else "Grabadas (replay)")
    salida_str = "Detallada" if verbose else "Compacta"
    filtro_str = f" | Escenario: {escenario}" if escenario else ""
    print(f"Modo IA: {modo_str} | Salida: {salida_str}{filtro_str} | Usuario: {USUARIO_PRUEBAS_EMAIL}")

    return _ejecutar_suite(verbose=verbose, ia_real=ia_real, regrabar=regrabar, forzar_grabadas=forzar_grabadas, solo_escenario=escenario)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Suite consolidada de regresión de WhatsApp")
    parser.add_argument("-v", "--verbose", action="store_true", help="Salida detallada escenario por escenario y tablas completas")
    parser.add_argument("--ia-real", "--live", action="store_true", help="Ejecutar todas las llamadas contra OpenAI real")
    parser.add_argument("--regrabar", "--record", action="store_true", help="Regrabar todas las llamadas contra OpenAI real y sobrescribir archivos")
    parser.add_argument("--forzar-grabadas", action="store_true", help="Forzar uso de grabaciones incluso en escenarios de modelo P7.1-P7.7 (modo offline)")
    parser.add_argument("--escenario", type=str, default=None, help="Ejecutar solo el escenario especificado por ID (ej: P6.5)")
    args = parser.parse_args()

    total, aprobados, omitidos, fallidos, _ = correr_suite_completa(
        verbose=args.verbose,
        ia_real=args.ia_real,
        regrabar=args.regrabar,
        forzar_grabadas=args.forzar_grabadas,
        escenario=args.escenario,
    )
    if fallidos > 0:
        sys.exit(1)
