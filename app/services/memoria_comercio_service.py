"""
app/services/memoria_comercio_service.py — Servicio de memoria de categorización por comercio.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import desc, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.memoria_comercio import MemoriaComercio
from app.models.subcategoria import EstadoSubcategoria, Subcategoria
from app.models.transaccion import EstadoVerificacionTransaccion, TipoTransaccion, Transaccion
from app.schemas.transaccion import TransaccionUpdate
from app.services import transaccion_service
from app.utils.texto import normalizar_texto

STOPWORDS_COMERCIO = {
    "gasto", "gaste", "pago", "pague", "compra", "compre",
    "en", "el", "la", "los", "las", "un", "una", "de", "del", "al", "a",
    "con", "por", "para",
}


def clave_comercio(descripcion: str | None) -> str | None:
    """
    Función pura que extrae la clave canónica de un comercio a partir de una descripción.
    - normalizar_texto (minúsculas y sin acentos).
    - quitar números y signos (todo lo que no sea letra o espacio).
    - partir en palabras y descartar stopwords comunes de gastos/pagos.
    - a cada palabra de más de 4 letras que termine en 's', quitarle esa 's'.
    - unir con un espacio y recortar a 120 caracteres.
    - si queda vacío, devuelve None.
    """
    if not descripcion:
        return None
    texto = normalizar_texto(descripcion)
    # Quitar números y signos: todo lo que no sea letra (a-z) o espacio
    texto = re.sub(r"[^a-z\s]", " ", texto)
    palabras = texto.split()
    filtradas: list[str] = []
    for p in palabras:
        if p in STOPWORDS_COMERCIO:
            continue
        if len(p) > 4 and p.endswith("s"):
            p = p[:-1]
        filtradas.append(p)
    if not filtradas:
        return None
    clave = " ".join(filtradas)[:120].strip()
    return clave if clave else None


def buscar(
    db: Session,
    usuario_id: UUID,
    descripcion: str | None,
    tipo: str,
) -> MemoriaComercio | None:
    """
    Busca la memoria asociada al comercio y tipo para un usuario dado.
    """
    clave = clave_comercio(descripcion)
    if not clave:
        return None
    tipo_str = tipo.value if hasattr(tipo, "value") else str(tipo).lower()
    return db.execute(
        select(MemoriaComercio)
        .options(joinedload(MemoriaComercio.categoria), joinedload(MemoriaComercio.subcategoria))
        .where(
            MemoriaComercio.usuario_id == usuario_id,
            MemoriaComercio.clave == clave,
            MemoriaComercio.tipo == tipo_str,
        )
    ).scalars().first()


def guardar(
    db: Session,
    usuario_id: UUID,
    descripcion: str,
    tipo: str,
    categoria_id: UUID,
    subcategoria_id: UUID | None,
    commit: bool = True,
) -> MemoriaComercio:
    """
    Guarda o actualiza la memoria de un comercio para el usuario.
    Valida que la categoría y subcategoría correspondan y estén activas.
    """
    clave = clave_comercio(descripcion)
    if not clave:
        raise HTTPException(
            status_code=400,
            detail="No pudimos reconocer el comercio en esa descripción.",
        )

    tipo_str = tipo.value if hasattr(tipo, "value") else str(tipo).lower()
    tipo_enum = TipoCategoria.INGRESO if tipo_str == "ingreso" else TipoCategoria.EGRESO

    # Validar categoría
    categoria = db.execute(
        select(Categoria).where(
            Categoria.id == categoria_id,
            Categoria.estado == EstadoCategoria.ACTIVA,
            Categoria.tipo == tipo_enum,
        )
    ).scalar_one_or_none()
    if not categoria:
        raise HTTPException(status_code=400, detail="Esa categoría no corresponde.")

    # Validar subcategoría si se especificó
    if subcategoria_id:
        subcategoria = db.execute(
            select(Subcategoria).where(
                Subcategoria.id == subcategoria_id,
                Subcategoria.categoria_id == categoria_id,
                Subcategoria.estado == EstadoSubcategoria.ACTIVA,
            )
        ).scalar_one_or_none()
        if not subcategoria:
            raise HTTPException(status_code=400, detail="Esa categoría no corresponde.")

    # Upsert por (usuario_id, clave, tipo)
    memoria = db.execute(
        select(MemoriaComercio).where(
            MemoriaComercio.usuario_id == usuario_id,
            MemoriaComercio.clave == clave,
            MemoriaComercio.tipo == tipo_str,
        )
    ).scalar_one_or_none()

    ahora = datetime.now(timezone.utc)
    if memoria:
        memoria.categoria_id = categoria_id
        memoria.subcategoria_id = subcategoria_id
        memoria.fecha_actualizacion = ahora
    else:
        memoria = MemoriaComercio(
            usuario_id=usuario_id,
            clave=clave,
            tipo=tipo_str,
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            fecha_creacion=ahora,
            fecha_actualizacion=ahora,
        )
        db.add(memoria)

    if commit:
        db.commit()
        db.refresh(memoria)
    else:
        db.flush()

    return memoria


def anteriores_distintos(
    db: Session,
    usuario_id: UUID,
    memoria: MemoriaComercio,
) -> list[Transaccion]:
    """
    Retorna transacciones confirmadas del usuario, de ese tipo, que no son cuota hija,
    cuya clave_comercio(descripcion) es igual a la clave de la memoria y cuya
    (categoria_id, subcategoria_id) es distinta de la de la memoria.
    Ordenadas por fecha descendente, hasta 200.
    """
    tipo_str = memoria.tipo.value if hasattr(memoria.tipo, "value") else str(memoria.tipo).lower()
    tipo_enum = TipoTransaccion.INGRESO if tipo_str == "ingreso" else TipoTransaccion.EGRESO

    stmt = (
        select(Transaccion)
        .options(joinedload(Transaccion.categoria), joinedload(Transaccion.subcategoria))
        .where(
            Transaccion.usuario_id == usuario_id,
            Transaccion.tipo == tipo_enum,
            Transaccion.es_cuota_hija.is_(False),
            or_(
                Transaccion.estado_verificacion.is_(None),
                Transaccion.estado_verificacion != EstadoVerificacionTransaccion.PENDIENTE,
            ),
        )
        .order_by(desc(Transaccion.fecha), desc(Transaccion.fecha_creacion))
    )

    candidatas = db.execute(stmt).scalars().all()
    resultado: list[Transaccion] = []

    for tx in candidatas:
        # Verificar si la categoría/subcategoría es distinta a la de la memoria
        es_distinta = (
            tx.categoria_id != memoria.categoria_id or
            tx.subcategoria_id != memoria.subcategoria_id
        )
        if es_distinta and clave_comercio(tx.descripcion) == memoria.clave:
            resultado.append(tx)
            if len(resultado) == 200:
                break

    return resultado


def aplicar_a_anteriores(
    db: Session,
    usuario_id: UUID,
    memoria: MemoriaComercio,
    transaccion_ids: list[UUID],
    commit: bool = True,
) -> dict[str, int]:
    """
    Aplica la categoría y subcategoría de la memoria a las transacciones anteriores seleccionadas.
    Solo modifica las que cumplen las condiciones de anteriores_distintos; las demás cuentan como omitidas.
    """
    candidatas_validas = anteriores_distintos(db, usuario_id, memoria)
    validas_dict = {tx.id: tx for tx in candidatas_validas}

    actualizadas = 0
    omitidas = 0

    for tid in transaccion_ids:
        if tid in validas_dict:
            transaccion_service.actualizar_transaccion(
                db=db,
                usuario_id=usuario_id,
                transaccion_id=tid,
                data=TransaccionUpdate(
                    categoria_id=memoria.categoria_id,
                    subcategoria_id=memoria.subcategoria_id,
                ),
                commit=False,
            )
            actualizadas += 1
        else:
            omitidas += 1

    if commit and actualizadas > 0:
        db.commit()

    return {"actualizadas": actualizadas, "omitidas": omitidas}


def aplicar_memoria_a_movimiento(
    db: Session,
    usuario_id: UUID,
    movimiento: dict,
    tipo: str,
) -> bool:
    """
    Aplica la memoria de comercio a un diccionario de movimiento de WhatsApp si existe coincidencia.
    Actualiza movimiento['categoria'] con 'Categoría > Subcategoría' o 'Categoría'.
    """
    texto = movimiento.get("descripcion") or movimiento.get("item")
    memoria = buscar(db, usuario_id, texto, tipo)
    if memoria:
        cat_nombre = memoria.categoria.nombre if memoria.categoria else (
            db.get(Categoria, memoria.categoria_id).nombre if memoria.categoria_id else ""
        )
        if memoria.subcategoria_id:
            sub_nombre = memoria.subcategoria.nombre if memoria.subcategoria else (
                db.get(Subcategoria, memoria.subcategoria_id).nombre if memoria.subcategoria_id else None
            )
            if sub_nombre:
                movimiento["categoria"] = f"{cat_nombre} > {sub_nombre}"
            else:
                movimiento["categoria"] = cat_nombre
        else:
            movimiento["categoria"] = cat_nombre
        return True
    return False
