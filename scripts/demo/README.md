# Usuario Demo de Argentum (Lucía Demo)

Este módulo provee la generación completa, determinística y realista del usuario demo `demo.argentum@argentum.com`.

## Perfil de la Persona

- **Identidad**: Lucía Demo, 29 años, diseñadora gráfica en relación de dependencia en CABA. Vive sola y alquila.
- **Billeteras**:
  - `Galicia` (ARS, principal, cobro de sueldo).
  - `Mercado Pago` (ARS, gastos cotidianos y transferencias).
  - `Efectivo` (ARS, saldo inicial $40.000, cafés y controles mensuales).
  - `Dólares` (USD, compras mensuales de ahorro y gastos del viaje a Brasil).
  - `Balanz` (ARS, inversión con TNA 32% y devengamiento mensual de rendimientos).
- **Tarjetas de crédito**:
  - `Visa Galicia` (cierre día 20, vencimiento día 3 del mes siguiente, percepción 30%).
- **Flujos financieros**:
  - Sueldo neto con paritarias y medio aguinaldo (SAC) semestral.
  - Gastos fijos (alquiler, expensas, luz, gas, agua).
  - Suscripciones mensuales (Netflix, Spotify, ChatGPT Plus en USD, Gym, Prepaga, Celular).
  - Gastos variables y cotidianos (supermercado, delivery, café, SUBE, salidas, farmacia, indumentaria).
  - Compras en cuotas (electrodomésticos, calzado, tecnología).
  - Metas de ahorro ("Viaje a Brasil" con aportes y retiro de fondos, "Fondo de emergencia").
  - Inversiones en Balanz y rendimientos de cuenta remunerada.
  - Presupuestos mensuales (Supermercado, Delivery, Salidas, Café).
  - Reintegros comerciales (promociones de supermercado recibidas y pendientes).
  - Casos especiales: "Pasó por mi cuenta", "¿Qué es esta plata?", y devolución total de compra.

## Modos de Ejecución

El script CLI principal es `scripts.demo.cargar_demo`:

### 1. Creación Inicial (`--crear`)
Crea la cuenta de usuario y genera todos los movimientos del plan desde el 25/05/2026 hasta la fecha indicada (`--hoy`), además de proyectar a futuro aquellos eventos que la aplicación permite fechar anticipadamente (hasta el 21/08/2027).

```bash
# La clave se ingresa interactivamente por consola o mediante la variable de entorno DEMO_PASSWORD
python -m scripts.demo.cargar_demo --crear
```

Para pruebas o simulaciones con fecha fija:
```bash
python -m scripts.demo.cargar_demo --crear --hoy 2026-11-02
```

### 2. Ponerse al Día (`--ponerse-al-dia`)
Actualiza los movimientos que no podían fecharse a futuro en la creación inicial (pagos de resumen de tarjeta, rendimientos devengados, aportes a metas y controles de saldo) que hayan vencido entre la última carga y la fecha actual. Es una operación idempotente.

```bash
python -m scripts.demo.cargar_demo --ponerse-al-dia
```

### 3. Borrado Completo (`--borrar`)
Elimina al usuario demo y la totalidad de sus billeteras, tarjetas, transacciones, metas, suscripciones y presupuestos utilizando el servicio oficial `usuario_service.eliminar_usuario`. Requiere confirmación explícita escribiendo la dirección de email del usuario demo.

```bash
python -m scripts.demo.cargar_demo --borrar
```

## Reglas de Seguridad y Aislamiento

1. **Aislamiento estricto**: Todas las operaciones filtran exclusivamente por el identificador del usuario demo (`demo.argentum@argentum.com`). Ninguna fila perteneciente a otro usuario es consultada ni alterada.
2. **Notificaciones silenciadas**: La cuenta se crea con todas las alertas por correo electrónico y WhatsApp desactivadas.
3. **Determinismo**: La generación utiliza una semilla fija para asegurar reproducibilidad exacta.
4. **Respeto a las reglas de negocio**: Los movimientos se crean mediante los servicios oficiales de la aplicación (`transaccion_service`, `pago_resumen_service`, `meta_service`, etc.), garantizando la integridad de saldos y relaciones.

