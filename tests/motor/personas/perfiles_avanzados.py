# Generadores de perfiles sintéticos avanzados: P06 a P10
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from app.models.transaccion import TipoTransaccion
from tests.motor.personas.base_tiempo import (
    ContextoGeneracionPersona,
    MESES_HISTORIA,
    factor_inflacion,
    factor_inflacion_rezago,
)
from tests.motor.personas.perfiles_base import generar_p01


def generar_p06(ctx: ContextoGeneracionPersona):
    """P06: Pasante con ingreso fijo chico (650.000) y gastos de gustos."""
    ctx.registrar_grupo("pasantia", "ingreso_habitual", "Asignación estímulo pasantía")
    ctx.registrar_grupo("celular", "gasto_fijo", "Abono celular")
    ctx.registrar_grupo("curso_idioma", "gasto_fijo", "Cuota mensual curso de inglés")
    ctx.registrar_grupo("ropa_gustos", "costumbre", "Compras ropa de moda Zara")
    ctx.registrar_grupo("salidas_viernes", "costumbre", "Salidas con amigos de oficina")
    ctx.registrar_grupo("almuerzo_oficina", "costumbre", "Almuerzo diario menú ejecutivo")
    ctx.registrar_grupo("kiosco", "gasto_diario", "Kiosco café")
    ctx.registrar_grupo("recital", "eventual", "Entrada recital Lollapalooza")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(4, dias_en_mes)),
            monto=Decimal("650000.00"),
            tipo=TipoTransaccion.INGRESO,
            descripcion="Pago Pasantía Empresa",
            nombre_cat="Empleo",
            nombre_subcat="Sueldo",
            grupo_verdadero="pasantia",
            tipo_verdadero="ingreso_habitual",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(10, dias_en_mes)),
            monto=Decimal("15000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Movistar Celular Plan",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(14, dias_en_mes)),
            monto=Decimal("45000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Instituto Cultural Inglés",
            nombre_cat="Educación",
            nombre_subcat="Idiomas",
            grupo_verdadero="curso_idioma",
            tipo_verdadero="gasto_fijo",
        )

        dia_r = min(12 + ctx.rng.randint(-2, 2), dias_en_mes)
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_r),
            monto=Decimal(str(ctx.rng.randint(35000, 75000))) * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Zara Ropa Indumentaria",
            nombre_cat="Indumentaria",
            nombre_subcat="Ropa",
            grupo_verdadero="ropa_gustos",
            tipo_verdadero="costumbre",
        )

        for s in (6, 13, 20, 27):
            dia_v = min(s, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_v),
                monto=Decimal(str(ctx.rng.randint(18000, 32000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="After Office Bar Palermo",
                nombre_cat="Recreativo",
                nombre_subcat="Salidas",
                grupo_verdadero="salidas_viernes",
                tipo_verdadero="costumbre",
            )

        for _ in range(5):
            dia_alm = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_alm),
                monto=Decimal(str(ctx.rng.randint(6500, 11000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Menú Almuerzo Take Away",
                nombre_cat="Gastronomía",
                nombre_subcat="Restaurantes",
                grupo_verdadero="almuerzo_oficina",
                tipo_verdadero="costumbre",
            )

        for _ in range(3):
            dia_k = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_k),
                monto=Decimal("2500.00"),
                tipo=TipoTransaccion.EGRESO,
                descripcion="Kiosco Snacks",
                nombre_cat="Alimentación",
                nombre_subcat="Kiosco",
                grupo_verdadero="kiosco",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 3, 20),
        monto=Decimal("150000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Entrada Lollapalooza Festival",
        nombre_cat="Recreativo",
        nombre_subcat="Hobbies y juegos",
        grupo_verdadero="recital",
        tipo_verdadero="eventual",
    )


def generar_p07(ctx: ContextoGeneracionPersona):
    """P07: Jefa de un hogar de 7 personas: súper muy alto, colegio, prepaga familiar, 2.500.000."""
    ctx.registrar_grupo("sueldo", "ingreso_habitual", "Ingreso familiar principal")
    ctx.registrar_grupo("colegio", "gasto_fijo", "Colegio cuotas de los chicos")
    ctx.registrar_grupo("prepaga_familiar", "gasto_fijo", "Medicina prepaga plan familiar")
    ctx.registrar_grupo("luz", "gasto_fijo", "Edenor consumo familiar grande")
    ctx.registrar_grupo("gas", "gasto_fijo", "Metrogas consumo grande")
    ctx.registrar_grupo("internet", "gasto_fijo", "Internet fibra casa")
    ctx.registrar_grupo("verduleria", "costumbre", "Compra semanal verdulería grande")
    ctx.registrar_grupo("carniceria", "costumbre", "Carnicería compra familiar")
    ctx.registrar_grupo("supermercado_grande", "gasto_diario", "Carrefour compras gigantes semanales")
    ctx.registrar_grupo("farmacia_familiar", "gasto_diario", "Farmacia remedios familiares")
    ctx.registrar_grupo("reparacion_techo", "eventual", "Arreglo impermeabilización techo")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(2, dias_en_mes)),
            monto=Decimal("2500000.00") * f_infl,
            tipo=TipoTransaccion.INGRESO,
            descripcion="Acreditación Haberes Familiar",
            nombre_cat="Empleo",
            nombre_subcat="Sueldo",
            grupo_verdadero="sueldo",
            tipo_verdadero="ingreso_habitual",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(8 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("380000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Colegio San Martín Cuotas Chicos",
            nombre_cat="Educación",
            nombre_subcat="Cuotas",
            grupo_verdadero="colegio",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(11 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("320000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Galeno Plan Familiar 7 Personas",
            nombre_cat="Salud",
            nombre_subcat="Obra social / Prepaga",
            grupo_verdadero="prepaga_familiar",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(16, dias_en_mes)),
            monto=Decimal("65000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Edenor Electricidad Casa",
            nombre_cat="Vivienda",
            nombre_subcat="Luz",
            grupo_verdadero="luz",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(19, dias_en_mes)),
            monto=Decimal("42000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Metrogas Gas Natural",
            nombre_cat="Vivienda",
            nombre_subcat="Gas",
            grupo_verdadero="gas",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(22, dias_en_mes)),
            monto=Decimal("32000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Fibertel Wifi Familiar",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="internet",
            tipo_verdadero="gasto_fijo",
        )

        for s in (5, 12, 19, 26):
            dia_v = min(s, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_v),
                monto=Decimal(str(ctx.rng.randint(22000, 38000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Verdulería Central Compra Semanal",
                nombre_cat="Alimentación",
                nombre_subcat="Verdulería",
                grupo_verdadero="verduleria",
                tipo_verdadero="costumbre",
            )
            dia_c = min(s + 1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_c),
                monto=Decimal(str(ctx.rng.randint(45000, 75000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Carnicería El Buen Corte Familiar",
                nombre_cat="Alimentación",
                nombre_subcat="Carnicería",
                grupo_verdadero="carniceria",
                tipo_verdadero="costumbre",
            )

        for _ in range(6):
            dia_sg = ctx.rng.randint(1, dias_en_mes)
            monto_sg = Decimal(str(ctx.rng.randint(85000, 185000))) * f_infl
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_sg),
                monto=monto_sg,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Carrefour Hipermercado Compra Familiar",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="supermercado_grande",
                tipo_verdadero="gasto_diario",
            )

        for _ in range(3):
            dia_f = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_f),
                monto=Decimal(str(ctx.rng.randint(15000, 45000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Farmacity Medicamentos Niños",
                nombre_cat="Salud",
                nombre_subcat="Farmacia",
                grupo_verdadero="farmacia_familiar",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 2, 24),
        monto=Decimal("420000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Reparación membrana e impermeabilización techos",
        nombre_cat="Equipamiento del hogar",
        nombre_subcat="Reparaciones",
        grupo_verdadero="reparacion_techo",
        tipo_verdadero="eventual",
    )


def generar_p08(ctx: ContextoGeneracionPersona):
    """P08: Jubilado: haber sube por inflación t-2, más bono fijo 70.000."""
    ctx.registrar_grupo("jubilacion", "ingreso_habitual", "Haber jubilatorio indexado")
    ctx.registrar_grupo("bono_anses", "ingreso_extra", "Bono previsional fijo ANSES")
    ctx.registrar_grupo("expensas", "gasto_fijo", "Expensas departamento")
    ctx.registrar_grupo("luz", "gasto_fijo", "Luz Edenor")
    ctx.registrar_grupo("gas", "gasto_fijo", "Gas Metrogas")
    ctx.registrar_grupo("telefono_fijo", "gasto_fijo", "Telefonía fija e internet Telecom")
    ctx.registrar_grupo("medicamentos_cronicos", "gasto_fijo", "Farmacia medicación fija mensual")
    ctx.registrar_grupo("cafe_amigos", "costumbre", "Café semanal con jubilados")
    ctx.registrar_grupo("almacen_barrio", "gasto_diario", "Compras almacén almacenero")
    ctx.registrar_grupo("verduleria", "gasto_diario", "Verdulería de barrio")
    ctx.registrar_grupo("estufa", "eventual", "Compra estufa eléctrica para invierno")

    base_haber = Decimal("310000.00")

    for anio, mes in MESES_HISTORIA:
        f_infl_rez = factor_inflacion_rezago(anio, mes, rezago_meses=2)
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        monto_haber = (base_haber * f_infl_rez).quantize(Decimal("0.01"))
        dia_j = min(12 + ctx.rng.randint(-1, 1), dias_en_mes)
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_j),
            monto=monto_haber,
            tipo=TipoTransaccion.INGRESO,
            descripcion="ANSES Haber Jubilatorio SIPA",
            nombre_cat="Empleo",
            nombre_subcat="Sueldo",
            grupo_verdadero="jubilacion",
            tipo_verdadero="ingreso_habitual",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(dia_j, dias_en_mes)),
            monto=Decimal("70000.00"),
            tipo=TipoTransaccion.INGRESO,
            descripcion="ANSES Bono Extraordinario Previsional",
            nombre_cat="Empleo",
            nombre_subcat="Bonos y horas extras",
            grupo_verdadero="bono_anses",
            tipo_verdadero="ingreso_extra",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(9 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("45000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Expensas Edificio Barrio Norte",
            nombre_cat="Vivienda",
            nombre_subcat="Expensas",
            grupo_verdadero="expensas",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(14, dias_en_mes)),
            monto=Decimal("15000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Edenor Tarifa Social",
            nombre_cat="Vivienda",
            nombre_subcat="Luz",
            grupo_verdadero="luz",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(17, dias_en_mes)),
            monto=Decimal("9500.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Metrogas Consumo Residencial",
            nombre_cat="Vivienda",
            nombre_subcat="Gas",
            grupo_verdadero="gas",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(20, dias_en_mes)),
            monto=Decimal("18000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Telecom Línea Fija e Internet",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="telefono_fijo",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(15 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("38000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Farmacia PAMI Medicación Crónica",
            nombre_cat="Salud",
            nombre_subcat="Farmacia",
            grupo_verdadero="medicamentos_cronicos",
            tipo_verdadero="gasto_fijo",
        )

        for s in (6, 13, 20, 27):
            dia_c = min(s, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_c),
                monto=Decimal("3500.00") * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Café Tortoni Encuentro Amigos",
                nombre_cat="Gastronomía",
                nombre_subcat="Cafetería",
                grupo_verdadero="cafe_amigos",
                tipo_verdadero="costumbre",
            )

        for _ in range(4):
            dia_a = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_a),
                monto=Decimal(str(ctx.rng.randint(6000, 18000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Almacén Don Tito",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="almacen_barrio",
                tipo_verdadero="gasto_diario",
            )

        for _ in range(3):
            dia_v = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_v),
                monto=Decimal(str(ctx.rng.randint(4000, 12000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Verdulería Don Pepe",
                nombre_cat="Alimentación",
                nombre_subcat="Verdulería",
                grupo_verdadero="verduleria",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 6, 12),
        monto=Decimal("48000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Estufa Caloventor Liliana",
        nombre_cat="Equipamiento del hogar",
        nombre_subcat="Muebles y electrodomésticos",
        grupo_verdadero="estufa",
        tipo_verdadero="eventual",
    )


def generar_p09(ctx: ContextoGeneracionPersona):
    """P09: Igual que P01, pero registra solo ~50% de sus gastos del día a día y costumbres (cobertura 0,5)."""
    generar_p01(ctx, es_p09=True)


def generar_p10(ctx: ContextoGeneracionPersona):
    """P10: Monotributista con cuota mensual fija de monotributo e ingresos variables."""
    ctx.registrar_grupo("facturacion_clientes", "ingreso_habitual", "Facturas a distintos clientes")
    ctx.registrar_grupo("cuota_monotributo", "gasto_fijo", "Cuota mensual fija Monotributo AFIP")
    ctx.registrar_grupo("alquiler_taller", "gasto_fijo", "Alquiler taller de trabajo")
    ctx.registrar_grupo("internet_taller", "gasto_fijo", "Internet fibra taller")
    ctx.registrar_grupo("celular", "gasto_fijo", "Celular laboral")
    ctx.registrar_grupo("seguro_taller", "gasto_fijo", "Seguro contra robo e incendio")
    ctx.registrar_grupo("almuerzos_trabajo", "costumbre", "Almuerzo cerca del taller")
    ctx.registrar_grupo("combustible_flete", "costumbre", "Carga nafta para repartos/visitas")
    ctx.registrar_grupo("ferreteria_insumos", "gasto_diario", "Compras chicas ferretería")
    ctx.registrar_grupo("supermercado", "gasto_diario", "Supermercado comida")
    ctx.registrar_grupo("taladro_percutor", "eventual", "Taladro DeWalt para taller")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        cant_cobros = ctx.rng.randint(2, 5)
        for _ in range(cant_cobros):
            dia_f = ctx.rng.randint(1, dias_en_mes)
            monto_f = Decimal(str(ctx.rng.randint(300000, 950000))) * f_infl
            desc_f = ctx.rng.choice(["Cobro Factura B Cliente", "Transferencia Servicio Mantenimiento", "Pago Proveedor Obras"])
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_f),
                monto=monto_f,
                tipo=TipoTransaccion.INGRESO,
                descripcion=desc_f,
                nombre_cat="Trabajo independiente",
                nombre_subcat="Venta de productos/servicios",
                grupo_verdadero="facturacion_clientes",
                tipo_verdadero="ingreso_habitual",
            )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(20, dias_en_mes)),
            monto=Decimal("54000.00"),
            tipo=TipoTransaccion.EGRESO,
            descripcion="AFIP Cuota Mensual Monotributo",
            nombre_cat="Vivienda",
            nombre_subcat="Impuestos",
            grupo_verdadero="cuota_monotributo",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(5 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("380000.00") * (Decimal("1") if (anio, mes) < (2026, 3) else Decimal("1.25")),
            tipo=TipoTransaccion.EGRESO,
            descripcion="Alquiler Taller Galpón Chacarita",
            nombre_cat="Vivienda",
            nombre_subcat="Alquiler",
            grupo_verdadero="alquiler_taller",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(14, dias_en_mes)),
            monto=Decimal("32000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Fibertel Negocios 300MB",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="internet_taller",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(18, dias_en_mes)),
            monto=Decimal("21000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Movistar Empresa Plan",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(24, dias_en_mes)),
            monto=Decimal("28000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Seguro Integral Comercio Taller",
            nombre_cat="Vivienda",
            nombre_subcat="Seguros",
            grupo_verdadero="seguro_taller",
            tipo_verdadero="gasto_fijo",
        )

        for s in (8, 22):
            dia_n = min(s + ctx.rng.randint(-1, 1), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_n),
                monto=Decimal("35000.00") * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Axion Energy Nafta Súper",
                nombre_cat="Transporte",
                nombre_subcat="Combustible",
                grupo_verdadero="combustible_flete",
                tipo_verdadero="costumbre",
            )

        for _ in range(3):
            dia_alm = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_alm),
                monto=Decimal(str(ctx.rng.randint(7000, 14000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Bodegón Almuerzo Taller",
                nombre_cat="Gastronomía",
                nombre_subcat="Restaurantes",
                grupo_verdadero="almuerzos_trabajo",
                tipo_verdadero="costumbre",
            )

        for _ in range(5):
            dia_ferr = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_ferr),
                monto=Decimal(str(ctx.rng.randint(8000, 35000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Ferretería Industrial Bulones e Insumos",
                nombre_cat="Equipamiento del hogar",
                nombre_subcat="Reparaciones",
                grupo_verdadero="ferreteria_insumos",
                tipo_verdadero="gasto_diario",
            )
        for _ in range(3):
            dia_s = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_s),
                monto=Decimal(str(ctx.rng.randint(25000, 60000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Supermercado Comida",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="supermercado",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 4, 15),
        monto=Decimal("210000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Taladro Percutor DeWalt 20V Taller",
        nombre_cat="Equipamiento del hogar",
        nombre_subcat="Muebles y electrodomésticos",
        grupo_verdadero="taladro_percutor",
        tipo_verdadero="eventual",
    )
