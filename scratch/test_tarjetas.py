from datetime import date
import sys
import os

# Agregar el path para importar el servicio
sys.path.append(os.path.abspath(os.path.join(os.getcwd(), 'argentum-backend')))

from app.services.tarjeta_service import calcular_primer_vencimiento

def test_vencimientos():
    casos = [
        # Caso 1: Compra antes del cierre (Día 8, cierre 10, vence 3) -> 3 del mes siguiente
        {"fecha": date(2024, 3, 8), "cierre": 10, "vence": 3, "esperado": date(2024, 4, 3)},
        # Caso 2: Compra después del cierre (Día 12, cierre 10, vence 3) -> 3 de dos meses adelante
        {"fecha": date(2024, 3, 12), "cierre": 10, "vence": 3, "esperado": date(2024, 5, 3)},
        # Caso 3: Diciembre antes del cierre (Día 5, cierre 10, vence 3) -> 3 Enero
        {"fecha": date(2024, 12, 5), "cierre": 10, "vence": 3, "esperado": date(2025, 1, 3)},
        # Caso 4: Diciembre después del cierre (Día 15, cierre 10, vence 3) -> 3 Febrero
        {"fecha": date(2024, 12, 15), "cierre": 10, "vence": 3, "esperado": date(2025, 2, 3)},
        # Caso 5: Vencimiento 31 en mes corto (vence 31, mes destino Febrero) -> 28/29 Feb
        {"fecha": date(2024, 1, 15), "cierre": 10, "vence": 31, "esperado": date(2024, 3, 31)}, # Enero -> Marzo (31 existe)
        {"fecha": date(2024, 1, 1), "cierre": 10, "vence": 31, "esperado": date(2024, 2, 29)}, # Enero -> Feb (29 existe en 2024)
        {"fecha": date(2023, 1, 1), "cierre": 10, "vence": 31, "esperado": date(2023, 2, 28)}, # Enero -> Feb (28 en 2023)
    ]

    print("\n--- TEST: calcular_primer_vencimiento ---")
    for i, c in enumerate(casos):
        res = calcular_primer_vencimiento(c["fecha"], c["cierre"], c["vence"])
        status = "✅" if res == c["esperado"] else "❌"
        print(f"Caso {i+1}: Compra {c['fecha']} (Cierre {c['cierre']}, Vence {c['vence']}) -> Res: {res} | Esperado: {c['esperado']} {status}")

if __name__ == "__main__":
    test_vencimientos()
