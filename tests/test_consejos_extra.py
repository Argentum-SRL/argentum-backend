# -*- coding: utf-8 -*-
"""
tests/test_consejos_extra.py
Pruebas unitarias puras para consejos C8 a C13 (fase_i2b_consejos).
Sin acceso a base de datos, sin red, sin hoy_argentina.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.consejos_extra import (
    DatosExtra,
    IngresoDato,
    MetaDato,
    ResumenUsdDato,
    SaldoArrastradoDato,
    evaluar_arrastre,
    evaluar_candidatos,
    evaluar_carga,
    evaluar_dolares,
    evaluar_extra,
    evaluar_meta,
    evaluar_subio,
    indice_ipc,
)
from app.services.consejos_reglas import (
    DatosConsejos,
    FilaGasto,
    TEXTO_ACCION,
    comparar_medicion,
    elegir,
    evaluar_todos,
)


def test_c8_carga():
    """C8: Cuotas que pesan."""
    hist = [
        Decimal("100000"),
        Decimal("100000"),
        Decimal("110000"),
        Decimal("90000"),
        Decimal("100000"),
        Decimal("105000"),
    ]
    inicio = date(2026, 12, 1)

    # Caso completo ARS con ingreso y liberación
    c = evaluar_carga(
        cargas_hist=hist,
        proxima=Decimal("150000"),
        siguiente=Decimal("120000"),
        ingreso=Decimal("1000000"),
        moneda="ARS",
        inicio_proximo=inicio,
    )
    assert c is not None
    assert c.tipo == "carga"
    assert c.moneda == "ARS"
    assert c.clave == "carga/ARS/2026-12-01"
    assert (
        c.texto
        == "El mes que viene ya tenés $150.000 comprometidos en cuotas, lo más alto de los últimos 6 meses. "
        "Es el 15% de lo que te entra. Al mes siguiente se te liberan $30.000."
    )
    assert c.valores["proxima"] == "150000.00"
    assert c.valores["libera"] == "30000.00"

    # Con ingreso None y siguiente 150.000 -> sin 'Es el' y sin 'Al mes siguiente'
    c_sin_ing = evaluar_carga(
        cargas_hist=hist,
        proxima=Decimal("150000"),
        siguiente=Decimal("150000"),
        ingreso=None,
        moneda="ARS",
        inicio_proximo=inicio,
    )
    assert c_sin_ing is not None
    assert (
        c_sin_ing.texto
        == "El mes que viene ya tenés $150.000 comprometidos en cuotas, lo más alto de los últimos 6 meses."
    )

    # Con proxima 110.000 (no supera max) -> None; 110.001 -> califica
    assert (
        evaluar_carga(
            cargas_hist=hist,
            proxima=Decimal("110000"),
            siguiente=Decimal("110000"),
            ingreso=None,
            moneda="ARS",
            inicio_proximo=inicio,
        )
        is None
    )

    c_limite = evaluar_carga(
        cargas_hist=hist,
        proxima=Decimal("110001"),
        siguiente=Decimal("110001"),
        ingreso=None,
        moneda="ARS",
        inicio_proximo=inicio,
    )
    assert c_limite is not None
    assert (
        c_limite.texto
        == "El mes que viene ya tenés $110.000 comprometidos en cuotas, lo más alto de los últimos 6 meses."
    )

    # hist alternado [80k, 120k, 80k, 120k, 80k, 120k]: me=100k, mad=20k -> me + 3*mad = 160k
    hist_alt = [
        Decimal("80000"),
        Decimal("120000"),
        Decimal("80000"),
        Decimal("120000"),
        Decimal("80000"),
        Decimal("120000"),
    ]
    assert (
        evaluar_carga(
            cargas_hist=hist_alt,
            proxima=Decimal("150000"),
            siguiente=Decimal("150000"),
            ingreso=None,
            moneda="ARS",
            inicio_proximo=inicio,
        )
        is None
    )

    c_alt_califica = evaluar_carga(
        cargas_hist=hist_alt,
        proxima=Decimal("160001"),
        siguiente=Decimal("160001"),
        ingreso=None,
        moneda="ARS",
        inicio_proximo=inicio,
    )
    assert c_alt_califica is not None
    assert (
        c_alt_califica.texto
        == "El mes que viene ya tenés $160.000 comprometidos en cuotas, lo más alto de los últimos 6 meses."
    )

    # hist [100.000 x 5, 200.000]: max es 200k -> proxima 150k -> None
    hist_outlier = [Decimal("100000")] * 5 + [Decimal("200000")]
    assert (
        evaluar_carga(
            cargas_hist=hist_outlier,
            proxima=Decimal("150000"),
            siguiente=Decimal("150000"),
            ingreso=None,
            moneda="ARS",
            inicio_proximo=inicio,
        )
        is None
    )

    # hist con menos de 3 valores > 0 -> None
    hist_pocos = [Decimal("100000"), Decimal("100000"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")]
    assert (
        evaluar_carga(
            cargas_hist=hist_pocos,
            proxima=Decimal("500000"),
            siguiente=Decimal("500000"),
            ingreso=None,
            moneda="ARS",
            inicio_proximo=inicio,
        )
        is None
    )

    # hist 3 valores > 0 -> n=3
    hist_tres = [Decimal("100000"), Decimal("100000"), Decimal("100000")]
    c_tres = evaluar_carga(
        cargas_hist=hist_tres,
        proxima=Decimal("500000"),
        siguiente=Decimal("500000"),
        ingreso=None,
        moneda="ARS",
        inicio_proximo=inicio,
    )
    assert c_tres is not None
    assert (
        c_tres.texto
        == "El mes que viene ya tenés $500.000 comprometidos en cuotas, lo más alto de los últimos 3 meses."
    )

    # USD
    hist_usd = [
        Decimal("100"),
        Decimal("100"),
        Decimal("110"),
        Decimal("90"),
        Decimal("100"),
        Decimal("105"),
    ]
    c_usd = evaluar_carga(
        cargas_hist=hist_usd,
        proxima=Decimal("150"),
        siguiente=Decimal("120"),
        ingreso=None,
        moneda="USD",
        inicio_proximo=inicio,
    )
    assert c_usd is not None
    assert (
        c_usd.texto
        == "El mes que viene ya tenés US$150 comprometidos en cuotas, lo más alto de los últimos 6 meses. "
        "Al mes siguiente se te liberan US$30."
    )


def test_c9_extra():
    """C9: Ingreso extra (aguinaldo, bono o cobro grande)."""
    hoy = date(2026, 12, 5)

    # Trabajo > Aguinaldo $500.000 el 03/12 sin metas
    ing_ag = IngresoDato(
        id="ing-1",
        fecha=date(2026, 12, 3),
        monto=Decimal("500000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Aguinaldo",
    )
    c_sin_meta = evaluar_extra(
        ingresos_7=(ing_ag,),
        ingreso_habitual={},
        metas=(),
        hoy=hoy,
    )
    assert c_sin_meta is not None
    assert c_sin_meta.tipo == "extra"
    assert c_sin_meta.clave == "extra/ing-1"
    assert c_sin_meta.accion == "crear_meta"
    assert c_sin_meta.accion_datos == {"nombre": "Ahorro", "monto": "100000", "moneda": "ARS"}
    assert (
        c_sin_meta.texto
        == "Te entraron $500.000 de aguinaldo. Si apartás $100.000 ahora para una meta, no se diluye en el mes."
    )

    # Con metas ARS "Viaje" (limite 01/06/2027) y "Auto" (sin fecha) -> elige Viaje
    meta_viaje = MetaDato(
        id="m-viaje",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("100000"),
        fecha_limite=date(2027, 6, 1),
        dias_desde_creacion=100,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=1,
    )
    meta_auto = MetaDato(
        id="m-auto",
        nombre="Auto",
        moneda="ARS",
        objetivo=Decimal("2000000"),
        actual=Decimal("200000"),
        fecha_limite=None,
        dias_desde_creacion=100,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=1,
    )
    c_con_meta = evaluar_extra(
        ingresos_7=(ing_ag,),
        ingreso_habitual={},
        metas=(meta_viaje, meta_auto),
        hoy=hoy,
    )
    assert c_con_meta is not None
    assert c_con_meta.accion == "aportar_meta"
    assert (
        c_con_meta.texto
        == "Te entraron $500.000 de aguinaldo. Si apartás $100.000 ahora para Viaje, no se diluye en el mes."
    )

    # Con solo una meta en USD -> texto sin meta
    meta_usd = MetaDato(
        id="m-usd",
        nombre="Fondo USD",
        moneda="USD",
        objetivo=Decimal("1000"),
        actual=Decimal("100"),
        fecha_limite=date(2027, 6, 1),
        dias_desde_creacion=100,
        aportes_ciclos=(Decimal("50"),),
        cantidad_aportes=1,
    )
    c_meta_usd = evaluar_extra(
        ingresos_7=(ing_ag,),
        ingreso_habitual={},
        metas=(meta_usd,),
        hoy=hoy,
    )
    assert c_meta_usd is not None
    assert c_meta_usd.accion == "crear_meta"
    assert "para una meta" in c_meta_usd.texto

    # Bonos y extras $80.000
    ing_bono = IngresoDato(
        id="ing-bono",
        fecha=date(2026, 12, 3),
        monto=Decimal("80000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Bonos y extras",
    )
    c_bono = evaluar_extra(
        ingresos_7=(ing_bono,),
        ingreso_habitual={},
        metas=(),
        hoy=hoy,
    )
    assert c_bono is not None
    assert (
        c_bono.texto
        == "Te entraron $80.000 de un bono o extra. Si apartás $16.000 ahora para una meta, no se diluye en el mes."
    )

    # Trabajo > Sueldo $1.600.000 con habitual ARS 1.000.000 -> califica
    ing_sueldo = IngresoDato(
        id="ing-sueldo",
        fecha=date(2026, 12, 3),
        monto=Decimal("1600000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Sueldo",
    )
    c_sueldo = evaluar_extra(
        ingresos_7=(ing_sueldo,),
        ingreso_habitual={"ARS": Decimal("1000000")},
        metas=(),
        hoy=hoy,
    )
    assert c_sueldo is not None
    assert (
        c_sueldo.texto
        == "Te entraron $1.600.000, más de lo habitual. Si apartás $320.000 ahora para una meta, no se diluye en el mes."
    )

    # Sueldo $1.500.000 con habitual 1.000.000 (no es > 1,5 * habitual) -> None
    ing_sueldo_15 = IngresoDato(
        id="ing-s15",
        fecha=date(2026, 12, 3),
        monto=Decimal("1500000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Sueldo",
    )
    assert (
        evaluar_extra(
            ingresos_7=(ing_sueldo_15,),
            ingreso_habitual={"ARS": Decimal("1000000")},
            metas=(),
            hoy=hoy,
        )
        is None
    )

    # $1.600.000 sin habitual -> None; con habitual solo en USD -> None
    assert evaluar_extra((ing_sueldo,), {}, (), hoy) is None
    assert evaluar_extra((ing_sueldo,), {"USD": Decimal("1000")}, (), hoy) is None

    # Aguinaldo del 28/11 (fuera de ventana de 6 días: hoy-6 = 29/11) -> None; del 29/11 -> aparece
    ing_antiguo = IngresoDato(
        id="ing-ant",
        fecha=date(2026, 11, 28),
        monto=Decimal("500000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Aguinaldo",
    )
    assert evaluar_extra((ing_antiguo,), {}, (), hoy) is None

    ing_borde = IngresoDato(
        id="ing-borde",
        fecha=date(2026, 11, 29),
        monto=Decimal("500000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Aguinaldo",
    )
    assert evaluar_extra((ing_borde,), {}, (), hoy) is not None

    # Aguinaldo US$500
    ing_usd = IngresoDato(
        id="ing-usd",
        fecha=date(2026, 12, 3),
        monto=Decimal("500"),
        moneda="USD",
        categoria="Trabajo",
        subcategoria="Aguinaldo",
    )
    c_usd_extra = evaluar_extra((ing_usd,), {}, (), hoy)
    assert c_usd_extra is not None
    assert (
        c_usd_extra.texto
        == "Te entraron US$500 de aguinaldo. Si apartás US$100 ahora para una meta, no se diluye en el mes."
    )

    # Bono del 02/12 y aguinaldo del 04/12 -> el más nuevo gana (aguinaldo)
    ing_b_02 = IngresoDato(
        id="ing-b02",
        fecha=date(2026, 12, 2),
        monto=Decimal("80000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Bonos y extras",
    )
    ing_ag_04 = IngresoDato(
        id="ing-ag04",
        fecha=date(2026, 12, 4),
        monto=Decimal("500000"),
        moneda="ARS",
        categoria="Trabajo",
        subcategoria="Aguinaldo",
    )
    c_primer = evaluar_extra((ing_b_02, ing_ag_04), {}, (), hoy)
    assert c_primer is not None
    assert "aguinaldo" in c_primer.texto


def test_c10_meta():
    """C10: Meta atrasada y meta sin fecha."""
    hoy = date(2026, 11, 5)

    # "Viaje" ARS objetivo 1.000.000, actual 400.000, fecha_limite 31/03/2027, 90 días, aportes (50.000, 50.000, 50.000), 3 aportes
    meta_viaje = MetaDato(
        id="meta-viaje-id",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("50000"), Decimal("50000"), Decimal("50000")),
        cantidad_aportes=3,
    )
    c10 = evaluar_meta(meta_viaje, hoy)
    assert c10 is not None
    assert c10.tipo == "meta"
    assert c10.accion == "aportar_meta"
    assert c10.clave == "meta/meta-viaje-id/2026-11"
    assert (
        c10.texto
        == "A este ritmo llegás a Viaje en noviembre de 2027, no en marzo de 2027. "
        "Para llegar a tiempo hacen falta unos $150.000 por mes."
    )
    assert c10.valores["ritmo"] == "50000.00"
    assert c10.valores["necesario"] == "150000.00"

    # Aportes (150.000, 150.000, 150.000) -> ritmo=150.000, meses=4 -> llega en marzo 2027 -> llega a tiempo -> None
    meta_al_dia = MetaDato(
        id="m-aldia",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("150000"), Decimal("150000"), Decimal("150000")),
        cantidad_aportes=3,
    )
    assert evaluar_meta(meta_al_dia, hoy) is None

    # Aportes (149.999, 149.999, 149.999) -> meses=5 -> abril de 2027
    meta_casi = MetaDato(
        id="m-casi",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("149999"), Decimal("149999"), Decimal("149999")),
        cantidad_aportes=3,
    )
    c_casi = evaluar_meta(meta_casi, hoy)
    assert c_casi is not None
    assert (
        c_casi.texto
        == "A este ritmo llegás a Viaje en abril de 2027, no en marzo de 2027. "
        "Para llegar a tiempo hacen falta unos $150.000 por mes."
    )

    # Aportes (0, 0, 150.000) con 1 aporte -> promedio 50.000 -> primer texto
    meta_esporadica = MetaDato(
        id="m-esp",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("0"), Decimal("0"), Decimal("150000")),
        cantidad_aportes=1,
    )
    c_esp = evaluar_meta(meta_esporadica, hoy)
    assert c_esp is not None
    assert (
        c_esp.texto
        == "A este ritmo llegás a Viaje en noviembre de 2027, no en marzo de 2027. "
        "Para llegar a tiempo hacen falta unos $150.000 por mes."
    )

    # 59 días -> None; 60 -> aparece
    meta_59 = MetaDato(
        id="m-59",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=59,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=1,
    )
    assert evaluar_meta(meta_59, hoy) is None

    meta_60 = MetaDato(
        id="m-60",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=60,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=1,
    )
    assert evaluar_meta(meta_60, hoy) is not None

    # 0 aportes -> None
    meta_0ap = MetaDato(
        id="m-0ap",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=0,
    )
    assert evaluar_meta(meta_0ap, hoy) is None

    # Aportes (0, 0, 0) con 1 aporte -> ritmo=0 -> None
    meta_cero = MetaDato(
        id="m-cero",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("0"), Decimal("0"), Decimal("0")),
        cantidad_aportes=1,
    )
    assert evaluar_meta(meta_cero, hoy) is None

    # fecha_limite <= hoy (01/11/2026 <= 05/11/2026) -> None
    meta_vencida = MetaDato(
        id="m-venc",
        nombre="Viaje",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=date(2026, 11, 1),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=1,
    )
    assert evaluar_meta(meta_vencida, hoy) is None

    # Sin fecha "Auto" (mismos montos, 30 días)
    meta_sin_fecha = MetaDato(
        id="meta-auto-id",
        nombre="Auto",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=None,
        dias_desde_creacion=30,
        aportes_ciclos=(Decimal("50000"), Decimal("50000"), Decimal("50000")),
        cantidad_aportes=3,
    )
    c_sf = evaluar_meta(meta_sin_fecha, hoy)
    assert c_sf is not None
    assert c_sf.tipo == "meta"
    assert c_sf.accion == "poner_fecha_meta"
    assert c_sf.clave == "meta/meta-auto-id/sin_fecha"
    assert (
        c_sf.texto
        == "Ponerle fecha a Auto la vuelve más real. A tu ritmo actual llegarías en noviembre de 2027."
    )

    # Con 29 días -> None
    meta_sf_29 = MetaDato(
        id="m-sf29",
        nombre="Auto",
        moneda="ARS",
        objetivo=Decimal("1000000"),
        actual=Decimal("400000"),
        fecha_limite=None,
        dias_desde_creacion=29,
        aportes_ciclos=(Decimal("50000"),),
        cantidad_aportes=1,
    )
    assert evaluar_meta(meta_sf_29, hoy) is None

    # USD: "Viaje" USD objetivo 1.000, actual 400, mismos plazos, aportes (50, 50, 50)
    meta_viaje_usd = MetaDato(
        id="m-v-usd",
        nombre="Viaje",
        moneda="USD",
        objetivo=Decimal("1000"),
        actual=Decimal("400"),
        fecha_limite=date(2027, 3, 31),
        dias_desde_creacion=90,
        aportes_ciclos=(Decimal("50"), Decimal("50"), Decimal("50")),
        cantidad_aportes=3,
    )
    c_vusd = evaluar_meta(meta_viaje_usd, hoy)
    assert c_vusd is not None
    assert (
        c_vusd.texto
        == "A este ritmo llegás a Viaje en noviembre de 2027, no en marzo de 2027. "
        "Para llegar a tiempo hacen falta unos US$150 por mes."
    )


def test_c11_subio():
    """C11: Servicio que subió más que la inflación."""
    hoy = date(2026, 11, 5)
    ipc = {
        "2026-06": Decimal("100"),
        "2026-07": Decimal("102"),
        "2026-08": Decimal("104"),
        "2026-09": Decimal("106"),
        "2026-10": Decimal("108"),
    }

    # Tecnología > Internet: 20.000 el 10/07, 10/08 y 10/09 y 26.000 el 10/10
    filas = [
        FilaGasto(date(2026, 7, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 10, 10), Decimal("26000"), "ARS", "Tecnología", "Internet"),
    ]
    res = evaluar_subio(filas, ipc, hoy)
    assert len(res) == 1
    c = res[0]
    assert c.tipo == "subio"
    assert c.moneda == "ARS"
    assert c.clave == "subio/Internet/2026-10"
    assert (
        c.texto
        == "Internet pasó de $20.000 a $26.000 (30%), más que la inflación de esos meses (3,8%). "
        "Puede valer la pena revisar el plan."
    )
    assert c.ahorro is not None
    assert c.ahorro.quantize(Decimal("0.01")) == Decimal("62769.23")

    # Con el último en 21.769 -> None; 21.770 -> aparece (9%)
    # suba > infl + 5: 108/104 - 1 = 3.846% + 5% = 8.846%.
    # 20.000 * 1.0884615 = 21769.23 -> 21769 no supera, 21770 sí.
    filas_21769 = [
        FilaGasto(date(2026, 7, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 10, 10), Decimal("21769"), "ARS", "Tecnología", "Internet"),
    ]
    assert len(evaluar_subio(filas_21769, ipc, hoy)) == 0

    # Suba exactamente de 5 puntos sobre inflación 0 (de 20.000 a 21.000: suba 5%, infl 0%)
    # Con corte estricto > infl + 5 -> no califica ([]); con >= calificaría
    ipc_plano = {"2026-08": Decimal("100"), "2026-09": Decimal("100"), "2026-10": Decimal("100")}
    filas_borde_5 = [
        FilaGasto(date(2026, 7, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 10, 10), Decimal("21000"), "ARS", "Tecnología", "Internet"),
    ]
    assert len(evaluar_subio(filas_borde_5, ipc_plano, hoy)) == 0

    filas_21770 = [
        FilaGasto(date(2026, 7, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 10, 10), Decimal("21770"), "ARS", "Tecnología", "Internet"),
    ]
    res_70 = evaluar_subio(filas_21770, ipc, hoy)
    assert len(res_70) == 1
    assert (
        res_70[0].texto
        == "Internet pasó de $20.000 a $21.770 (9%), más que la inflación de esos meses (3,8%). "
        "Puede valer la pena revisar el plan."
    )

    # Pagos 10/08, 10/09, 10/10 de 20.000 y 01/11 de 26.000
    # mes_antes = 2026-09 (ipc 106). mes_ahora = 2026-11 (estimado 108 * 108/106). infl = 3,8%
    filas_nov = [
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 10, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 11, 1), Decimal("26000"), "ARS", "Tecnología", "Internet"),
    ]
    res_nov = evaluar_subio(filas_nov, ipc, hoy)
    assert len(res_nov) == 1
    assert (
        res_nov[0].texto
        == "Internet pasó de $20.000 a $26.000 (30%), más que la inflación de esos meses (3,8%). "
        "Puede valer la pena revisar el plan."
    )
    assert res_nov[0].valores["infl_estimada"] == "si"

    # ipc {"2026-06": 100, "2026-07": 102, "2026-08": 104} con Internet 20.000 el 10/07, 10/08 y 10/09 y 26.000 el 10/10 (hoy 05/11/2026)
    # -> infl 4,0% con infl_estimada == 'si'
    ipc_est = {
        "2026-06": Decimal("100"),
        "2026-07": Decimal("102"),
        "2026-08": Decimal("104"),
    }
    filas_est_4 = [
        FilaGasto(date(2026, 7, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 10, 10), Decimal("26000"), "ARS", "Tecnología", "Internet"),
    ]
    res_est_4 = evaluar_subio(filas_est_4, ipc_est, date(2026, 11, 5))
    assert len(res_est_4) == 1
    assert (
        res_est_4[0].texto
        == "Internet pasó de $20.000 a $26.000 (30%), más que la inflación de esos meses (4,0%). "
        "Puede valer la pena revisar el plan."
    )
    assert res_est_4[0].valores["infl_estimada"] == "si"

    # Pruebas directas de indice_ipc con ipc_est
    assert indice_ipc(ipc_est, "2026-08") == Decimal("104")
    assert indice_ipc(ipc_est, "2026-10") == Decimal("108.1184")
    assert indice_ipc(ipc_est, "2026-05") is None

    # Hueco en el medio -> None
    ipc_hueco = {"2026-06": Decimal("100"), "2026-08": Decimal("104")}
    assert indice_ipc(ipc_hueco, "2026-07") is None

    # Solo los tres primeros -> []
    assert len(evaluar_subio(filas[:3], ipc, hoy)) == 0

    # Los cuatro corridos un mes antes (último el 10/09 <= hoy - 45 días: 05/11 - 45 = 21/09) -> []
    filas_viejas = [
        FilaGasto(date(2026, 6, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 7, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 8, 10), Decimal("20000"), "ARS", "Tecnología", "Internet"),
        FilaGasto(date(2026, 9, 10), Decimal("26000"), "ARS", "Tecnología", "Internet"),
    ]
    assert len(evaluar_subio(filas_viejas, ipc, hoy)) == 0

    # Los mismos montos en USD -> []
    filas_usd = [
        FilaGasto(f.fecha, f.monto, "USD", f.categoria, f.subcategoria)
        for f in filas
    ]
    assert len(evaluar_subio(filas_usd, ipc, hoy)) == 0

    # Alimentación > Supermercado con los mismos montos -> []
    filas_super = [
        FilaGasto(f.fecha, f.monto, "ARS", "Alimentación", "Supermercado")
        for f in filas
    ]
    assert len(evaluar_subio(filas_super, ipc, hoy)) == 0

    # ipc sin "2026-08" -> []
    ipc_sin_08 = {k: v for k, v in ipc.items() if k != "2026-08"}
    assert len(evaluar_subio(filas, ipc_sin_08, hoy)) == 0


def test_c12_arrastre():
    """C12: Saldo del resumen sin pagar."""
    saldos = [
        SaldoArrastradoDato(
            tarjeta="Visa",
            tarjeta_id="visa-id",
            moneda="ARS",
            monto_restante=Decimal("120000"),
            vencimiento=date(2026, 10, 10),
        ),
        SaldoArrastradoDato(
            tarjeta="Visa",
            tarjeta_id="visa-id",
            moneda="ARS",
            monto_restante=Decimal("30000"),
            vencimiento=date(2026, 11, 10),
        ),
        SaldoArrastradoDato(
            tarjeta="Master",
            tarjeta_id="master-id",
            moneda="USD",
            monto_restante=Decimal("50"),
            vencimiento=date(2026, 11, 10),
        ),
        SaldoArrastradoDato(
            tarjeta="Visa",
            tarjeta_id="visa-id",
            moneda="USD",
            monto_restante=Decimal("0"),
            vencimiento=date(2026, 11, 10),
        ),
    ]

    res = evaluar_arrastre(saldos)
    assert len(res) == 2

    # Orden: por nombre de tarjeta y moneda -> Master USD primero, después Visa ARS
    c_master = res[0]
    assert c_master.tipo == "arrastre"
    assert c_master.moneda == "USD"
    assert (
        c_master.texto
        == "Del resumen de Master te quedaron US$50 sin pagar: la tarjeta cobra de los intereses más altos. "
        "Si podés, pagalo antes que otros gastos."
    )

    c_visa = res[1]
    assert c_visa.tipo == "arrastre"
    assert c_visa.moneda == "ARS"
    assert c_visa.clave == "arrastre/visa-id/ARS/2026-11-10"
    assert (
        c_visa.texto
        == "Del resumen de Visa te quedaron $150.000 sin pagar: la tarjeta cobra de los intereses más altos. "
        "Si podés, pagalo antes que otros gastos."
    )

    # Visa ARS 30.000 + Visa USD 20 -> dos consejos (uno por moneda)
    saldos_dos = [
        SaldoArrastradoDato(
            tarjeta="Visa",
            tarjeta_id="visa-id",
            moneda="ARS",
            monto_restante=Decimal("30000"),
            vencimiento=date(2026, 11, 10),
        ),
        SaldoArrastradoDato(
            tarjeta="Visa",
            tarjeta_id="visa-id",
            moneda="USD",
            monto_restante=Decimal("20"),
            vencimiento=date(2026, 11, 10),
        ),
    ]
    res_dos = evaluar_arrastre(saldos_dos)
    assert len(res_dos) == 2
    assert {c.moneda for c in res_dos} == {"ARS", "USD"}


def test_c13_dolares():
    """C13: Pagar resumen en dólares con dólares propios para evitar percepción."""
    r_visa = ResumenUsdDato(
        tarjeta="Visa",
        tarjeta_id="visa-id",
        cierre=date(2026, 11, 1),
        vencimiento=date(2026, 11, 10),
        usd_pendiente=Decimal("100"),
        percepcion_pct=Decimal("30.00"),
    )
    venta = Decimal("1450")

    # propios 150, hoy 05/11 -> evitable 100, perc 43.500
    res_150 = evaluar_dolares([r_visa], usd_propios=Decimal("150"), cotizacion_venta=venta, hoy=date(2026, 11, 5))
    assert len(res_150) == 1
    assert res_150[0].tipo == "dolares"
    assert res_150[0].moneda == "ARS"
    assert res_150[0].ahorro == Decimal("43500.00")
    assert (
        res_150[0].texto
        == "Si pagás los US$100 del resumen de Visa con tus dólares, evitás unos $43.500 de percepción."
    )

    # propios 60 -> evitable 60, perc 26.100 -> 26.100
    res_60 = evaluar_dolares([r_visa], usd_propios=Decimal("60"), cotizacion_venta=venta, hoy=date(2026, 11, 5))
    assert len(res_60) == 1
    assert (
        res_60[0].texto
        == "Si pagás US$60 de los US$100 del resumen de Visa con tus dólares, evitás unos $26.100 de percepción."
    )

    # propios 0 -> []
    assert len(evaluar_dolares([r_visa], Decimal("0"), venta, date(2026, 11, 5))) == 0

    # hoy 31/10 (hoy < cierre) y 01/11 (hoy == cierre, todavía no cerró) -> []
    assert len(evaluar_dolares([r_visa], Decimal("150"), venta, date(2026, 10, 31))) == 0
    assert len(evaluar_dolares([r_visa], Decimal("150"), venta, date(2026, 11, 1))) == 0

    # hoy 02/11 -> aparece
    assert len(evaluar_dolares([r_visa], Decimal("150"), venta, date(2026, 11, 2))) == 1

    # hoy 11/11 (hoy > vencimiento) -> []
    assert len(evaluar_dolares([r_visa], Decimal("150"), venta, date(2026, 11, 11))) == 0

    # percepción 0 -> []
    r_cero_perc = ResumenUsdDato(
        tarjeta="Visa",
        tarjeta_id="visa-id",
        cierre=date(2026, 11, 1),
        vencimiento=date(2026, 11, 10),
        usd_pendiente=Decimal("100"),
        percepcion_pct=Decimal("0"),
    )
    assert len(evaluar_dolares([r_cero_perc], Decimal("150"), venta, date(2026, 11, 5))) == 0

    # venta None -> []
    assert len(evaluar_dolares([r_visa], Decimal("150"), None, date(2026, 11, 5))) == 0


def test_evaluar_candidatos_integracion_pura():
    """Pruebas puras de orquestación con evaluar_candidatos."""
    hoy = date(2026, 11, 5)
    saldo_arrastre = SaldoArrastradoDato(
        tarjeta="Visa",
        tarjeta_id="visa-id",
        moneda="ARS",
        monto_restante=Decimal("120000"),
        vencimiento=date(2026, 11, 10),
    )
    extra = DatosExtra(
        saldos=(saldo_arrastre,),
    )

    # Datos con extra que da C12 y datos_viejos True -> []
    datos_viejos = DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=True,
        cobertura=100,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[],
        filas=[],
        ingresos_45=1,
        libre_ars=Decimal("100000"),
        saldo_hoy_ars=Decimal("100000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1400"),
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=False,
        extra=extra,
    )
    assert evaluar_candidatos(datos_viejos) == []

    # Con cobertura 49 -> []
    datos_baja_cob = DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=49,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[],
        filas=[],
        ingresos_45=1,
        libre_ars=Decimal("100000"),
        saldo_hoy_ars=Decimal("100000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1400"),
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=False,
        extra=extra,
    )
    assert evaluar_candidatos(datos_baja_cob) == []

    # Con extra None -> igual a evaluar_todos
    datos_sin_extra = DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[],
        filas=[],
        ingresos_45=1,
        libre_ars=Decimal("100000"),
        saldo_hoy_ars=Decimal("100000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1400"),
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=False,
        extra=None,
    )
    assert evaluar_candidatos(datos_sin_extra) == evaluar_todos(datos_sin_extra)

    # Con todo bien -> contiene el arrastre y elegir (historial vacío, sin silenciados, sin prioridades) lo devuelve primero
    datos_ok = DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[],
        filas=[],
        ingresos_45=1,
        libre_ars=Decimal("100000"),
        saldo_hoy_ars=Decimal("100000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1400"),
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=False,
        extra=extra,
    )
    candidatos = evaluar_candidatos(datos_ok)
    assert any(c.tipo == "arrastre" for c in candidatos)
    elegido = elegir(
        consejos=candidatos,
        historial=[],
        silenciados=set(),
        prioridades=[],
        hoy=hoy,
        cotizacion_compra=Decimal("1400"),
    )
    assert elegido is not None
    assert elegido.tipo == "arrastre"

    # C8 con cuotas ARS e ingreso_habitual solo en USD -> texto sin "Es el"
    filas_cuotas = [
        FilaGasto(date(2026, 10, 5), Decimal("100000"), "ARS", "Hogar", "Muebles", es_cuota=True),
        FilaGasto(date(2026, 9, 5), Decimal("100000"), "ARS", "Hogar", "Muebles", es_cuota=True),
        FilaGasto(date(2026, 8, 5), Decimal("100000"), "ARS", "Hogar", "Muebles", es_cuota=True),
    ]
    extra_c8 = DatosExtra(
        cuotas_proximas=((date(2026, 12, 1), {"ARS": Decimal("150000"), "USD": Decimal("0")}),),
        ingreso_habitual={"USD": Decimal("1000")},  # Solo USD
    )
    datos_c8 = DatosConsejos(
        hoy=hoy,
        hay_datos=True,
        moneda_principal="ARS",
        datos_viejos=False,
        cobertura=100,
        ciclo_actual=(date(2026, 11, 1), date(2026, 11, 30)),
        cerrados=[
            (date(2026, 10, 1), date(2026, 10, 31)),
            (date(2026, 9, 1), date(2026, 9, 30)),
            (date(2026, 8, 1), date(2026, 8, 31)),
        ],
        filas=filas_cuotas,
        ingresos_45=1,
        libre_ars=Decimal("100000"),
        saldo_hoy_ars=Decimal("100000"),
        inversiones_ars=Decimal("0"),
        usd_total=Decimal("0"),
        cotizacion_compra=Decimal("1400"),
        tasa_inflacion=None,
        mes_inflacion=None,
        fuente_inflacion=None,
        billetera_inversion=None,
        tiene_meta_colchon=False,
        extra=extra_c8,
    )
    cands_c8 = evaluar_candidatos(datos_c8)
    c8_match = [c for c in cands_c8 if c.tipo == "carga" and c.moneda == "ARS"]
    assert len(c8_match) == 1
    assert "Es el" not in c8_match[0].texto


def test_comparar_medicion_y_texto_accion():
    """comparar_medicion para arrastre, meta y extra, y constantes de TEXTO_ACCION."""
    # arrastre (150.000, 0) mejoro y (150.000, 150.000) igual
    assert comparar_medicion("arrastre", Decimal("150000"), Decimal("0")) == "mejoro"
    assert comparar_medicion("arrastre", Decimal("150000"), Decimal("150000")) == "igual"

    # meta (50.000, 100.000) mejoro y (50.000, 50.000) igual
    assert comparar_medicion("meta", Decimal("50000"), Decimal("100000")) == "mejoro"
    assert comparar_medicion("meta", Decimal("50000"), Decimal("50000")) == "igual"

    # extra (0, 1) mejoro
    assert comparar_medicion("extra", Decimal("0"), Decimal("1")) == "mejoro"

    # TEXTO_ACCION
    assert TEXTO_ACCION["aportar_meta"] == "Aportar a meta"
    assert TEXTO_ACCION["poner_fecha_meta"] == "Ponerle fecha"

