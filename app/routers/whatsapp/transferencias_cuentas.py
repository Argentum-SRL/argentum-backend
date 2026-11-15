# -*- coding: utf-8 -*-
"""
Resolución de cuentas y billeteras para transferencias entre cuentas propias.
Maneja entidades bancarias, billeteras activas y candidatas de selección.
"""
from __future__ import annotations

import re
from uuid import UUID
from sqlalchemy.orm import Session

from app.core.entidades import ALIAS_ENTIDADES, entidad_de_texto
from app.models.billetera import Billetera, EstadoBilletera
from app.models.usuario import Moneda
from app.routers.whatsapp.resolvers_cascada import resolver_billetera_cascada
from app.utils.texto import normalizar_texto


def billeteras_para_transferir(usuario_id: UUID, db: Session) -> list[Billetera]:
    """
    Retorna billeteras activas del usuario (incluidas las de inversión),
    ordenadas por es_principal desc, nombre asc, id asc.
    """
    return (
        db.query(Billetera)
        .filter(
            Billetera.usuario_id == usuario_id,
            Billetera.estado == EstadoBilletera.ACTIVA,
        )
        .order_by(
            Billetera.es_principal.desc(),
            Billetera.nombre.asc(),
            Billetera.id.asc(),
        )
        .all()
    )


def billetera_es_de_entidad(b: Billetera, ent_id: str) -> bool:
    """
    Determina si una billetera pertenece a una entidad determinada por ID,
    por nombre normalizado o por coincidencia de palabras completas de alias.
    """
    if not ent_id:
        return False
    if getattr(b, "entidad_efectiva", None) == ent_id:
        return True
    if entidad_de_texto(b.nombre) == ent_id:
        return True
    nom_norm = normalizar_texto(b.nombre)
    for alias, eid in ALIAS_ENTIDADES.items():
        if eid == ent_id:
            if re.search(r"\b" + re.escape(alias) + r"\b", nom_norm):
                return True
    return False


def _moneda_str(m: Moneda | str | None) -> str | None:
    if m is None:
        return None
    return m.value if hasattr(m, "value") else str(m)


def resolver_cuenta(
    texto: str,
    billeteras: list[Billetera],
    moneda_hint: Moneda | str | None = None,
) -> tuple[str, Billetera | None, list[Billetera], str | None]:
    """
    Resuelve una cuenta entre las billeteras disponibles:
    1) Cascada de nombres y alias existentes.
    2) Efectivo / cash / cajero según moneda_hint o ARS.
    3) Catálogo oficial de entidades bancarias.
    4) Desconocida.
    Aplica Regla 5A para alternar a cuenta de la misma entidad en moneda escrita.
    """
    if not texto:
        return "desconocida", None, [], None

    t = normalizar_texto(texto)
    m_hint = _moneda_str(moneda_hint)

    # 1. resolver_billetera_cascada
    b_cascada, cands_cascada = resolver_billetera_cascada(texto, billeteras)
    if b_cascada is not None:
        res_b = b_cascada
        # Regla 5A
        if m_hint and _moneda_str(res_b.moneda) != m_hint:
            ent = getattr(res_b, "entidad_efectiva", None) or entidad_de_texto(res_b.nombre)
            if ent:
                otras = [
                    x for x in billeteras
                    if billetera_es_de_entidad(x, ent) and _moneda_str(x.moneda) == m_hint
                ]
                if len(otras) == 1:
                    return "ok", otras[0], [otras[0]], ent
        ent = getattr(res_b, "entidad_efectiva", None) or entidad_de_texto(res_b.nombre)
        return "ok", res_b, [res_b], ent
    elif len(cands_cascada) > 1:
        if m_hint:
            filtradas = [c for c in cands_cascada if _moneda_str(c.moneda) == m_hint]
            if len(filtradas) == 1:
                res_b = filtradas[0]
                ent = getattr(res_b, "entidad_efectiva", None) or entidad_de_texto(res_b.nombre)
                return "ok", res_b, [res_b], ent
            elif len(filtradas) > 1:
                return "ambigua", None, filtradas, None
        return "ambigua", None, cands_cascada, None

    # 2. "efectivo", "cash", "cajero"
    if t in ("efectivo", "cash", "cajero"):
        target_mon = m_hint or "ARS"
        efectivos = [
            b for b in billeteras
            if b.es_efectivo and _moneda_str(b.moneda) == target_mon
        ]
        if len(efectivos) == 1:
            return "ok", efectivos[0], [efectivos[0]], None
        elif len(efectivos) == 0:
            return "sin_efectivo", None, [], None
        else:
            return "ambigua", None, efectivos, None

    # 3. ent = entidad_de_texto(texto)
    ent = entidad_de_texto(texto)
    if ent:
        de_ent = [b for b in billeteras if billetera_es_de_entidad(b, ent)]
        if m_hint:
            filtradas = [b for b in de_ent if _moneda_str(b.moneda) == m_hint]
            if len(filtradas) == 1:
                return "ok", filtradas[0], [filtradas[0]], ent
            elif len(filtradas) > 1:
                return "ambigua", None, filtradas, ent
            elif len(de_ent) == 1:
                # Si solo tiene en otra moneda, devuelve esa para regla 5A posterior
                return "ok", de_ent[0], [de_ent[0]], ent
            elif len(de_ent) > 1:
                return "ambigua", None, de_ent, ent
            else:
                return "no_cargada", None, [], ent
        else:
            if len(de_ent) == 1:
                return "ok", de_ent[0], [de_ent[0]], ent
            elif len(de_ent) > 1:
                return "ambigua", None, de_ent, ent
            else:
                return "no_cargada", None, [], ent

    # 4. desconocida
    return "desconocida", None, [], None


def candidatas_menu(
    billeteras: list[Billetera],
    moneda: Moneda | str,
    excluir_id: UUID | None,
    solo_bancos: bool = False,
) -> list[Billetera]:
    """
    Retorna billeteras sin inversión de la moneda dada, excluyendo la indicada,
    y sin billeteras de efectivo si solo_bancos es True.
    """
    m_str = _moneda_str(moneda)
    cands = []
    for b in billeteras:
        if b.es_inversion:
            continue
        if _moneda_str(b.moneda) != m_str:
            continue
        if excluir_id and b.id == excluir_id:
            continue
        if solo_bancos and b.es_efectivo:
            continue
        cands.append(b)
    return cands
