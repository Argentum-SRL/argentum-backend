"""
Módulo de Catálogos Base, Entidades Financieras, IPC y Calendario de Ciclos.

Provee:
1. Catálogo de Billeteras requeridas para testingadmin.
2. Catálogo de Tarjetas de Crédito de testingadmin.
3. Mapeo de Categorías y Subcategorías del sistema.
4. Consulta y cálculo de ajuste trimestral según IPCCache real.
5. Cálculo determinístico de los 14 ciclos financieros usando las funciones
   oficiales de la app (`calcular_inicio_ciclo_para_mes_ancla` y `get_ciclo_fechas`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria
from app.models.subcategoria import Subcategoria
from app.models.tarjeta_credito import TarjetaCredito, RedTarjeta
from app.models.tools import IPCCache
from app.models.usuario import Usuario, Moneda
from app.services.dashboard_service import get_ciclo_fechas, calcular_inicio_ciclo_para_mes_ancla


@dataclass
class CicloInfo:
    numero: int
    fecha_inicio: date
    fecha_fin: date
    fecha_sueldo: date
    anio_ancla: int
    mes_ancla: int

    @property
    def dias(self) -> int:
        return (self.fecha_fin - self.fecha_inicio).days + 1


class CatalogoEntidades:
    def __init__(self, db: Session, user: Usuario):
        self.db = db
        self.user = user

        # 1. Billeteras
        self.billeteras: Dict[str, Billetera] = {}
        for b in db.query(Billetera).filter(Billetera.usuario_id == user.id).all():
            self.billeteras[b.nombre] = b

        # Asegurar Billetera de inversión "Ahorro con rendimiento"
        if "Ahorro con rendimiento" not in self.billeteras:
            b_inv = Billetera(
                usuario_id=user.id,
                nombre="Ahorro con rendimiento",
                moneda=Moneda.ARS,
                saldo_inicial=Decimal("0.00"),
                saldo_actual=Decimal("0.00"),
                es_principal=False,
                es_efectivo=False,
                es_inversion=True,
                tna=Decimal("34.00"),
                fecha_ultimo_rendimiento=datetime.now(timezone.utc),
                estado=EstadoBilletera.ACTIVA,
            )
            db.add(b_inv)
            db.commit()
            db.refresh(b_inv)
            self.billeteras["Ahorro con rendimiento"] = b_inv
        else:
            b_inv = self.billeteras["Ahorro con rendimiento"]
            b_inv.tna = Decimal("34.00")
            b_inv.es_inversion = True
            db.commit()

        self.b_galicia = self.billeteras["Galicia"]
        self.b_santander = self.billeteras["Santander"]
        self.b_efectivo_ars = self.billeteras["Efectivo ARS"]
        self.b_efectivo_usd = self.billeteras["Efectivo USD"]
        self.b_inversion = self.billeteras["Ahorro con rendimiento"]

        # 2. Tarjetas
        tarjetas_db = db.query(TarjetaCredito).filter(TarjetaCredito.usuario_id == user.id).all()
        self.tarjetas: Dict[str, TarjetaCredito] = {t.nombre: t for t in tarjetas_db}
        self.t_visa_galicia = self.tarjetas.get("•••• 1506") or [t for t in tarjetas_db if t.red == RedTarjeta.VISA and t.billetera_id == self.b_galicia.id][0]
        self.t_amex_galicia = self.tarjetas.get("•••• 2745") or [t for t in tarjetas_db if t.red == RedTarjeta.AMEX][0]
        self.t_visa_santander = self.tarjetas.get("•••• 5077") or [t for t in tarjetas_db if t.billetera_id == self.b_santander.id][0]

        # 3. Categorías y Subcategorías
        self._cargar_categorias()

        # 4. IPC Cache
        self._cargar_ipc()

        # 5. Calendario de Ciclos
        self._construir_ciclos()

    def _cargar_categorias(self):
        def find_cat(nombre: str, tipo: str = "egreso") -> Categoria:
            c = self.db.query(Categoria).filter(Categoria.nombre.ilike(nombre), Categoria.tipo == tipo).first()
            if not c:
                raise RuntimeError(f"Categoría requerida '{nombre}' ({tipo}) no encontrada en BD.")
            return c

        def find_sub(cat: Categoria, nombre: str) -> Subcategoria:
            s = self.db.query(Subcategoria).filter(Subcategoria.categoria_id == cat.id, Subcategoria.nombre.ilike(nombre)).first()
            if not s:
                raise RuntimeError(f"Subcategoría requerida '{nombre}' de '{cat.nombre}' no encontrada en BD.")
            return s

        # Ingresos
        self.cat_empleo = find_cat("Empleo", "ingreso")
        self.sub_sueldo = find_sub(self.cat_empleo, "Sueldo")
        self.sub_aguinaldo = find_sub(self.cat_empleo, "Aguinaldo")

        self.cat_indep = find_cat("Trabajo independiente", "ingreso")
        self.sub_honorarios = find_sub(self.cat_indep, "Honorarios")
        self.sub_venta = find_sub(self.cat_indep, "Venta de productos/servicios")

        # Egresos Fijos y Servicios
        self.cat_vivienda = find_cat("Vivienda", "egreso")
        self.sub_alquiler = find_sub(self.cat_vivienda, "Alquiler")
        self.sub_expensas = find_sub(self.cat_vivienda, "Expensas")
        self.sub_luz = find_sub(self.cat_vivienda, "Luz")
        self.sub_gas = find_sub(self.cat_vivienda, "Gas")
        self.sub_agua = find_sub(self.cat_vivienda, "Agua")

        self.cat_comun = find_cat("Comunicación", "egreso")
        self.sub_celular = find_sub(self.cat_comun, "Celular")
        self.sub_internet = find_sub(self.cat_comun, "Internet y cable")

        self.cat_salud = find_cat("Salud", "egreso")
        self.sub_farmacia = find_sub(self.cat_salud, "Farmacia")
        self.sub_gimnasio = find_sub(self.cat_salud, "Deportes y gimnasio")

        # Egresos Variables y Día a Día
        self.cat_alim = find_cat("Alimentación", "egreso")
        self.sub_super = find_sub(self.cat_alim, "Supermercado")
        self.sub_carne = find_sub(self.cat_alim, "Carnicería")
        self.sub_verdu = find_sub(self.cat_alim, "Verdulería")
        self.sub_kiosco = find_sub(self.cat_alim, "Kiosco")

        self.cat_gastro = find_cat("Gastronomía", "egreso")
        self.sub_resto = find_sub(self.cat_gastro, "Restaurantes")
        self.sub_delivery = find_sub(self.cat_gastro, "Delivery")
        self.sub_cafe = find_sub(self.cat_gastro, "Cafetería")

        self.cat_transp = find_cat("Transporte", "egreso")
        self.sub_comb = find_sub(self.cat_transp, "Combustible")
        self.sub_transp_pub = find_sub(self.cat_transp, "Transporte público")
        self.sub_taxi = find_sub(self.cat_transp, "Taxi / Apps")

        self.cat_indum = find_cat("Indumentaria", "egreso")
        self.sub_ropa = find_sub(self.cat_indum, "Ropa")
        self.sub_calzado = find_sub(self.cat_indum, "Calzado")

        self.cat_recreo = find_cat("Recreativo", "egreso")
        self.sub_salidas = find_sub(self.cat_recreo, "Salidas")
        self.sub_hobbies = find_sub(self.cat_recreo, "Hobbies y juegos")

        self.cat_hogar_eq = find_cat("Equipamiento del hogar", "egreso")
        self.sub_muebles = find_sub(self.cat_hogar_eq, "Muebles y electrodomésticos")
        self.sub_limpieza = find_sub(self.cat_hogar_eq, "Limpieza")

        self.cat_otros_egr = find_cat("Otros", "egreso")
        self.sub_cuidado = find_sub(self.cat_otros_egr, "Cuidado personal")

    def _cargar_ipc(self):
        rows = self.db.query(IPCCache).all()
        self.ipc_dict: Dict[str, float] = {r.fecha_dato: r.indice_acumulado for r in rows}

    def obtener_coeficiente_ipc(self, fecha_base: str, fecha_tope: str) -> Decimal:
        """Calcula el factor acumulado de IPC entre dos meses ('YYYY-MM')."""
        val_base = self.ipc_dict.get(fecha_base)
        val_tope = self.ipc_dict.get(fecha_tope)
        if not val_base or not val_tope:
            return Decimal("1.0000")
        factor = Decimal(str(val_tope)) / Decimal(str(val_base))
        return factor.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

    def _construir_ciclos(self):
        """
        Calcula determinísticamente los 14 ciclos desde el ciclo que contiene
        el 01/08/2025 hasta hoy (2026-09-25).
        """
        self.ciclos: List[CicloInfo] = []
        fecha_primera = date(2025, 8, 1)
        c_ini, c_fin = get_ciclo_fechas(self.user, fecha_primera)

        curr_ini = c_ini
        num = 1
        while curr_ini <= date(2026, 9, 25):
            ini, fin = get_ciclo_fechas(self.user, curr_ini)
            # El mes ancla es el mes que le da nombre al ciclo (el mes de cierre o del día 30)
            mes_ancla = ini.month if ini.day <= 15 else (ini.month % 12 + 1)
            anio_ancla = ini.year if (ini.day <= 15 or ini.month < 12) else ini.year + 1

            self.ciclos.append(
                CicloInfo(
                    numero=num,
                    fecha_inicio=ini,
                    fecha_fin=fin,
                    fecha_sueldo=ini,
                    anio_ancla=anio_ancla,
                    mes_ancla=mes_ancla,
                )
            )
            # Siguiente ciclo arranca el día después del fin de este ciclo
            from datetime import timedelta
            curr_ini = fin + timedelta(days=1)
            num += 1
