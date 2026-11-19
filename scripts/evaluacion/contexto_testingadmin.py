# -*- coding: utf-8 -*-
"""
scripts/evaluacion/contexto_testingadmin.py

Extrae la estructura de cuentas, tarjetas, metas, suscripciones y presupuestos
de testingadmin@argentum.com (solo lectura, sin ids ni montos de saldo).
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_PATH = Path(
    r"D:\Argentum\auditorias\raw\fase_m2a_preparacion"
    r"\fase_m2a_preparacion_21_contexto_testingadmin.txt"
)


def main() -> int:
    db_url = os.environ.get("DATABASE_URL", "")
    if "localhost:5433" not in db_url:
        print("FRENO: falta con_base_local.py")
        return 1

    from sqlalchemy import select
    from sqlalchemy.orm import joinedload, selectinload
    from app.core.database import SessionLocal
    from app.models.billetera import Billetera, EstadoBilletera
    from app.models.meta import Meta
    from app.models.presupuesto import EstadoPresupuesto, Presupuesto
    from app.models.presupuesto_categoria import PresupuestoCategoria
    from app.models.suscripcion import EstadoSuscripcion, Suscripcion
    from app.models.tarjeta_credito import EstadoTarjeta, TarjetaCredito
    from app.models.usuario import Usuario
    from app.services.dashboard_service import get_ciclo_fechas
    from app.utils.fecha import hoy_argentina

    db = SessionLocal()
    try:
        u = db.execute(
            select(Usuario).where(Usuario.email == "testingadmin@argentum.com")
        ).scalar_one()

        billeteras = db.execute(
            select(Billetera)
            .where(
                Billetera.usuario_id == u.id,
                Billetera.estado == EstadoBilletera.ACTIVA,
            )
            .order_by(Billetera.nombre.asc())
        ).scalars().all()

        tarjetas = db.execute(
            select(TarjetaCredito)
            .options(joinedload(TarjetaCredito.billetera))
            .where(
                TarjetaCredito.usuario_id == u.id,
                TarjetaCredito.estado == EstadoTarjeta.ACTIVA,
            )
            .order_by(TarjetaCredito.nombre.asc())
        ).scalars().all()

        metas = db.execute(
            select(Meta)
            .where(Meta.usuario_id == u.id)
            .order_by(Meta.nombre.asc())
        ).scalars().all()

        suscripciones = db.execute(
            select(Suscripcion)
            .options(
                joinedload(Suscripcion.categoria),
                joinedload(Suscripcion.subcategoria),
            )
            .where(
                Suscripcion.usuario_id == u.id,
                Suscripcion.estado == EstadoSuscripcion.ACTIVA,
            )
            .order_by(Suscripcion.nombre.asc())
        ).scalars().all()

        presupuestos = db.execute(
            select(Presupuesto)
            .options(
                selectinload(Presupuesto.categorias).joinedload(
                    PresupuestoCategoria.categoria
                ),
                selectinload(Presupuesto.categorias).joinedload(
                    PresupuestoCategoria.subcategoria
                ),
            )
            .where(
                Presupuesto.usuario_id == u.id,
                Presupuesto.estado == EstadoPresupuesto.ACTIVO,
                Presupuesto.eliminado_en.is_(None),
            )
            .order_by(Presupuesto.nombre.asc())
        ).scalars().all()

        script_src = Path(__file__).read_text(encoding="utf-8").rstrip()
        mon_ppal = (
            u.moneda_principal.value
            if hasattr(u.moneda_principal, "value")
            else str(u.moneda_principal)
        )
        ciclo_tipo = (
            u.ciclo_tipo.value
            if hasattr(u.ciclo_tipo, "value")
            else str(u.ciclo_tipo)
        )
        ini_ciclo, fin_ciclo = get_ciclo_fechas(u, hoy_argentina())
        lineas = [
            "=" * 80,
            "=== CODIGO DEL SCRIPT: scripts/evaluacion/contexto_testingadmin.py ===",
            "=" * 80,
            script_src,
            "",
            "=" * 80,
            "=== CONTEXTO DE TESTINGADMIN (SIN IDS NI MONTOS DE SALDO) ===",
            "=" * 80,
            f"Ciclo configurado: tipo={ciclo_tipo} | valor={u.ciclo_valor}",
            f"Ciclo actual: {ini_ciclo.isoformat()} a {fin_ciclo.isoformat()} "
            f"(día inicio={ini_ciclo.day})",
            f"Moneda principal: {mon_ppal}",
            "",
            "--- BILLETERAS ACTIVAS ---",
        ]
        for b in billeteras:
            mon = b.moneda.value if hasattr(b.moneda, "value") else str(b.moneda)
            lineas.append(
                f"  - {b.nombre} | moneda={mon} | inversion={b.es_inversion} | "
                f"principal={b.es_principal}"
            )

        lineas.append("\n--- TARJETAS ACTIVAS ---")
        for t in tarjetas:
            banco = t.billetera.nombre if t.billetera else "Sin banco"
            lineas.append(
                f"  - {t.nombre} | banco={banco} | "
                f"cierre=día {t.dia_cierre} | vencimiento=día {t.dia_vencimiento}"
            )

        lineas.append("\n--- METAS ---")
        for m in metas:
            mon = m.moneda.value if hasattr(m.moneda, "value") else str(m.moneda)
            est = m.estado.value if hasattr(m.estado, "value") else str(m.estado)
            lineas.append(f"  - {m.nombre} | moneda={mon} | estado={est}")

        lineas.append("\n--- SUSCRIPCIONES ACTIVAS ---")
        for s in suscripciones:
            frec = (
                s.frecuencia.value
                if hasattr(s.frecuencia, "value")
                else str(s.frecuencia)
            )
            cat_n = s.categoria.nombre if s.categoria else "Sin categoría"
            sub_n = s.subcategoria.nombre if s.subcategoria else "Otros"
            lineas.append(f"  - {s.nombre} | frecuencia={frec} | {cat_n} › {sub_n}")

        lineas.append("\n--- PRESUPUESTOS ACTIVOS ---")
        for p in presupuestos:
            per = p.periodo.value if hasattr(p.periodo, "value") else str(p.periodo)
            mon = p.moneda.value if hasattr(p.moneda, "value") else str(p.moneda)
            cats = []
            for pc in p.categorias:
                cn = pc.categoria.nombre if pc.categoria else None
                sn = pc.subcategoria.nombre if pc.subcategoria else None
                if cn and sn:
                    cats.append(f"{cn} › {sn}")
                elif cn:
                    cats.append(cn)
                elif sn:
                    cats.append(sn)
            lineas.append(
                f"  - {p.nombre} | periodo={per} | moneda={mon} | "
                f"categorias={', '.join(sorted(cats))}"
            )
    finally:
        db.close()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lineas) + "\n", encoding="utf-8", newline="\n")
    print(f"Escrito: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
