# -*- coding: utf-8 -*-
"""
Catálogo único de categorías y subcategorías de Argentum.
Decisión 1 (Árbol 26/10 versión 2) y ALIAS_NOMBRES (Decisión 7).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TipoCategoria = Literal["egreso", "ingreso"]


@dataclass(frozen=True)
class SubcategoriaCatalogo:
    nombre: str
    orden: int
    es_sistema: bool = False
    division_ipc: str | None = None
    descripcion: str = ""


@dataclass(frozen=True)
class CategoriaCatalogo:
    nombre: str
    tipo: TipoCategoria
    orden: int
    icono: str
    color: str
    subcategorias: tuple[SubcategoriaCatalogo, ...]
    descripcion_otros: str | None = None


# ------------------------------------------------------------------------------
# Árbol de categorías y subcategorías - Decisión 1
# ------------------------------------------------------------------------------

ARBOL_CATEGORIAS: tuple[CategoriaCatalogo, ...] = (
    # ========================== EGRESO ==========================
    CategoriaCatalogo(
        nombre="Alimentación",
        tipo="egreso",
        orden=1,
        icono="alimentacion",
        color="#F97316",
        descripcion_otros="fiambrería, dietética, vinoteca",
        subcategorias=(
            SubcategoriaCatalogo("Supermercado", 0, False, "01", "súper, Coto, Carrefour, Día, Jumbo, Changomás, almacén, chino, mayorista, despensa, compras del mes"),
            SubcategoriaCatalogo("Verdulería", 1, False, "01", "verdulería, frutería, frutas, verduras, papas, tomate, lechuga, bananas"),
            SubcategoriaCatalogo("Carnicería", 2, False, "01", "carnicería, carne, asado, milanesas, pollo, pollería, granja, pescadería, pescado, achuras, chorizo"),
            SubcategoriaCatalogo("Panadería", 3, False, "01", "panadería, pan, facturas, medialunas, bizcochos, budín, tortas, chipá"),
            SubcategoriaCatalogo("Kiosco", 4, False, "01", "kiosco, golosinas, alfajores, chocolates, chicles, bebida suelta, cigarrillos, puchos, tabaco"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Gastronomía",
        tipo="egreso",
        orden=2,
        icono="hamburguesa",
        color="#F59E0B",
        subcategorias=(
            SubcategoriaCatalogo("Restaurantes", 0, False, "11", "restaurante, resto, parrilla, pizzería, bodegón, almuerzo o cena afuera, tenedor libre"),
            SubcategoriaCatalogo("Delivery", 1, False, "11", "delivery, PedidosYa, Rappi, pedir comida, pizza o empanadas a domicilio"),
            SubcategoriaCatalogo("Cafetería", 2, False, "11", "café, cafetería, Starbucks, merienda o desayuno afuera, heladería"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Transporte",
        tipo="egreso",
        orden=3,
        icono="transporte",
        color="#0284C7",
        subcategorias=(
            SubcategoriaCatalogo("Transporte público", 0, False, "07", "colectivo, bondi, subte, tren, SUBE, carga de SUBE"),
            SubcategoriaCatalogo("Taxi y apps", 1, False, "07", "taxi, remis, Uber, Cabify, DiDi"),
            SubcategoriaCatalogo("Combustible", 2, False, "07", "nafta, combustible, GNC, gasoil, YPF, Shell, Axion"),
            SubcategoriaCatalogo("Estacionamiento", 3, False, "07", "estacionamiento, cochera, parquímetro, garaje, trapito"),
            SubcategoriaCatalogo("Peajes", 4, False, "07", "peaje, TelePASE"),
            SubcategoriaCatalogo("Auto", 5, False, "07", "mecánico, taller, service, cambio de aceite, gomería, repuestos, VTV, lavadero, seguro del auto"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Vivienda",
        tipo="egreso",
        orden=4,
        icono="luz",
        color="#EAB308",
        subcategorias=(
            SubcategoriaCatalogo("Alquiler", 0, False, "04", "alquiler de la casa o el departamento"),
            SubcategoriaCatalogo("Expensas", 1, False, "04", "expensas, administración del edificio"),
            SubcategoriaCatalogo("Luz", 2, False, "04", "luz, Edenor, Edesur, EPEC, factura de luz"),
            SubcategoriaCatalogo("Gas", 3, False, "04", "gas, Metrogas, Naturgy, Camuzzi, garrafa"),
            SubcategoriaCatalogo("Agua", 4, False, "04", "agua, AySA, Aguas Cordobesas"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Hogar",
        tipo="egreso",
        orden=5,
        icono="casa",
        color="#8B5CF6",
        subcategorias=(
            SubcategoriaCatalogo("Limpieza", 0, False, "05", "artículos de limpieza, lavandina, detergente, lavandería, tintorería"),
            SubcategoriaCatalogo("Muebles y electro", 1, False, "05", "muebles, colchón, heladera, lavarropas, microondas, decoración, bazar"),
            SubcategoriaCatalogo("Reparaciones", 2, False, "05", "plomero, electricista, gasista, cerrajero, pintor, arreglos de la casa, ferretería"),
            SubcategoriaCatalogo("Servicio doméstico", 3, False, "05", "empleada doméstica, mucama, niñera, señora o chica que limpia"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Salud",
        tipo="egreso",
        orden=6,
        icono="medicina",
        color="#10B981",
        subcategorias=(
            SubcategoriaCatalogo("Prepaga y obra social", 0, False, "06", "prepaga, obra social, OSDE, Swiss Medical, Galeno, Medifé, Sancor Salud, IOMA, cuota médica"),
            SubcategoriaCatalogo("Médico", 1, False, "06", "médico, consulta, especialista, pediatra, copago, estudios, análisis, laboratorio, ecografía"),
            SubcategoriaCatalogo("Farmacia", 2, False, "06", "farmacia, remedios, medicamentos, Farmacity"),
            SubcategoriaCatalogo("Dentista", 3, False, "06", "dentista, odontólogo, ortodoncia, brackets"),
            SubcategoriaCatalogo("Terapias", 4, False, "06", "psicólogo, terapia, kinesiólogo, nutricionista, fonoaudiólogo"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Cuidado personal",
        tipo="egreso",
        orden=7,
        icono="spa",
        color="#C026D3",
        subcategorias=(
            SubcategoriaCatalogo("Peluquería y belleza", 0, False, "12", "peluquería, corte de pelo, barbería, manicura, uñas, depilación, estética, spa"),
            SubcategoriaCatalogo("Perfumería", 1, False, "12", "perfume, cremas, maquillaje, shampoo, cosméticos, desodorante"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Ocio",
        tipo="egreso",
        orden=8,
        icono="entretenimiento",
        color="#EC4899",
        subcategorias=(
            SubcategoriaCatalogo("Salidas", 0, False, "09", "bar, boliche, cerveza, tragos, cine, teatro, recital, show, entradas, fiesta"),
            SubcategoriaCatalogo("Streaming", 1, False, "09", "Netflix, Spotify, Disney+, HBO Max, Prime Video, Paramount+, YouTube Premium, Crunchyroll"),
            SubcategoriaCatalogo("Hobbies y juegos", 2, False, "09", "videojuegos, PlayStation, Xbox, Steam, juguetes, libros para leer, manualidades, clases de música o arte"),
            SubcategoriaCatalogo("Deportes y gimnasio", 3, False, "09", "gimnasio, gym, club, fútbol, pádel, natación, yoga, pilates, entrenador"),
            SubcategoriaCatalogo("Viajes", 4, False, "09", "viaje, vacaciones, pasajes, vuelo, hotel, hostel, excursión, escapada"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Educación",
        tipo="egreso",
        orden=9,
        icono="libros",
        color="#DC2626",
        subcategorias=(
            SubcategoriaCatalogo("Colegio y facultad", 0, False, "10", "cuota del colegio, facultad, universidad, jardín, matrícula"),
            SubcategoriaCatalogo("Cursos", 1, False, "10", "curso, inglés, idiomas, capacitación, Duolingo, clases particulares"),
            SubcategoriaCatalogo("Libros y útiles", 2, False, "10", "útiles escolares, libros de estudio, fotocopias, librería"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Indumentaria",
        tipo="egreso",
        orden=10,
        icono="remera",
        color="#7C3AED",
        subcategorias=(
            SubcategoriaCatalogo("Ropa", 0, False, "03", "ropa, remera, pantalón, jean, campera, buzo, ropa interior"),
            SubcategoriaCatalogo("Calzado", 1, False, "03", "zapatillas, zapatos, botas, ojotas"),
            SubcategoriaCatalogo("Accesorios", 2, False, "03", "cartera, mochila, reloj, anteojos de sol, bijou, cinturón"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Tecnología",
        tipo="egreso",
        orden=11,
        icono="equipos",
        color="#6366F1",
        subcategorias=(
            SubcategoriaCatalogo("Celular", 0, False, "08", "abono o recarga del celular, Personal, Claro, Movistar, plan de datos"),
            SubcategoriaCatalogo("Internet", 1, False, "08", "internet, wifi, fibra, cable, Fibertel, Telecentro, Flow, DirecTV"),
            SubcategoriaCatalogo("Apps y programas", 2, False, "09", "ChatGPT, Claude, iCloud, Google One, Microsoft 365, Canva, Notion, Dropbox, apps pagas"),
            SubcategoriaCatalogo("Equipos", 3, False, "09", "celular nuevo, notebook, computadora, tablet, auriculares, televisor, consola, monitor"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Impuestos",
        tipo="egreso",
        orden=12,
        icono="impuestos",
        color="#78716C",
        subcategorias=(
            SubcategoriaCatalogo("ABL e inmobiliario", 0, False, "fuera", "ABL, rentas, impuesto inmobiliario, tasa municipal"),
            SubcategoriaCatalogo("Patente", 1, False, "fuera", "patente del auto o de la moto"),
            SubcategoriaCatalogo("Monotributo", 2, False, "fuera", "monotributo, autónomos"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Banco",
        tipo="egreso",
        orden=13,
        icono="banco",
        color="#64748B",
        subcategorias=(
            SubcategoriaCatalogo("Comisiones", 0, False, "12", "comisión, mantenimiento de cuenta, costo del resumen, impuesto al cheque"),
            SubcategoriaCatalogo("Préstamos", 1, False, "fuera", "cuota de un préstamo, crédito personal"),
            SubcategoriaCatalogo("Intereses", 2, False, "fuera", "intereses de la tarjeta, punitorios, intereses de un préstamo"),
            SubcategoriaCatalogo("Seguros", 3, False, "12", "seguro de hogar, de vida o del celular (el del auto va en Auto)"),
            SubcategoriaCatalogo("Tarjeta de crédito", 4, True, "fuera"),
            SubcategoriaCatalogo("Impuestos", 5, True, "fuera"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Otros",
        tipo="egreso",
        orden=14,
        icono="herramienta",
        color="#6B7280",
        subcategorias=(
            SubcategoriaCatalogo("Regalos", 0, False, "general", "regalo, cumpleaños de otra persona, donación"),
            SubcategoriaCatalogo("Mascotas", 1, False, "09", "veterinaria, alimento para perro o gato, piedritas, pipeta"),
            SubcategoriaCatalogo("Ayuda familiar", 2, False, "fuera", "plata para la familia (mamá, papá, abuelos, hijos)"),
            SubcategoriaCatalogo("Pagos a personas", 3, False, "general", "plata que le pasaste a alguien sin decir para qué"),
            SubcategoriaCatalogo("Pasó por mi cuenta", 4, False, "fuera", "plata de otra persona que solo pasó por tu cuenta: pagaste algo suyo con la plata que te mandó"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Ahorro",
        tipo="egreso",
        orden=90,
        icono="ahorro",
        color="#2563EB",
        subcategorias=(),
    ),
    # ========================== INGRESO ==========================
    CategoriaCatalogo(
        nombre="Trabajo",
        tipo="ingreso",
        orden=1,
        icono="salario",
        color="#16A34A",
        subcategorias=(
            SubcategoriaCatalogo("Sueldo", 0, False, None, "sueldo, quincena, salario, recibo de sueldo"),
            SubcategoriaCatalogo("Aguinaldo", 1, False, None, "aguinaldo, SAC"),
            SubcategoriaCatalogo("Bonos y extras", 2, False, None, "bono, premio, horas extras, comisión, propina"),
            SubcategoriaCatalogo("Freelance y changas", 3, False, None, "changa, trabajo particular, honorarios, factura de un trabajo"),
            SubcategoriaCatalogo("Ventas", 4, False, None, "venta de algo, vendí, Mercado Libre"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Jubilación y AUH",
        tipo="ingreso",
        orden=2,
        icono="jubilacion",
        color="#65A30D",
        subcategorias=(
            SubcategoriaCatalogo("Jubilación o pensión", 0, False, None, "jubilación, pensión"),
            SubcategoriaCatalogo("AUH y asignaciones", 1, False, None, "AUH, asignación familiar, salario familiar"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Inversiones",
        tipo="ingreso",
        orden=3,
        icono="dineroenmano",
        color="#2563EB",
        subcategorias=(
            SubcategoriaCatalogo("Intereses", 0, False, None, "intereses, plazo fijo, rendimiento, dividendos"),
            SubcategoriaCatalogo("Alquileres", 1, False, None, "alquiler cobrado de una propiedad"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Otros",
        tipo="ingreso",
        orden=4,
        icono="dineroenmano",
        color="#9CA3AF",
        subcategorias=(
            SubcategoriaCatalogo("Regalos", 0, False, None, "regalo recibido"),
            SubcategoriaCatalogo("Ayuda familiar", 1, False, None, "plata de la familia (mamá, papá, abuelos)"),
            SubcategoriaCatalogo("Cobros de personas", 2, False, None, "plata que alguien te pasó sin decir para qué"),
            SubcategoriaCatalogo("Pasó por mi cuenta", 3, False, None, "plata que te mandaron para que pagues algo de otro o se la pases a alguien"),
        ),
    ),
    CategoriaCatalogo(
        nombre="Ahorro",
        tipo="ingreso",
        orden=90,
        icono="ahorro",
        color="#2563EB",
        subcategorias=(),
    ),
)


# ------------------------------------------------------------------------------
# ALIAS_NOMBRES - Decisión 7
# Mapeo de nombres históricos a nombres nuevos para db_lookups y compatibilidad.
# ------------------------------------------------------------------------------

ALIAS_NOMBRES: dict[str, str] = {
    # Decisión 2: Categorías renombradas
    "Recreativo": "Ocio",
    "Comunicación": "Tecnología",
    "Equipamiento del hogar": "Hogar",
    "Empleo": "Trabajo",
    "Inversiones y rentas": "Inversiones",
    # Decisión 4: Subcategorías renombradas
    "Taxi / Apps": "Taxi y apps",
    "Mantenimiento y seguro del auto": "Auto",
    "Muebles y electrodomésticos": "Muebles y electro",
    "Obra social / Prepaga": "Prepaga y obra social",
    "Médico / Consulta": "Médico",
    "Odontología": "Dentista",
    "Cuotas": "Colegio y facultad",
    "Idiomas": "Cursos",
    "Materiales y libros": "Libros y útiles",
    "Internet y cable": "Internet",
    "Comisiones y gastos bancarios": "Comisiones",
    "Intereses pagados": "Intereses",
    "Bonos y horas extras": "Bonos y extras",
    "Dividendos e intereses": "Intereses",
    "Alquileres cobrados": "Alquileres",
    # Decisión 7: Adicionales explícitos
    "Trabajo independiente": "Trabajo",
    "Honorarios": "Freelance y changas",
    "Venta de productos/servicios": "Ventas",
    "Estudios y análisis": "Médico",
    "Impuesto al cheque / movimientos": "Comisiones",
    "Reintegros": "Otros",
    "Servicios": "Vivienda",
    "Entretenimiento": "Ocio",
    "Restaurantes y delivery": "Gastronomía",
    # Variantes en minúsculas / normalizadas (soporte T.2 y finanzas)
    "recreativo": "Ocio",
    "comunicacion": "Tecnología",
    "comunicación": "Tecnología",
    "equipamiento del hogar": "Hogar",
    "empleo": "Trabajo",
    "inversiones y rentas": "Inversiones",
    "trabajo independiente": "Trabajo",
    "taxi / apps": "Taxi y apps",
    "mantenimiento y seguro del auto": "Auto",
    "muebles y electrodomésticos": "Muebles y electro",
    "obra social / prepaga": "Prepaga y obra social",
    "médico / consulta": "Médico",
    "medico / consulta": "Médico",
    "odontología": "Dentista",
    "odontologia": "Dentista",
    "cuotas": "Colegio y facultad",
    "idiomas": "Cursos",
    "materiales y libros": "Libros y útiles",
    "internet y cable": "Internet",
    "comisiones y gastos bancarios": "Comisiones",
    "intereses pagados": "Intereses",
    "bonos y horas extras": "Bonos y extras",
    "dividendos e intereses": "Intereses",
    "alquileres cobrados": "Alquileres",
    "honorarios": "Freelance y changas",
    "venta de productos/servicios": "Ventas",
    "estudios y análisis": "Médico",
    "estudios y analisis": "Médico",
    "impuesto al cheque / movimientos": "Comisiones",
    "reintegros": "Otros",
    "servicios": "Vivienda",
    "entretenimiento": "Ocio",
    "restaurantes y delivery": "Gastronomía",
}


# ------------------------------------------------------------------------------
# Funciones auxiliares de lectura del catálogo
# ------------------------------------------------------------------------------

def obtener_arbol() -> tuple[CategoriaCatalogo, ...]:
    return ARBOL_CATEGORIAS


def obtener_categoria(nombre: str, tipo: TipoCategoria | None = None) -> CategoriaCatalogo | None:
    for cat in ARBOL_CATEGORIAS:
        if cat.nombre == nombre and (tipo is None or cat.tipo == tipo):
            return cat
    return None


def obtener_subcategoria(
    categoria_nombre: str, subcategoria_nombre: str, tipo: TipoCategoria | None = None
) -> SubcategoriaCatalogo | None:
    cat = obtener_categoria(categoria_nombre, tipo)
    if not cat:
        return None
    for sub in cat.subcategorias:
        if sub.nombre == subcategoria_nombre:
            return sub
    return None


def resolver_alias(nombre: str) -> str:
    """Devuelve el nombre nuevo si existe en ALIAS_NOMBRES, o el nombre original."""
    if not nombre:
        return nombre
    nombre_limpio = nombre.strip()
    return ALIAS_NOMBRES.get(nombre_limpio, ALIAS_NOMBRES.get(nombre_limpio.lower(), nombre_limpio))


def nombres_categorias_visibles(tipo: TipoCategoria | None = None) -> set[str]:
    return {c.nombre for c in ARBOL_CATEGORIAS if tipo is None or c.tipo == tipo}


def nombres_subcategorias_visibles(tipo: TipoCategoria | None = None) -> set[str]:
    res: set[str] = set()
    for c in ARBOL_CATEGORIAS:
        if tipo is None or c.tipo == tipo:
            for s in c.subcategorias:
                if not s.es_sistema:
                    res.add(s.nombre)
    return res


def todos_los_nombres_visibles(tipo: TipoCategoria | None = None) -> set[str]:
    return nombres_categorias_visibles(tipo) | nombres_subcategorias_visibles(tipo)
