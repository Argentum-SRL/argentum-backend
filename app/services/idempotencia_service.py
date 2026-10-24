"""
app/services/idempotencia_service.py

Servicio para manejo de idempotencia en operaciones que mueven fondos.
Garantiza que una operación repetida con la misma clave se registre una sola vez.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.clave_idempotencia import ClaveIdempotencia

PATRON_CLAVE = re.compile(r"^[A-Za-z0-9_-]{8,100}$")


def leer_clave(request: Request) -> str | None:
    """
    Lee y valida el encabezado Idempotency-Key del pedido.
    Debe tener entre 8 y 100 caracteres alfanuméricos, guiones o guiones bajos.
    Si no viene el encabezado retorna None.
    Si viene con formato inválido lanza HTTP 400.
    """
    clave = request.headers.get("Idempotency-Key") or request.headers.get("idempotency-key")
    if clave is None:
        return None

    if not PATRON_CLAVE.fullmatch(clave):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La clave de la operación no es válida.",
        )
    return clave


def hash_pedido(metodo: str, ruta: str, cuerpo: dict | None) -> str:
    """
    Genera el digest SHA-256 en formato hex del pedido.
    Incluye método, ruta y cuerpo normalizado.
    """
    payload = {
        "metodo": metodo,
        "ruta": ruta,
        "cuerpo": cuerpo or {},
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ejecutar(
    db: Session,
    request: Request,
    usuario_id: UUID,
    cuerpo: dict | None,
    operar: Callable[[], Any],
    serializar: Callable[[Any], dict],
    codigo: int,
) -> JSONResponse:
    """
    Ejecuta una operación financiera de forma idempotente:
    1. Si no hay clave, opera normalmente con commit único.
    2. Con clave, si ya existe y coincide hash/ruta devuelve la respuesta guardada;
       si difiere lanza 422.
    3. Si no existe, reserva la fila mediante un savepoint nested. Ante concurrencia
       (IntegrityError) recupera la respuesta de la otra transacción.
    4. Ejecuta operar(), serializa y persiste la respuesta en la misma transacción.
    5. Ante cualquier excepción deshace todo (db.rollback()).
    """
    clave = leer_clave(request)
    if clave is None:
        resultado = operar()
        db.flush()
        datos = serializar(resultado)
        db.commit()
        return JSONResponse(status_code=codigo, content=datos)

    h_esperado = hash_pedido(request.method, request.url.path, cuerpo)

    try:
        fila = (
            db.query(ClaveIdempotencia)
            .filter_by(usuario_id=usuario_id, clave=clave)
            .first()
        )
        if fila is not None and fila.codigo_http != 0:
            if fila.ruta == request.url.path and fila.hash_cuerpo == h_esperado:
                return JSONResponse(status_code=fila.codigo_http, content=fila.respuesta)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Esta operación ya se registró con otros datos. Cerrá y volvé a abrir para cargar una nueva.",
            )

        if fila is not None and fila.codigo_http == 0:
            for _ in range(30):
                time.sleep(0.05)
                db.expire_all()
                fila = (
                    db.query(ClaveIdempotencia)
                    .filter_by(usuario_id=usuario_id, clave=clave)
                    .first()
                )
                if fila is not None and fila.codigo_http != 0:
                    if fila.ruta == request.url.path and fila.hash_cuerpo == h_esperado:
                        return JSONResponse(status_code=fila.codigo_http, content=fila.respuesta)
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Esta operación ya se registró con otros datos. Cerrá y volvé a abrir para cargar una nueva.",
                    )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta operación se está procesando. Probá de nuevo en un momento.",
            )

        fila_actual = None
        try:
            with db.begin_nested():
                nueva_fila = ClaveIdempotencia(
                    usuario_id=usuario_id,
                    clave=clave,
                    ruta=request.url.path,
                    hash_cuerpo=h_esperado,
                    codigo_http=0,
                    respuesta={},
                )
                db.add(nueva_fila)
                db.flush()
                fila_actual = nueva_fila
        except IntegrityError:
            # Concurrencia: otra transacción insertó la misma clave simultáneamente
            db.expire_all()
            fila_concurrente = None
            for _ in range(30):
                fila_concurrente = (
                    db.query(ClaveIdempotencia)
                    .filter_by(usuario_id=usuario_id, clave=clave)
                    .first()
                )
                if fila_concurrente is not None and fila_concurrente.codigo_http != 0:
                    break
                time.sleep(0.05)
                db.expire_all()

            if fila_concurrente is not None and fila_concurrente.codigo_http != 0:
                if fila_concurrente.ruta == request.url.path and fila_concurrente.hash_cuerpo == h_esperado:
                    return JSONResponse(
                        status_code=fila_concurrente.codigo_http,
                        content=fila_concurrente.respuesta,
                    )
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Esta operación ya se registró con otros datos. Cerrá y volvé a abrir para cargar una nueva.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Esta operación se está procesando. Probá de nuevo en un momento.",
            )

        resultado = operar()
        db.flush()
        datos = serializar(resultado)
        fila_actual.codigo_http = codigo
        fila_actual.respuesta = datos
        db.commit()
        return JSONResponse(status_code=codigo, content=datos)
    except Exception:
        db.rollback()
        raise


def purgar_claves_idempotencia(db: Session) -> int:
    """
    Elimina registros de claves de idempotencia con más de 24 horas de antigüedad.
    Realiza commit y retorna la cantidad de filas borradas.
    """
    limite = datetime.now(timezone.utc) - timedelta(hours=24)
    stmt = delete(ClaveIdempotencia).where(ClaveIdempotencia.creada_en < limite)
    res = db.execute(stmt)
    db.commit()
    return int(res.rowcount or 0)
