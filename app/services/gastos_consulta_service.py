from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.categoria import Categoria, EstadoCategoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
from app.models.transaccion import (
    EstadoVerificacionTransaccion,
    MetodoPago,
    TipoTransaccion,
    Transaccion,
)
from app.models.usuario import Moneda


def _condicion_gasto(usuario_id, desde, hasta):
    return and_(
        Transaccion.usuario_id == usuario_id,
        Transaccion.tipo == TipoTransaccion.EGRESO,
        Transaccion.fecha >= desde,
        Transaccion.fecha <= hasta,
        Transaccion.es_padre_cuotas == False,
        Transaccion.metodo_pago.is_distinct_from(MetodoPago.CREDITO),
        Transaccion.movimiento_meta_id.is_(None),
        ~Transaccion.descripcion.ilike("Aporte a la meta:%"),
        ~Transaccion.descripcion.ilike("Retiro de la meta:%"),
        or_(
            Transaccion.estado_verificacion == EstadoVerificacionTransaccion.CONFIRMADA,
            Transaccion.estado_verificacion == None,
        ),
    )


def calcular_gastos_periodo(
    db: Session,
    usuario_id: UUID,
    desde: date,
    hasta: date,
    categoria_id=None,
    subcategoria_id=None,
    texto_descripcion: str | None = None,
    top_n: int = 3,
) -> dict:
    cond = _condicion_gasto(usuario_id, desde, hasta)
    extras = []
    if subcategoria_id is not None:
        extras.append(Transaccion.subcategoria_id == subcategoria_id)
    elif categoria_id is not None:
        extras.append(Transaccion.categoria_id == categoria_id)
    if texto_descripcion:
        patron = (
            "%"
            + texto_descripcion.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
            + "%"
        )
        extras.append(Transaccion.descripcion.ilike(patron, escape="\\"))
    filas = db.execute(
        select(
            Transaccion.moneda,
            func.coalesce(func.sum(Transaccion.monto), 0),
            func.count(Transaccion.id),
        )
        .where(cond, *extras)
        .group_by(Transaccion.moneda)
    ).all()
    res = {
        "ars": {"total": Decimal("0"), "cantidad": 0},
        "usd": {"total": Decimal("0"), "cantidad": 0},
        "top_categorias_ars": [],
    }
    for moneda, total, cantidad in filas:
        clave = "usd" if moneda == Moneda.USD else "ars"
        res[clave] = {"total": Decimal(str(total)), "cantidad": int(cantidad)}
    if top_n > 0 and res["ars"]["cantidad"] > 0:
        filas_cat = db.execute(
            select(Categoria.nombre, func.sum(Transaccion.monto))
            .select_from(Transaccion)
            .outerjoin(Categoria, Categoria.id == Transaccion.categoria_id)
            .where(cond, Transaccion.moneda == Moneda.ARS, *extras)
            .group_by(Categoria.nombre)
            .order_by(func.sum(Transaccion.monto).desc())
            .limit(top_n)
        ).all()
        res["top_categorias_ars"] = [
            (nombre or "Sin categoría", float(total)) for nombre, total in filas_cat
        ]
    return res


def resolver_concepto_catalogo(
    db: Session, concepto: str, tipo: str = "egreso"
) -> dict | None:
    from app.utils.texto import normalizar_texto

    objetivo = normalizar_texto(concepto)
    if not objetivo:
        return None
    variantes = {objetivo, objetivo[:-1] if objetivo.endswith("s") else objetivo + "s"}
    subcategorias = (
        db.execute(
            select(Subcategoria)
            .options(joinedload(Subcategoria.categoria))
            .where(Subcategoria.estado == EstadoSubcategoria.ACTIVA)
            .order_by(
                Subcategoria.orden.asc(),
                Subcategoria.nombre.asc(),
                Subcategoria.id.asc(),
            )
        )
        .scalars()
        .all()
    )
    categorias = (
        db.execute(
            select(Categoria)
            .where(Categoria.estado == EstadoCategoria.ACTIVA)
            .order_by(Categoria.nombre.asc(), Categoria.id.asc())
        )
        .scalars()
        .all()
    )

    candidatos_sub = [s for s in subcategorias if normalizar_texto(s.nombre) in variantes]
    if len(candidatos_sub) == 1:
        s = candidatos_sub[0]
        return {
            "categoria_id": s.categoria_id,
            "subcategoria_id": s.id,
            "nombre": s.nombre,
        }
    elif len(candidatos_sub) > 1:
        candidatos_tipo = [
            s for s in candidatos_sub
            if s.categoria and getattr(s.categoria.tipo, "value", s.categoria.tipo) == tipo
        ]
        pool = candidatos_tipo if candidatos_tipo else candidatos_sub
        if len(pool) == 1:
            s = pool[0]
            return {
                "categoria_id": s.categoria_id,
                "subcategoria_id": s.id,
                "nombre": s.nombre,
            }

        tipo_tx = TipoTransaccion.EGRESO if tipo == "egreso" else TipoTransaccion.INGRESO
        con_tx = []
        for s in pool:
            cnt = db.execute(
                select(func.count(Transaccion.id)).where(
                    Transaccion.subcategoria_id == s.id,
                    Transaccion.tipo == tipo_tx,
                )
            ).scalar() or 0
            con_tx.append((cnt, s))

        con_tx.sort(key=lambda item: item[0], reverse=True)
        s_ganador = con_tx[0][1]
        return {
            "categoria_id": s_ganador.categoria_id,
            "subcategoria_id": s_ganador.id,
            "nombre": s_ganador.nombre,
        }

    candidatos_cat = [c for c in categorias if normalizar_texto(c.nombre) in variantes]
    if len(candidatos_cat) == 1:
        c = candidatos_cat[0]
        return {"categoria_id": c.id, "subcategoria_id": None, "nombre": c.nombre}
    elif len(candidatos_cat) > 1:
        candidatos_tipo = [
            c for c in candidatos_cat
            if getattr(c.tipo, "value", c.tipo) == tipo
        ]
        pool = candidatos_tipo if candidatos_tipo else candidatos_cat
        if len(pool) == 1:
            c = pool[0]
            return {"categoria_id": c.id, "subcategoria_id": None, "nombre": c.nombre}

        tipo_tx = TipoTransaccion.EGRESO if tipo == "egreso" else TipoTransaccion.INGRESO
        con_tx = []
        for c in pool:
            cnt = db.execute(
                select(func.count(Transaccion.id)).where(
                    Transaccion.categoria_id == c.id,
                    Transaccion.tipo == tipo_tx,
                )
            ).scalar() or 0
            con_tx.append((cnt, c))

        con_tx.sort(key=lambda item: item[0], reverse=True)
        c_ganador = con_tx[0][1]
        return {"categoria_id": c_ganador.id, "subcategoria_id": None, "nombre": c_ganador.nombre}

    return None
