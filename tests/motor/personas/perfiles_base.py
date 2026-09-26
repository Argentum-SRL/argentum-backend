# Generadores de perfiles sintéticos base: P01 a P05
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from app.models.transaccion import TipoTransaccion
from app.models.usuario import Moneda
from tests.motor.personas.base_tiempo import (
    ContextoGeneracionPersona,
    MESES_HISTORIA,
    factor_inflacion,
)


def generar_p01(ctx: ContextoGeneracionPersona, es_p09: bool = False):
    """P01: Empleado en blanco, sueldo fijo sin aumentos, alquila, ingreso de 1.200.000."""
    ctx.registrar_grupo("sueldo", "ingreso_habitual", "Sueldo fijo mensual")
    ctx.registrar_grupo("alquiler", "gasto_fijo", "Alquiler departamento mensual")
    ctx.registrar_grupo("expensas", "gasto_fijo", "Expensas comunes edificio")
    ctx.registrar_grupo("internet", "gasto_fijo", "Abono internet Fibertel")
    ctx.registrar_grupo("celular", "gasto_fijo", "Línea celular Personal")
    ctx.registrar_grupo("luz", "gasto_fijo", "Factura de electricidad Edenor")
    ctx.registrar_grupo("delivery", "costumbre", "Pedidos de comida fin de semana")
    ctx.registrar_grupo("cafeteria", "costumbre", "Café de paso habitual")
    ctx.registrar_grupo("gimnasio", "costumbre", "Cuota gimnasio")
    ctx.registrar_grupo("supermercado", "gasto_diario", "Compras varias de supermercado")
    ctx.registrar_grupo("kiosco", "gasto_diario", "Snacks y golosinas")
    ctx.registrar_grupo("transporte_sube", "gasto_diario", "Cargas SUBE transporte")
    ctx.registrar_grupo("arreglo_hogar", "eventual", "Arreglo plomería cocina")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        # 1. Ingreso habitual: Sueldo fijo 1.200.000 (sin aumentos) día 1 a 3
        dia_sueldo = min(ctx.rng.randint(1, 3), dias_en_mes)
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_sueldo),
            monto=Decimal("1200000.00"),
            tipo=TipoTransaccion.INGRESO,
            descripcion="Acreditación Sueldo Haberes",
            nombre_cat="Empleo",
            nombre_subcat="Sueldo",
            grupo_verdadero="sueldo",
            tipo_verdadero="ingreso_habitual",
        )

        # 2. Gastos fijos (COMPROMISO)
        dia_alq = min(5 + ctx.rng.randint(-1, 1), dias_en_mes)
        monto_alq = Decimal("350000.00") if (anio, mes) < (2026, 3) else Decimal("420000.00")
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_alq),
            monto=monto_alq,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Alquiler Depto Palermo",
            nombre_cat="Vivienda",
            nombre_subcat="Alquiler",
            grupo_verdadero="alquiler",
            tipo_verdadero="gasto_fijo",
        )

        dia_exp = min(10 + ctx.rng.randint(-2, 2), dias_en_mes)
        monto_exp = Decimal("75000.00") * f_infl * (Decimal("1") + Decimal(str(ctx.rng.uniform(-0.03, 0.03))))
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_exp),
            monto=monto_exp,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Expensas Edificio",
            nombre_cat="Vivienda",
            nombre_subcat="Expensas",
            grupo_verdadero="expensas",
            tipo_verdadero="gasto_fijo",
        )

        dia_net = min(15 + ctx.rng.randint(-1, 1), dias_en_mes)
        monto_net = Decimal("28000.00") * f_infl
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_net),
            monto=monto_net,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Fibertel Internet 300MB",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="internet",
            tipo_verdadero="gasto_fijo",
        )

        dia_cel = min(18 + ctx.rng.randint(-1, 1), dias_en_mes)
        monto_cel = Decimal("16000.00") * f_infl
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_cel),
            monto=monto_cel,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Personal Celular Plan",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )

        dia_luz = min(22 + ctx.rng.randint(-2, 2), dias_en_mes)
        monto_luz = Decimal("24000.00") * f_infl * (Decimal("1") + Decimal(str(ctx.rng.uniform(-0.05, 0.05))))
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_luz),
            monto=monto_luz,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Edenor Electricidad",
            nombre_cat="Vivienda",
            nombre_subcat="Luz",
            grupo_verdadero="luz",
            tipo_verdadero="gasto_fijo",
        )

        # 3. Costumbres (HABITO)
        if not (es_p09 and ctx.rng.random() < 0.5):
            dia_gim = min(8 + ctx.rng.randint(-1, 1), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_gim),
                monto=Decimal("32000.00") * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="SportClub Cuota Mensual",
                nombre_cat="Salud",
                nombre_subcat="Deportes y gimnasio",
                grupo_verdadero="gimnasio",
                tipo_verdadero="costumbre",
            )

        for semana in range(1, 5):
            if es_p09 and ctx.rng.random() < 0.5:
                continue
            dia_deliv = min(semana * 7 - ctx.rng.randint(0, 2), dias_en_mes)
            monto_deliv = Decimal("14000.00") * f_infl * (Decimal("1") + Decimal(str(ctx.rng.uniform(-0.1, 0.1))))
            desc_deliv = ctx.rng.choice(["PedidosYa Pizza", "PedidosYa Burger", "PedidosYa Empanadas"])
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_deliv),
                monto=monto_deliv,
                tipo=TipoTransaccion.EGRESO,
                descripcion=desc_deliv,
                nombre_cat="Gastronomía",
                nombre_subcat="Delivery",
                grupo_verdadero="delivery",
                tipo_verdadero="costumbre",
            )

        for _ in range(3):
            if es_p09 and ctx.rng.random() < 0.5:
                continue
            dia_cafe = ctx.rng.randint(1, dias_en_mes)
            monto_cafe = Decimal("4200.00") * f_infl * (Decimal("1") + Decimal(str(ctx.rng.uniform(-0.05, 0.05))))
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_cafe),
                monto=monto_cafe,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Cafetería Martínez",
                nombre_cat="Gastronomía",
                nombre_subcat="Cafetería",
                grupo_verdadero="cafeteria",
                tipo_verdadero="costumbre",
            )

        # 4. Gastos diarios (VARIABLE)
        cant_super = ctx.rng.randint(3, 5)
        for _ in range(cant_super):
            if es_p09 and ctx.rng.random() < 0.5:
                continue
            dia_sup = ctx.rng.randint(1, dias_en_mes)
            monto_sup = Decimal(str(ctx.rng.randint(25000, 75000))) * f_infl
            desc_sup = ctx.rng.choice(["Coto Supermercado", "Carrefour Express", "Dia Supermercado"])
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_sup),
                monto=monto_sup,
                tipo=TipoTransaccion.EGRESO,
                descripcion=desc_sup,
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="supermercado",
                tipo_verdadero="gasto_diario",
            )

        for _ in range(3):
            if es_p09 and ctx.rng.random() < 0.5:
                continue
            dia_k = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_k),
                monto=Decimal(str(ctx.rng.randint(1200, 3500))),
                tipo=TipoTransaccion.EGRESO,
                descripcion="Open 25 Kiosco",
                nombre_cat="Alimentación",
                nombre_subcat="Kiosco",
                grupo_verdadero="kiosco",
                tipo_verdadero="gasto_diario",
            )

        for _ in range(3):
            if es_p09 and ctx.rng.random() < 0.5:
                continue
            dia_sube = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_sube),
                monto=Decimal("2500.00"),
                tipo=TipoTransaccion.EGRESO,
                descripcion="Carga SUBE",
                nombre_cat="Transporte",
                nombre_subcat="Transporte público",
                grupo_verdadero="transporte_sube",
                tipo_verdadero="gasto_diario",
            )

    # 5. Gasto eventual grande
    ctx.agregar_movimiento(
        fecha=date(2026, 1, 14),
        monto=Decimal("115000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Reparación cañería desagüe plomero",
        nombre_cat="Equipamiento del hogar",
        nombre_subcat="Reparaciones",
        grupo_verdadero="arreglo_hogar",
        tipo_verdadero="eventual",
    )


def generar_p02(ctx: ContextoGeneracionPersona):
    """P02: Empleado con paritarias cada 2 a 4 meses y aguinaldo, propietario, 2.800.000."""
    ctx.registrar_grupo("sueldo", "ingreso_habitual", "Sueldo con paritarias")
    ctx.registrar_grupo("aguinaldo", "ingreso_extra", "SAC Diciembre y Junio")
    ctx.registrar_grupo("expensas", "gasto_fijo", "Expensas departamento propietario")
    ctx.registrar_grupo("prepaga", "gasto_fijo", "Medicina prepaga OSDE")
    ctx.registrar_grupo("seguro_auto", "gasto_fijo", "Seguro automotor")
    ctx.registrar_grupo("internet", "gasto_fijo", "Internet fibra óptica")
    ctx.registrar_grupo("celular", "gasto_fijo", "Abono celular")
    ctx.registrar_grupo("combustible", "costumbre", "Carga combustible YPF quincenal")
    ctx.registrar_grupo("restaurante", "costumbre", "Cenas de fin de semana")
    ctx.registrar_grupo("cuidado_personal", "costumbre", "Peluquería / Barbershop mensual")
    ctx.registrar_grupo("supermercado", "gasto_diario", "Compras grandes Jumbo")
    ctx.registrar_grupo("farmacia", "gasto_diario", "Farmacity compras varias")
    ctx.registrar_grupo("peajes", "gasto_diario", "Telepase Autopistas")
    ctx.registrar_grupo("smart_tv", "eventual", "Compra Smart TV 55")

    escalones_paritarias = {
        (2025, 9): Decimal("2800000.00"),
        (2025, 12): Decimal("3100000.00"),
        (2026, 3): Decimal("3450000.00"),
        (2026, 6): Decimal("3800000.00"),
    }
    sueldo_actual = Decimal("2800000.00")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        if (anio, mes) in escalones_paritarias:
            sueldo_actual = escalones_paritarias[(anio, mes)]

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(2, dias_en_mes)),
            monto=sueldo_actual,
            tipo=TipoTransaccion.INGRESO,
            descripcion="Acreditación Sueldo Haberes",
            nombre_cat="Empleo",
            nombre_subcat="Sueldo",
            grupo_verdadero="sueldo",
            tipo_verdadero="ingreso_habitual",
        )

        if (anio, mes) in ((2025, 12), (2026, 6)):
            ctx.agregar_movimiento(
                fecha=date(anio, mes, min(19, dias_en_mes)),
                monto=(sueldo_actual / Decimal("2")).quantize(Decimal("0.01")),
                tipo=TipoTransaccion.INGRESO,
                descripcion="Acreditación SAC Aguinaldo",
                nombre_cat="Empleo",
                nombre_subcat="Aguinaldo",
                grupo_verdadero="aguinaldo",
                tipo_verdadero="ingreso_extra",
            )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(8 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("120000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Expensas Torre Belgrano",
            nombre_cat="Vivienda",
            nombre_subcat="Expensas",
            grupo_verdadero="expensas",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(12 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("165000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="OSDE Medicina Prepaga",
            nombre_cat="Salud",
            nombre_subcat="Obra social / Prepaga",
            grupo_verdadero="prepaga",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(16, dias_en_mes)),
            monto=Decimal("68000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="La Caja Seguro Automotor",
            nombre_cat="Transporte",
            nombre_subcat="Mantenimiento y seguro del auto",
            grupo_verdadero="seguro_auto",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(18, dias_en_mes)),
            monto=Decimal("35000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Iplan Liv Fibra Optica",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="internet",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(20, dias_en_mes)),
            monto=Decimal("22000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Claro Celular Plan Pospago",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(6 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("45000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="YPF Infinia Nafta",
            nombre_cat="Transporte",
            nombre_subcat="Combustible",
            grupo_verdadero="combustible",
            tipo_verdadero="costumbre",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(21 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("45000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="YPF Infinia Nafta",
            nombre_cat="Transporte",
            nombre_subcat="Combustible",
            grupo_verdadero="combustible",
            tipo_verdadero="costumbre",
        )

        for s in (7, 14, 21):
            dia_r = min(s + ctx.rng.randint(-1, 1), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_r),
                monto=Decimal(str(ctx.rng.randint(35000, 60000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Restaurante La Cabrera",
                nombre_cat="Gastronomía",
                nombre_subcat="Restaurantes",
                grupo_verdadero="restaurante",
                tipo_verdadero="costumbre",
            )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(25 + ctx.rng.randint(-2, 2), dias_en_mes)),
            monto=Decimal("18000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="The Barber Job Corte",
            nombre_cat="Otros",
            nombre_subcat="Cuidado personal",
            grupo_verdadero="cuidado_personal",
            tipo_verdadero="costumbre",
        )

        for _ in range(4):
            dia_j = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_j),
                monto=Decimal(str(ctx.rng.randint(60000, 140000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Jumbo Palermo Compra",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="supermercado",
                tipo_verdadero="gasto_diario",
            )
        for _ in range(2):
            dia_p = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_p),
                monto=Decimal("3800.00"),
                tipo=TipoTransaccion.EGRESO,
                descripcion="AUSA Telepase",
                nombre_cat="Transporte",
                nombre_subcat="Peajes",
                grupo_verdadero="peajes",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 5, 18),
        monto=Decimal("680000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Smart TV Samsung 55 UHD",
        nombre_cat="Equipamiento del hogar",
        nombre_subcat="Muebles y electrodomésticos",
        grupo_verdadero="smart_tv",
        tipo_verdadero="eventual",
    )


def generar_p03(ctx: ContextoGeneracionPersona):
    """P03: Freelance en pesos, cobros irregulares en monto y en fecha, promedio de 1.500.000."""
    ctx.registrar_grupo("honorarios", "ingreso_habitual", "Cobros freelance variables")
    ctx.registrar_grupo("alquiler", "gasto_fijo", "Alquiler departamento")
    ctx.registrar_grupo("internet", "gasto_fijo", "Fibertel trabajo")
    ctx.registrar_grupo("celular", "gasto_fijo", "Línea móvil")
    ctx.registrar_grupo("monotributo", "gasto_fijo", "Cuota AFIP Monotributo")
    ctx.registrar_grupo("coworking", "costumbre", "Café / Coworking de trabajo")
    ctx.registrar_grupo("delivery", "costumbre", "Delivery nocturno")
    ctx.registrar_grupo("supermercado", "gasto_diario", "Supermercado chino / Carrefour")
    ctx.registrar_grupo("kiosco", "gasto_diario", "Kiosco y café al paso")
    ctx.registrar_grupo("monitores", "eventual", "Monitor externo para diseño")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        cant_cobros = ctx.rng.randint(2, 4)
        for _ in range(cant_cobros):
            dia_c = ctx.rng.randint(1, dias_en_mes)
            monto_c = Decimal(str(ctx.rng.randint(350000, 750000))) * f_infl
            desc_c = ctx.rng.choice(["Transferencia Cliente Web", "Honorarios Diseño UX", "Cobro Desarrollo API"])
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_c),
                monto=monto_c,
                tipo=TipoTransaccion.INGRESO,
                descripcion=desc_c,
                nombre_cat="Trabajo independiente",
                nombre_subcat="Honorarios",
                grupo_verdadero="honorarios",
                tipo_verdadero="ingreso_habitual",
            )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(7 + ctx.rng.randint(-2, 2), dias_en_mes)),
            monto=Decimal("410000.00") * (Decimal("1") if (anio, mes) < (2026, 4) else Decimal("1.2")),
            tipo=TipoTransaccion.EGRESO,
            descripcion="Alquiler Depto Estudio",
            nombre_cat="Vivienda",
            nombre_subcat="Alquiler",
            grupo_verdadero="alquiler",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(14 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("30000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Fibertel 500MB Dedicado",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="internet",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(17 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("17000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Movistar Celular Plan",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(20, dias_en_mes)),
            monto=Decimal("38000.00") * (Decimal("1") if (anio, mes) < (2026, 2) else Decimal("1.3")),
            tipo=TipoTransaccion.EGRESO,
            descripcion="AFIP VEP Monotributo",
            nombre_cat="Vivienda",
            nombre_subcat="Impuestos",
            grupo_verdadero="monotributo",
            tipo_verdadero="gasto_fijo",
        )

        for s in (5, 12, 19, 26):
            dia_cowork = min(s + ctx.rng.randint(-1, 1), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_cowork),
                monto=Decimal("6500.00") * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Cuervo Café de Especialidad",
                nombre_cat="Gastronomía",
                nombre_subcat="Cafetería",
                grupo_verdadero="coworking",
                tipo_verdadero="costumbre",
            )
            dia_deliv = min(s + ctx.rng.randint(0, 2), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_deliv),
                monto=Decimal(str(ctx.rng.randint(12000, 22000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Rappi Delivery Cena",
                nombre_cat="Gastronomía",
                nombre_subcat="Delivery",
                grupo_verdadero="delivery",
                tipo_verdadero="costumbre",
            )

        for _ in range(5):
            dia_s = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_s),
                monto=Decimal(str(ctx.rng.randint(15000, 45000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Supermercado Almacén",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="supermercado",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 3, 10),
        monto=Decimal("320000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Monitor Dell 27 Pulgadas IPS",
        nombre_cat="Equipamiento del hogar",
        nombre_subcat="Muebles y electrodomésticos",
        grupo_verdadero="monitores",
        tipo_verdadero="eventual",
    )


def generar_p04(ctx: ContextoGeneracionPersona):
    """P04: Freelance que cobra en dólares (unos USD 1.500 por mes) y gasta en pesos."""
    ctx.registrar_grupo("cobro_usd", "ingreso_habitual", "Cobro mensual en dólares")
    ctx.registrar_grupo("alquiler", "gasto_fijo", "Alquiler en pesos")
    ctx.registrar_grupo("expensas", "gasto_fijo", "Expensas en pesos")
    ctx.registrar_grupo("internet", "gasto_fijo", "Internet fibra")
    ctx.registrar_grupo("celular", "gasto_fijo", "Celular plan")
    ctx.registrar_grupo("prepaga", "gasto_fijo", "Swiss Medical Prepaga")
    ctx.registrar_grupo("delivery", "costumbre", "Rappi fin de semana")
    ctx.registrar_grupo("salidas_bar", "costumbre", "Bares con amigos")
    ctx.registrar_grupo("supermercado", "gasto_diario", "Supermercado")
    ctx.registrar_grupo("pasaje_avion", "eventual", "Pasaje de viaje")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        dia_usd = min(ctx.rng.randint(1, 5), dias_en_mes)
        ctx.agregar_movimiento(
            fecha=date(anio, mes, dia_usd),
            monto=Decimal("1500.00"),
            tipo=TipoTransaccion.INGRESO,
            descripcion="Wire Transfer Client Inc USD",
            nombre_cat="Trabajo independiente",
            nombre_subcat="Honorarios",
            grupo_verdadero="cobro_usd",
            tipo_verdadero="ingreso_habitual",
            moneda=Moneda.USD,
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(6 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("480000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Alquiler Depto Belgrano",
            nombre_cat="Vivienda",
            nombre_subcat="Alquiler",
            grupo_verdadero="alquiler",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(10 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("85000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Expensas Edificio",
            nombre_cat="Vivienda",
            nombre_subcat="Expensas",
            grupo_verdadero="expensas",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(15, dias_en_mes)),
            monto=Decimal("32000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Telecentro 300MB",
            nombre_cat="Comunicación",
            nombre_subcat="Internet y cable",
            grupo_verdadero="internet",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(18, dias_en_mes)),
            monto=Decimal("19000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Movistar Plan Datos",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(22, dias_en_mes)),
            monto=Decimal("140000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Swiss Medical Prepaga",
            nombre_cat="Salud",
            nombre_subcat="Obra social / Prepaga",
            grupo_verdadero="prepaga",
            tipo_verdadero="gasto_fijo",
        )

        for s in (8, 16, 24):
            dia_del = min(s + ctx.rng.randint(-1, 1), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_del),
                monto=Decimal(str(ctx.rng.randint(18000, 32000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Rappi Gourmet Sushi",
                nombre_cat="Gastronomía",
                nombre_subcat="Delivery",
                grupo_verdadero="delivery",
                tipo_verdadero="costumbre",
            )
            dia_bar = min(s + 2, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_bar),
                monto=Decimal(str(ctx.rng.randint(25000, 50000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Bar Antares Cervecería",
                nombre_cat="Recreativo",
                nombre_subcat="Salidas",
                grupo_verdadero="salidas_bar",
                tipo_verdadero="costumbre",
            )

        for _ in range(4):
            dia_sup = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_sup),
                monto=Decimal(str(ctx.rng.randint(40000, 95000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Disco Supermercado",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="supermercado",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 2, 11),
        monto=Decimal("450000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Aerolíneas Argentinas Vuelo Bariloche",
        nombre_cat="Recreativo",
        nombre_subcat="Viajes",
        grupo_verdadero="pasaje_avion",
        tipo_verdadero="eventual",
    )


def generar_p05(ctx: ContextoGeneracionPersona):
    """P05: Estudiante mantenido: padres transfieren montos parecidos sin día fijo (~600.000/mes)."""
    ctx.registrar_grupo("mesada_padres", "ingreso_habitual", "Transferencias de los padres")
    ctx.registrar_grupo("cuota_facultad", "gasto_fijo", "Cuota mensual universidad")
    ctx.registrar_grupo("celular", "gasto_fijo", "Abono celular")
    ctx.registrar_grupo("fotocopias", "costumbre", "Fotocopiadora apuntes")
    ctx.registrar_grupo("salidas_estudiantes", "costumbre", "Bares los jueves")
    ctx.registrar_grupo("almacen", "gasto_diario", "Compras comida barata")
    ctx.registrar_grupo("sube", "gasto_diario", "Transporte colectivo")
    ctx.registrar_grupo("libros", "eventual", "Libro de texto universitario")

    for anio, mes in MESES_HISTORIA:
        f_infl = factor_inflacion(anio, mes)
        dias_en_mes = calendar.monthrange(anio, mes)[1]

        dia_t1 = ctx.rng.randint(2, 10)
        monto_t1 = Decimal(str(ctx.rng.randint(280000, 320000))) * f_infl
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(dia_t1, dias_en_mes)),
            monto=monto_t1,
            tipo=TipoTransaccion.INGRESO,
            descripcion="Transferencia Familiar Papa",
            nombre_cat="Otros",
            nombre_subcat="Regalos",
            grupo_verdadero="mesada_padres",
            tipo_verdadero="ingreso_habitual",
        )
        dia_t2 = ctx.rng.randint(15, 24)
        monto_t2 = Decimal(str(ctx.rng.randint(280000, 320000))) * f_infl
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(dia_t2, dias_en_mes)),
            monto=monto_t2,
            tipo=TipoTransaccion.INGRESO,
            descripcion="Transferencia Familiar Mama",
            nombre_cat="Otros",
            nombre_subcat="Regalos",
            grupo_verdadero="mesada_padres",
            tipo_verdadero="ingreso_habitual",
        )

        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(10 + ctx.rng.randint(-1, 1), dias_en_mes)),
            monto=Decimal("180000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Universidad Cuota Carrera",
            nombre_cat="Educación",
            nombre_subcat="Cuotas",
            grupo_verdadero="cuota_facultad",
            tipo_verdadero="gasto_fijo",
        )
        ctx.agregar_movimiento(
            fecha=date(anio, mes, min(16, dias_en_mes)),
            monto=Decimal("12000.00") * f_infl,
            tipo=TipoTransaccion.EGRESO,
            descripcion="Tuenti Plan Celular",
            nombre_cat="Comunicación",
            nombre_subcat="Celular",
            grupo_verdadero="celular",
            tipo_verdadero="gasto_fijo",
        )

        for s in (7, 21):
            dia_f = min(s + ctx.rng.randint(-1, 1), dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_f),
                monto=Decimal(str(ctx.rng.randint(4000, 9000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Fotocopiadora CECE Apuntes",
                nombre_cat="Educación",
                nombre_subcat="Materiales y libros",
                grupo_verdadero="fotocopias",
                tipo_verdadero="costumbre",
            )
            dia_sal = min(s + 2, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_sal),
                monto=Decimal(str(ctx.rng.randint(10000, 22000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Bar Universitario Birra",
                nombre_cat="Recreativo",
                nombre_subcat="Salidas",
                grupo_verdadero="salidas_estudiantes",
                tipo_verdadero="costumbre",
            )

        for _ in range(6):
            dia_alm = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_alm),
                monto=Decimal(str(ctx.rng.randint(8000, 25000))) * f_infl,
                tipo=TipoTransaccion.EGRESO,
                descripcion="Almacén Comida",
                nombre_cat="Alimentación",
                nombre_subcat="Supermercado",
                grupo_verdadero="almacen",
                tipo_verdadero="gasto_diario",
            )
        for _ in range(4):
            dia_sube = ctx.rng.randint(1, dias_en_mes)
            ctx.agregar_movimiento(
                fecha=date(anio, mes, dia_sube),
                monto=Decimal("3000.00"),
                tipo=TipoTransaccion.EGRESO,
                descripcion="Carga SUBE",
                nombre_cat="Transporte",
                nombre_subcat="Transporte público",
                grupo_verdadero="sube",
                tipo_verdadero="gasto_diario",
            )

    ctx.agregar_movimiento(
        fecha=date(2026, 4, 8),
        monto=Decimal("65000.00"),
        tipo=TipoTransaccion.EGRESO,
        descripcion="Libro Economía Samuelson",
        nombre_cat="Educación",
        nombre_subcat="Materiales y libros",
        grupo_verdadero="libros",
        tipo_verdadero="eventual",
    )
