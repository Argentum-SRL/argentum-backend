# Generador de Historial Realista de testingadmin@argentum.com

## Propósito
Este módulo regenera de forma integral y determinística el historial financiero del usuario `testingadmin@argentum.com` para simular el comportamiento de una persona real (empleado en relación de dependencia en CABA, inquilino, que utiliza tarjetas de crédito, paga sus resúmenes mes a mes, invierte y ahorra en metas).

## Estructura de Módulos
1. **`seguridad.py`**:
   - Verificación canaria de usuarios requeridos.
   - Verificación estricta del ID y email de `testingadmin`.
   - Interceptación en memoria (mocking) de todo envío de WhatsApp, Email o notificaciones push para evitar comunicaciones no deseadas.
   - Borrado consistente y seguro de registros previos respetando integridad referencial.
   - Monitoreo de snapshot de cuentas ajenas para garantizar su aislamiento.
2. **`datos_base.py`**:
   - Catálogos de billeteras, tarjetas de crédito, categorías y subcategorías.
   - Carga y cálculo de IPC real acumulado desde la tabla `ipc_cache`.
   - Cálculo determinístico de los 14 ciclos financieros (desde agosto 2025 hasta septiembre 2026) usando las funciones oficiales del backend (`calcular_inicio_ciclo_para_mes_ancla` y `get_ciclo_fechas`).
3. **`generador_ingresos.py`**:
   - Depósito de sueldo inicial neto ($2.600.000) con aumentos paritarios escalonados (3% a 5% cada 2 a 4 meses).
   - Sin marca de `es_recurrente`.
   - Medio aguinaldo (SAC) en junio y diciembre por la mitad del mejor sueldo del semestre.
   - Trabajos freelance extraordinarios (incluyendo uno en dólares en la billetera Efectivo USD).
4. **`generador_fijos.py`**:
   - Alquiler con ajuste trimestral según IPC real de `ipc_cache`.
   - Expensas variables mensuales.
   - Servicios bimestrales alternados (Luz y Gas con estacionalidad de invierno).
   - Comunicaciones (Internet y Celular) con subas en escalones.
   - Prepaga de salud con suba mensual sostenida.
   - Servicio con escalones: "Clases de guitarra" (inicia en $60.000 y sube $5.000 cada 2-3 meses).
5. **`generador_variables.py`**:
   - Gastos cotidianos: supermercado, almacén, verdulería, farmacia, SUBE y combustible.
   - Costumbres con montos variables: delivery, café, salidas de fin de semana y taxis/apps.
   - Temporadas: diciembre con gastos navideños y enero con vacaciones.
   - Carga incompleta: mayo 2026 (Ciclo 10) con registro de solo ~40% de gastos del día a día y costumbres.
6. **`generador_tarjetas_suscripciones.py`**:
   - Suscripciones mensuales (Netflix, Spotify) y anual (Google One), con historial de precios y un cobro corregido en Spotify.
   - Compras en cuotas: Smart TV (12 cuotas con interés), Heladera (12 cuotas sin interés) y Zapatillas (3 cuotas).
   - Compras en 1 pago y consumos en USD con Amex.
   - Pago mensual de resúmenes de tarjeta utilizando el servicio oficial `tarjeta_service.pagar_resumen_tarjeta`.
   - Pago parcial en abril 2026 que genera saldo arrastrado cancelado al mes siguiente.
7. **`generador_metas_inversiones.py`**:
   - Aportes mensuales a las metas "Fondo de Emergencia" ($100.000) y "Viaje a Bariloche" ($50.000).
   - Transferencia mensual a la billetera de inversión "Ahorro con rendimiento" y confirmación de rendimientos mensuales devengados vía `confirmar_rendimiento`.
   - Transferencias entre cuentas (extracciones a efectivo y fondeo a Santander).
   - Creación y períodos de presupuestos ("Gastronomía y Salidas" excedido en 3 de los últimos 4 ciclos; "Indumentaria" bajo control).
   - Recálculo de saldos de billeteras, metas, calibración y perfil financiero.
8. **`maestro.py`**:
   - Orquestador de la corrida con semilla fija `random.Random(2026)`.
   - Exporta la verdad conocida a `verdad_testingadmin.json`.
