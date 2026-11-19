# -*- coding: utf-8 -*-
"""
scripts/evaluacion/extraer_mensajes_reales.py

Extrae mensajes reales anonimizados (solo lectura de la copia local) de
usuarios autorizados de los últimos 180 días para armar el set M2.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CUENTAS_AUTORIZADAS = (
    "giordaninosebas@gmail.com",
    "mrm291201@gmail.com",
    "testingadmin@argentum.com",
)
OUT_PATH = Path(
    r"D:\Argentum\auditorias\raw\fase_m2a_preparacion"
    r"\fase_m2a_preparacion_20_mensajes_reales.txt"
)

_RX_LINK = re.compile(r"(?:https?://\S+|www\.\S+)", re.IGNORECASE)
_RX_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_RX_CBU = re.compile(r"\b\d{22}\b")
_RX_ALIAS = re.compile(
    r"\b(?=[A-Za-z0-9_-]*[A-Za-z])"
    r"[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\.[A-Za-z0-9_-]{2,}\b"
)
_RX_TEL = re.compile(r"(?<!\w)(?:\+?\d[\d\s\-().]{6,}\d)(?!\w)")
_RX_DIGITOS_7 = re.compile(r"\d{7,}")


def _reemplazar_tel(match: re.Match[str]) -> str:
    txt = match.group(0)
    digitos = sum(1 for c in txt if c.isdigit())
    if digitos >= 8:
        return "[TELEFONO]"
    return txt


def anonimizar(texto: str | None) -> str:
    """Anonimiza links, emails, CBU/CVU, alias, teléfonos y secuencias >= 7 dígitos."""
    if not texto:
        return ""
    res = _RX_LINK.sub("[LINK]", texto)
    res = _RX_EMAIL.sub("[EMAIL]", res)
    res = _RX_CBU.sub("[CBU]", res)
    res = _RX_ALIAS.sub("[ALIAS]", res)
    res = _RX_TEL.sub(_reemplazar_tel, res)
    res = _RX_DIGITOS_7.sub("[NUM]", res)
    return " ".join(res.split())


def _normalizar_dedup(texto: str) -> str:
    sin_tildes = "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(sin_tildes.lower().split())


def _es_descartable(texto: str) -> bool:
    norm = _normalizar_dedup(texto)
    if not norm:
        return True
    if norm in ("si", "no", "ok"):
        return True
    if len(norm) == 1:
        return True
    if re.fullmatch(r"\$?\s*[\d.,]+", norm):
        return True
    return False


def _tiene_dato_residual(texto: str) -> bool:
    if "@" in texto:
        return True
    if re.search(r"\d{7,}", texto):
        return True
    return False


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    from sqlalchemy import or_, select
    from app.core.database import SessionLocal
    from app.models.conversacion_wpp import ConversacionWpp, TipoMensajeWpp
    from app.models.usuario import Usuario
    from app.utils.fecha import TZ_ARGENTINA

    db = SessionLocal()
    try:
        usuarios = db.execute(
            select(Usuario)
            .where(
                or_(
                    Usuario.permiso_lectura_mensajes_en.is_not(None),
                    Usuario.email.in_(CUENTAS_AUTORIZADAS),
                )
            )
            .order_by(Usuario.fecha_registro.asc(), Usuario.id.asc())
        ).scalars().all()

        alias_por_uid = {
            u.id: f"Usuario_{idx:02d}" for idx, u in enumerate(usuarios, start=1)
        }
        uids = list(alias_por_uid.keys())
        desde = datetime.now(timezone.utc) - timedelta(days=180)

        filas = []
        if uids:
            filas = db.execute(
                select(ConversacionWpp)
                .where(
                    ConversacionWpp.usuario_id.in_(uids),
                    ConversacionWpp.fecha >= desde,
                    ConversacionWpp.tipo_mensaje.in_(
                        [TipoMensajeWpp.TEXTO, TipoMensajeWpp.AUDIO]
                    ),
                )
                .order_by(ConversacionWpp.fecha.desc())
            ).scalars().all()
    finally:
        db.close()

    total_antes = 0
    agrupados: dict[str, dict] = {}
    omitidos_residual = 0

    for row in filas:
        crudo = (
            row.transcripcion
            if row.tipo_mensaje == TipoMensajeWpp.AUDIO
            else row.mensaje_usuario
        )
        if not crudo or _es_descartable(crudo):
            continue
        total_antes += 1

        txt_anon = anonimizar(crudo)
        bot_primera = (row.mensaje_bot or "").splitlines()[0] if row.mensaje_bot else ""
        bot_anon = anonimizar(bot_primera)[:120]

        if _tiene_dato_residual(txt_anon) or _tiene_dato_residual(bot_anon):
            omitidos_residual += 1
            continue

        clave = _normalizar_dedup(txt_anon)
        fecha_ar = row.fecha.astimezone(TZ_ARGENTINA) if row.fecha else None
        fecha_str = fecha_ar.strftime("%Y-%m-%d %H:%M") if fecha_ar else ""
        intent_str = row.intent_detectado or "sin_intent"
        alias = alias_por_uid[row.usuario_id]

        if clave not in agrupados:
            agrupados[clave] = {
                "alias": alias,
                "fecha": fecha_str,
                "fecha_ord": row.fecha,
                "reps": 1,
                "intent": intent_str,
                "texto": txt_anon,
                "bot": bot_anon,
            }
        else:
            agrupados[clave]["reps"] += 1
            if row.fecha and (
                agrupados[clave]["fecha_ord"] is None
                or row.fecha > agrupados[clave]["fecha_ord"]
            ):
                agrupados[clave]["fecha_ord"] = row.fecha
                agrupados[clave]["fecha"] = fecha_str
                agrupados[clave]["alias"] = alias
                agrupados[clave]["intent"] = intent_str
                agrupados[clave]["bot"] = bot_anon

    ordenados = sorted(
        agrupados.values(),
        key=lambda x: (x["reps"], x["fecha"]),
        reverse=True,
    )[:1500]

    script_src = Path(__file__).read_text(encoding="utf-8").rstrip()
    lineas = [
        "=" * 80,
        "=== CODIGO DEL SCRIPT: scripts/evaluacion/extraer_mensajes_reales.py ===",
        "=" * 80,
        script_src,
        "",
        "=" * 80,
        f"Usuarios con permiso: {len(usuarios)}",
        f"Mensajes antes de deduplicar: {total_antes}",
        f"Mensajes después de deduplicar: {len(agrupados)}",
        f"Mensajes escritos (máx 1500): {len(ordenados)}",
        f"Omitidos por @ o >=7 dígitos tras anonimizar: {omitidos_residual}",
        "=" * 80,
        "",
    ]
    for item in ordenados:
        lineas.append(
            f"{item['alias']} | {item['fecha']} | {item['reps']} | "
            f"{item['intent']} | {item['texto']} | {item['bot']}"
        )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    print(f"Escrito: {OUT_PATH} ({len(ordenados)} mensajes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
