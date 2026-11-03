# -*- coding: utf-8 -*-
"""
scripts/demo/catalogo.py — Estructura y catálogo base de entidades del usuario demo (Lucía Demo).
Crea y obtiene billeteras, tarjetas, categorías, subcategorías, metas, presupuestos y suscripciones.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, EstadoCategoria, TipoCategoria
from app.models.meta import Meta
from app.models.presupuesto import PeriodoPresupuestoTipo, Presupuesto, RenovacionPresupuesto
from app.models.subcategoria import Subcategoria
from app.models.suscripcion import Suscripcion
from app.models.tarjeta_credito import EstadoTarjeta, RedTarjeta, TarjetaCredito
from app.models.usuario import Moneda, Usuario
from app.schemas.meta import MetaCreate
from app.schemas.presupuesto import PresupuestoCategoriaInput, PresupuestoCreate
from app.schemas.suscripcion import SuscripcionCreate
from app.services import meta_service, presupuesto_service, suscripcion_service
from scripts.demo.persona import FECHA_INICIO
from scripts.demo.resumen import fecha_a_utc_12_argentina


class CatalogoDemo:
    def __init__(self, db: Session, usuario: Usuario):
        self.db = db
        self.usuario = usuario

        # 1. Billeteras
        self.b_galicia = self._crear_o_get_billetera("Galicia", Moneda.ARS, es_principal=True)
        self.b_mp = self._crear_o_get_billetera("Mercado Pago", Moneda.ARS)
        self.b_efectivo = self._crear_o_get_billetera(
            "Efectivo",
            Moneda.ARS,
            es_efectivo=True,
            saldo_inicial=Decimal("40000.00"),
            saldo_actual=Decimal("40000.00"),
            fecha_saldo=FECHA_INICIO,
        )
        self.b_dolares = self._crear_o_get_billetera("Dólares", Moneda.USD, es_efectivo=True)
        self.b_balanz = self._crear_o_get_billetera(
            "Balanz",
            Moneda.ARS,
            es_inversion=True,
            tna=Decimal("32.00"),
        )

        # 2. Tarjeta Visa Galicia
        self.t_visa = self._crear_o_get_tarjeta(
            "Visa Galicia",
            billetera_id=self.b_galicia.id,
            dia_cierre=20,
            dia_vencimiento=3,
        )

        # 3. Categorías y Subcategorías
        self._cargar_categorias()

        # 4. Metas
        self.meta_brasil = self._crear_o_get_meta("Viaje a Brasil", Decimal("1500000.00"))
        self.meta_emergencia = self._crear_o_get_meta("Fondo de emergencia", Decimal("3000000.00"))

        # 5. Presupuestos
        self._crear_presupuestos()

        # 6. Suscripciones
        self._crear_suscripciones()

    def _crear_o_get_billetera(
        self,
        nombre: str,
        moneda: Moneda,
        es_principal: bool = False,
        es_efectivo: bool = False,
        es_inversion: bool = False,
        saldo_inicial: Decimal = Decimal("0.00"),
        saldo_actual: Decimal = Decimal("0.00"),
        fecha_saldo: Optional[object] = None,
        tna: Optional[Decimal] = None,
    ) -> Billetera:
        b = self.db.execute(
            select(Billetera).where(
                Billetera.usuario_id == self.usuario.id,
                func.lower(Billetera.nombre) == nombre.lower(),
            )
        ).scalar_one_or_none()
        if not b:
            f_saldo = fecha_saldo or FECHA_INICIO
            f_creac = fecha_a_utc_12_argentina(FECHA_INICIO)
            b = Billetera(
                usuario_id=self.usuario.id,
                nombre=nombre,
                moneda=moneda,
                es_principal=es_principal,
                es_efectivo=es_efectivo,
                es_inversion=es_inversion,
                saldo_inicial=saldo_inicial,
                saldo_actual=saldo_actual,
                fecha_saldo=f_saldo,
                fecha_creacion=f_creac,
                tna=tna,
                estado=EstadoBilletera.ACTIVA,
            )
            self.db.add(b)
            self.db.flush()
        return b

    def _crear_o_get_tarjeta(
        self,
        nombre: str,
        billetera_id: UUID,
        dia_cierre: int,
        dia_vencimiento: int,
    ) -> TarjetaCredito:
        t = self.db.execute(
            select(TarjetaCredito).where(
                TarjetaCredito.usuario_id == self.usuario.id,
                func.lower(TarjetaCredito.nombre) == nombre.lower(),
            )
        ).scalar_one_or_none()
        if not t:
            t = TarjetaCredito(
                usuario_id=self.usuario.id,
                nombre=nombre,
                red=RedTarjeta.VISA,
                billetera_id=billetera_id,
                dia_cierre=dia_cierre,
                dia_vencimiento=dia_vencimiento,
                limite_credito=Decimal("4500000.00"),
                estado=EstadoTarjeta.ACTIVA,
            )
            self.db.add(t)
            self.db.flush()
        return t

    def _cargar_categorias(self):
        cats = self.db.execute(select(Categoria)).scalars().all()
        cat_map = {c.nombre.lower(): c for c in cats}

        def get_c(nom: str) -> Categoria:
            c = cat_map.get(nom.lower())
            if not c:
                c = Categoria(nombre=nom, tipo=TipoCategoria.EGRESO, estado=EstadoCategoria.ACTIVA)
                self.db.add(c)
                self.db.flush()
                cat_map[nom.lower()] = c
            return c

        self.c_alim = get_c("Alimentación")
        self.c_gastro = get_c("Gastronomía")
        self.c_transp = get_c("Transporte")
        self.c_viv = get_c("Vivienda")
        self.c_salud = get_c("Salud")
        self.c_ocio = get_c("Ocio")
        self.c_indum = get_c("Indumentaria")
        self.c_tecno = get_c("Tecnología")
        self.c_otros_egr = get_c("Otros")
        self.c_trabajo = get_c("Trabajo")
        self.c_otros_ing = get_c("Otros")

        subs = self.db.execute(select(Subcategoria)).scalars().all()
        sub_map = {(s.categoria_id, s.nombre.lower()): s for s in subs}

        def get_s(cat: Categoria, nom: str) -> Subcategoria:
            s = sub_map.get((cat.id, nom.lower()))
            if not s:
                s = Subcategoria(categoria_id=cat.id, nombre=nom)
                self.db.add(s)
                self.db.flush()
                sub_map[(cat.id, nom.lower())] = s
            return s

        self.s_super = get_s(self.c_alim, "Supermercado")
        self.s_delivery = get_s(self.c_gastro, "Delivery")
        self.s_cafe = get_s(self.c_gastro, "Cafetería")
        self.s_resto = get_s(self.c_gastro, "Restaurantes")
        self.s_sube = get_s(self.c_transp, "Transporte público")
        self.s_alquiler = get_s(self.c_viv, "Alquiler")
        self.s_expensas = get_s(self.c_viv, "Expensas")
        self.s_luz = get_s(self.c_viv, "Luz")
        self.s_gas = get_s(self.c_viv, "Gas")
        self.s_agua = get_s(self.c_viv, "Agua")
        self.s_prepaga = get_s(self.c_salud, "Prepaga y obra social")
        self.s_farmacia = get_s(self.c_salud, "Farmacia")
        self.s_gym = get_s(self.c_ocio, "Deportes y gimnasio")
        self.s_salidas = get_s(self.c_ocio, "Salidas")
        self.s_streaming = get_s(self.c_ocio, "Streaming")
        self.s_viajes = get_s(self.c_ocio, "Viajes")
        self.s_juegos = get_s(self.c_ocio, "Hobbies y juegos")
        self.s_ropa = get_s(self.c_indum, "Ropa")
        self.s_calzado = get_s(self.c_indum, "Calzado")
        self.s_celular = get_s(self.c_tecno, "Celular")
        self.s_apps = get_s(self.c_tecno, "Apps y programas")
        self.s_equipos = get_s(self.c_tecno, "Equipos")
        self.s_regalos = get_s(self.c_otros_egr, "Regalos")
        self.s_paso_egr = get_s(self.c_otros_egr, "Pasó por mi cuenta")
        self.s_sueldo = get_s(self.c_trabajo, "Sueldo")
        self.s_sac = get_s(self.c_trabajo, "Aguinaldo")
        self.s_cobros = get_s(self.c_otros_ing, "Cobros de personas")
        self.s_paso_ing = get_s(self.c_otros_ing, "Pasó por mi cuenta")

    def _crear_o_get_meta(self, nombre: str, objetivo: Decimal) -> Meta:
        m = self.db.execute(
            select(Meta).where(
                Meta.usuario_id == self.usuario.id,
                func.lower(Meta.nombre) == nombre.lower(),
            )
        ).scalar_one_or_none()
        if not m:
            m = meta_service.crear_meta(
                self.db,
                self.usuario.id,
                MetaCreate(
                    nombre=nombre,
                    monto_objetivo=objetivo,
                    moneda=Moneda.ARS,
                ),
            )
        return m

    def _crear_presupuestos(self):
        items = [
            ("Supermercado", Decimal("350000.00"), self.c_alim.id, self.s_super.id),
            ("Delivery", Decimal("80000.00"), self.c_gastro.id, self.s_delivery.id),
            ("Salidas", Decimal("150000.00"), self.c_ocio.id, self.s_salidas.id),
            ("Café", Decimal("40000.00"), self.c_gastro.id, self.s_cafe.id),
        ]
        for nom, monto, cid, sid in items:
            p = self.db.execute(
                select(Presupuesto).where(
                    Presupuesto.usuario_id == self.usuario.id,
                    func.lower(Presupuesto.nombre) == nom.lower(),
                )
            ).scalar_one_or_none()
            if not p:
                presupuesto_service.crear_presupuesto(
                    self.db,
                    self.usuario.id,
                    PresupuestoCreate(
                        nombre=nom,
                        monto=monto,
                        moneda=Moneda.ARS,
                        periodo=PeriodoPresupuestoTipo.MENSUAL,
                        renovacion=RenovacionPresupuesto.AUTOMATICA,
                        categorias=[PresupuestoCategoriaInput(categoria_id=cid, subcategoria_id=sid)],
                    ),
                )

    def _crear_suscripciones(self):
        subs = [
            ("Netflix", Decimal("11999.00"), "ARS", self.t_visa.id, None, self.c_ocio.id, self.s_streaming.id),
            ("Spotify", Decimal("4299.00"), "ARS", self.t_visa.id, None, self.c_ocio.id, self.s_streaming.id),
            ("ChatGPT Plus", Decimal("20.00"), "USD", self.t_visa.id, None, self.c_tecno.id, self.s_apps.id),
            ("Gimnasio", Decimal("45000.00"), "ARS", None, self.b_galicia.id, self.c_ocio.id, self.s_gym.id),
            ("Prepaga", Decimal("185000.00"), "ARS", None, self.b_galicia.id, self.c_salud.id, self.s_prepaga.id),
            ("Celular", Decimal("22000.00"), "ARS", None, self.b_galicia.id, self.c_tecno.id, self.s_celular.id),
        ]
        self.suscripciones: Dict[str, Suscripcion] = {}
        for nom, monto, moneda, tid, bid, cid, sid in subs:
            s = self.db.execute(
                select(Suscripcion).where(
                    Suscripcion.usuario_id == self.usuario.id,
                    Suscripcion.nombre == nom,
                )
            ).scalar_one_or_none()
            if not s:
                s = suscripcion_service.crear_suscripcion(
                    self.db,
                    self.usuario.id,
                    SuscripcionCreate(
                        nombre=nom,
                        monto=monto,
                        moneda=moneda,
                        frecuencia="mensual",
                        proximo_cobro=FECHA_INICIO,
                        vigente_desde=FECHA_INICIO,
                        tarjeta_id=tid,
                        billetera_id=bid,
                        categoria_id=cid,
                        subcategoria_id=sid,
                    ),
                )
                s.proximo_cobro = FECHA_INICIO
                self.db.commit()
            self.suscripciones[nom] = s
