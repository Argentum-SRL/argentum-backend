# Herramientas del Motor Financiero de Argentum

Este directorio contiene la infraestructura de medición cuantitativa y control de regresión del motor financiero de Argentum, diseñada para la Fase 0 del rediseño integral.

## Herramientas

1. **`foto_motor.py`**: Registra una fotografía cuantitativa exhaustiva y determinística del estado actual de todos los cálculos del backend para el usuario `testingadmin@argentum.com`.
2. **`mediciones.py`**: Módulo con la lógica de extracción de cada bloque financiero (Billeteras, Saldo Disponible, Gastos del Ciclo, Ingresos, Perfil, Proyección, Clasificación, Herramientas, WhatsApp, etc.).
3. **`comparar_fotos.py`**: Compara dos fotografías JSON, reportando claves nuevas, claves desaparecidas y variaciones de valor numéricas y porcentuales.

---

## Red de Seguridad y Garantías

La ejecución de estas herramientas está blindada contra escrituras accidentales en la base de datos de producción:
- **Restricción estricta de usuario**: Solo permite medir a `testingadmin@argentum.com` (UUID `4c2ed62e-c22e-4d21-9bf9-ec6705f2c6fa`). Con cualquier otro usuario el proceso aborta inmediatamente.
- **Interceptor `before_commit`**: Se asocia un listener al evento de sesión de SQLAlchemy que intercepta y aborta con `RuntimeError` cualquier intento de commit originado en cualquier función de la app.
- **Rollback incondicional**: La sesión se cierra siempre con `db.rollback()` y `db.close()` en un bloque `finally`.
- **Aislamiento por bloque**: Cada bloque de medición (A a P) se ejecuta en un bloque `try/except` individual; si un bloque falla, guarda el mensaje de error y el resto de las mediciones continúa.

---

## Modo de Uso

### 1. Tomar una foto del motor
Desde la raíz del repositorio backend (`argentum-backend/`):

```bash
python scripts/motor/foto_motor.py --etiqueta actual_1
```

Genera dos archivos en `auditorias/fotos/`:
- `foto_<AAAAMMDD_HHMM>_<etiqueta>.json`: Formato plano clave → valor ordenado alfabéticamente.
- `foto_<AAAAMMDD_HHMM>_<etiqueta>.txt`: Formato legible con sangría y valores alineados.

### 2. Probar la red de seguridad de bloqueo de commits
Para comprobar que el listener bloquea efectivamente cualquier intento de commit:

```bash
python scripts/motor/foto_motor.py --probar-bloqueo
```

Debe imprimir en consola:
`CORRECTO: Commit interceptado y bloqueado con éxito: BLOQUEO DE SEGURIDAD...`

### 3. Comparar dos fotos
Para comparar una foto previa con una posterior a cambios:

```bash
python scripts/motor/comparar_fotos.py ../auditorias/fotos/foto_A.json ../auditorias/fotos/foto_B.json
```

Para omitir las diferencias triviales de fecha, hora y nombres de archivo de la foto:

```bash
python scripts/motor/comparar_fotos.py ../auditorias/fotos/foto_A.json ../auditorias/fotos/foto_B.json --ignorar-metadatos
```

**Códigos de salida:**
- `0`: Las fotos son cuantitativa y estructuralmente idénticas.
- `1`: Se encontraron diferencias.

---

## Bloques Medidos

- **Bloque A (Datos de la Foto)**: Fecha y hora UTC/local, hash de commit y rama de ambos repositorios (`argentum-backend` y `argentum-frontend`), configuración de ciclo del usuario y rangos del ciclo actual y siguiente.
- **Bloque B (Billeteras)**: Lista de billeteras activas (nombre, moneda, inversión, efectivo, saldo) y totales consolidados por moneda con inversión (regla frontend `calcularTotales`) y sin inversión.
- **Bloque C (Saldo Disponible)**: Saldo disponible canónico vía `_calcular_saldo_disponible_sync`, con desglose de billeteras, cuotas comprometidas y suscripciones mensuales en ARS y USD.
- **Bloque D (Gasto del Ciclo)**: Gasto consolidado según consulta determinística de WhatsApp, resumen del dashboard (`Query 1`), gastos por categoría (`cat_stmt`), balance de ciclo, suma con filtro `es_gasto_consumo`, gasto en presupuestos, gasto variable de `obtener_contexto_financiero`, y desglose de tarjetas (padres, cuotas hijas con vencimiento en el ciclo y pagos de resumen).
- **Bloque E (Ingreso)**: Ingreso promedio mensual de `tools_service`, ingreso típico del perfil deflactado con IPC, ingresos proyectados de la proyección y balance del ciclo.
- **Bloque F (Perfil Financiero)**: Todas las métricas cuantitativas de `calcular_perfil_nuevo`, campos persistidos en `PerfilFinanciero` y texto inyectado en el contexto IA.
- **Bloque G (Proyección)**: Proyección probabilística para ARS y USD (balance proyectado, gasto proyectado, desglose por certeza, descomposición, rango optimista/esperado/pesimista, y estado de calibración).
- **Bloque H (Clasificación de Gastos)**: Conteo de streams recurrentes por clase (`COMPROMISO`, `HABITO`, `VARIABLE`) y detalle de cada stream (frecuencia, ocurrencias, monto mediano deflactado y estado).
- **Bloque I (¿Me lo puedo permitir?)**: Evaluación de 4 escenarios estándar (S1: 300k contado, S2: 300k en 6 cuotas, S3: 1.2M en 12 cuotas, S4: 80k contado) con semáforos, mensajes y márgenes libres.
- **Bloque J (Cuotas vs Contado)**: Comparación para el escenario S3 con inflación mensual del 1,7%.
- **Bloque K (Textos de WhatsApp)**: Textos generados por los enriquecedores post-IA para consultas de saldo, balance, proyección, metas, presupuestos, gastos del mes y suscripciones.
- **Bloque L (Contexto de la IA)**: Salida completa aplanada de `_construir_contexto_financiero_uncached`.
- **Bloque M (Suscripciones)**: Suscripciones registradas con frecuencia, próximo cobro, precio vigente, costo mensual equivalente e historial de precios.
- **Bloque N (Cuotas)**: Cuotas impagas vencidas y por vencer del ciclo actual, del ciclo siguiente y deuda total pendiente.
- **Bloque O (Presupuestos y Metas)**: Límites, montos usados y porcentajes de metas activas.
- **Bloque P (IPC y Dólar)**: Últimos 3 registros de IPC en caché y últimas cotizaciones de dólar registradas en base de datos.
