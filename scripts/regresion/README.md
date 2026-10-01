# Suite de Regresión y Pruebas Locales (Argentum Backend)

Esta carpeta y el submódulo `scripts/local/` contienen el entorno automatizado de pruebas, verificación de consistencia transaccional y suite de regresión para el webhook de WhatsApp y el motor financiero de Argentum.

---

## 1. Cobertura de la Suite de Regresión WhatsApp (174 escenarios)

La suite consolidada (`scripts/regresion/suite_regresion_whatsapp.py`) valida de forma exhaustiva el comportamiento del asistente ante todos los casos de uso documentados:

- **Punto 3: Resolución determinística de billeteras** (10 escenarios). Billetera principal implícita, menús numéricos y por nombre, opciones fuera de rango, respuestas a números aislados, menús expirados, corrección de billetera en propuesta interactiva y usuarios con billetera única.
- **Punto 4: Detección de intenciones y gestión de contexto** (14 escenarios). Cancelaciones explícitas, reseteo de slots ante saludos o cambios de tema, reanudación de operaciones a medias, preguntas fuera de alcance, 6 variantes idiomáticas de negación ("no", "nada", "de ninguna manera", etc.) y expiración de propuestas por inactividad.
- **Punto 5: Integridad transaccional y prevención de duplicados** (8 escenarios). Prevención de duplicados idénticos en ventana corta, idempotencia ante reenvíos de webhook, concurrencia en confirmación rápida y exclusión estricta de cuotas hijas o planes de tarjeta en cálculos directos.
- **Punto 6: Manejo temporal y parámetros cuantitativos** (10 escenarios). Fechas relativas ("ayer", "el viernes pasado") y absolutas, rechazo de fechas >60 días en el pasado o futuras, transacciones en dólares estadounidenses (USD) con tasa implícita, límites cuantitativos, descarte anticipado de transacciones inválidas en lotes y estricta privacidad de saldos.
- **Punto 7: Jerga argentina, modismos y categorización por IA** (7 escenarios con validación de LLM real). Modismos cotidianos ("golosinas" -> Kiosco, "bondi" -> Transporte público, "nafta" -> Combustible, "prepaga" -> Obra social / Prepaga, "corte de pelo" -> Cuidado personal, etc.), prohibición de inventar categorías y preservación de descripciones originales.
- **Punto 8: Modificaciones y reversiones interactivas** (11 escenarios). Registro y posterior anulación inmediata ("borrá eso"), confirmación de cancelación, eliminación de transacciones y reversión exacta de saldos.
- **Puntos 9A y 9B: Transferencias y cajero/dólares** (32 escenarios: 12 en 9A y 20 en 9B). Transferencias origen-destino con o sin comisión, validación de saldos en ambas cuentas y consistencia contable.
- **Punto 10: Suscripciones y servicios periódicos** (13 escenarios). Detección de servicios recurrentes (Netflix, Spotify, gimnasio), solicitud de frecuencia de facturación, alta de la suscripción sin impacto prematuro en saldos de transacciones.
- **Punto 11: Multimoneda y conversiones** (13 escenarios). Billeteras en ARS y USD, registro de compras en moneda extranjera y cálculo consistente de tenencias.
- **Punto 12: Consultas analíticas y proyecciones financieras** (13 escenarios). Detección de intención analítica, resumen de presupuestos mensuales y proyecciones de flujo de fondos (`consultar_proyeccion`).
- **Punto 13: Tarjetas de crédito** (5 escenarios). Selección de tarjeta, cuotas, cálculo de primer vencimiento y validaciones.
- **Punto 14: Cuotas y pagos en tarjeta** (17 escenarios). Manejo de cuotas fijas, aclaraciones de cuotas ambiguas, opciones y menús de tarjeta.
- **Puntos 16 y 17: Metas de ahorro y procesamiento por lotes** (21 escenarios: 6 en Punto 16 y 15 en Punto 17). Aportes a metas, metas completadas con felicitación, reversión de aportes deshechos, y procesamiento de lotes con frases introductorias compuestas y fechas previas.

---

## 1.1 Estructura Modular de la Suite (`scripts/regresion/suite/`)

A partir de la Fase 3d-3, la suite se encuentra modularizada bajo el paquete `scripts/regresion/suite/`, manteniendo `scripts/regresion/suite_regresion_whatsapp.py` como CLI y orquestador principal:

- **`suite_regresion_whatsapp.py`**: Punto de entrada CLI consolidado. Mantiene exactamente los mismos flags (`--forzar-grabadas`, `--volcar-salidas <ruta>`, `--escenario <id>`, `-v`), orden de ejecución y flujo de control.
- **`suite/comun.py`**: Aislamiento transaccional con rollback automático (`run_isolated`), simulaciones seguras de usuario y mensajes, grabaciones determinísticas de IA (replay), guarda contra llamadas salientes a `graph.facebook.com` y colector de salidas para comparación fotográfica.
- **`suite/controles.py`**: Verificaciones posteriores a la suite: conteos por tabla, validación de saldos de billeteras contra referencias históricas y chequeo de reconciliación contable.
- **`suite/catalogo.py`**: Catálogo centralizado y ordenado de los 174 escenarios ejecutables, mapeando cada ID a su bloque y aserción esperada.
- **Módulos de Escenarios Temáticos**:
  - `escenarios_p03.py`: Resolución de billeteras (Punto 3).
  - `escenarios_p04.py`: Intenciones, cancelaciones y expiración (Punto 4).
  - `escenarios_p05.py`: Integridad transaccional y prevención de duplicados (Punto 5).
  - `escenarios_p06_p07.py`: Manejo temporal, USD y jerga argentina (Puntos 6 y 7).
  - `escenarios_p08.py`: Modificaciones y reversiones interactivas (Punto 8).
  - `escenarios_p09.py`: Transferencias simples e internas (Punto 9A).
  - `escenarios_p10.py`: Suscripciones y servicios periódicos (Punto 10).
  - `escenarios_p11.py`: Multimoneda y conversiones (Punto 11).
  - `escenarios_p12.py`: Consultas analíticas y proyecciones (Punto 12).
  - `escenarios_p13.py`: Tarjetas de crédito y cuotas simples (Punto 13).
  - `escenarios_p14.py`: Menús de tarjeta, aclaración de cuotas y desambiguación (Punto 14 y 15).
  - `escenarios_p16.py`: Metas de ahorro y aportes interactivos (Punto 16).
  - `escenarios_p17.py`: Lotes complejos, fechas previas y ajustes de marcas (Punto 17).

---

## 2. Ejecución contra Base de Datos Local (Recomendado)

El entorno local funciona sobre una instancia portable de **PostgreSQL 18** en el puerto `5433` con base de datos `argentum_local`, idéntica en estructura, datos, extensiones (`pgcrypto`, `plpgsql`, `uuid-ossp`) y ordenamiento (ICU `en-US` UTF8) a la base de producción.

Para ejecutar cualquier script o herramienta apuntando a la base local de forma transparente:

```bash
# Ejecutar la suite completa contra la base local
python scripts/local/con_base_local.py python scripts/regresion/suite_regresion_whatsapp.py -v

# Ejecutar la foto del motor contra la base local
python scripts/local/con_base_local.py python scripts/motor/foto_motor.py --etiqueta mi_foto_local

# Ejecutar el verificador de testingadmin contra la base local
python scripts/local/con_base_local.py python scripts/testingadmin/verificar_testingadmin.py

# Ejecutar pytest contra la base local
python scripts/local/con_base_local.py python -m pytest -q
```

`con_base_local.py` se encarga automáticamente de:
1. Comprobar si el servidor local está activo con `pg_ctl status`.
2. Si está detenido (por ejemplo, tras reiniciar la máquina), lo levanta automáticamente en segundo plano.
3. Inyecta `DATABASE_URL` y variables `PG*` apuntando a `localhost:5433/argentum_local`.
4. Todas las herramientas imprimen al inicio: `BASE: LOCAL (localhost:5433/argentum_local)`.

---

## 3. Cómo Refrescar la Base Local desde Producción

Para sincronizar la base local con el estado más reciente de producción:

```bash
python scripts/local/refrescar_base_local.py
```

Flujo automatizado de refresco:
1. Valida que el servidor local esté activo y que el comando apunte inequívocamente a `localhost:5433/argentum_local` (protección contra sobreescritura accidental).
2. Genera un volcado limpio (`pg_dump -Fc`) de producción hacia `C:\argentum_local\dumps\argentum_prod.dump`.
3. Reinicia las conexiones activas, elimina y recrea la base `argentum_local`.
4. Restaura el esquema y datos completos con `pg_restore`.
5. Ejecuta un control de integridad comparando el conteo exacto de filas en las 38 tablas entre producción y local.
*Duración promedio del refresco: ~45 segundos.*

---

## 4. Ejecución contra Producción (Uso Restringido)

Para correr directamente contra producción (únicamente cuando sea indispensable y autorizado):

```bash
python scripts/regresion/suite_regresion_whatsapp.py -v
```

Al invocarse directamente (sin `con_base_local.py`), la herramienta lee el `.env` del repositorio que apunta a la base de producción.
El script imprimirá en la primera línea: `BASE: PRODUCCION (<host_censurado>)` como recordatorio explícito.

---

## 5. Benchmarks: Producción vs Base Local

La migración de pruebas a PostgreSQL 18 local elimina la latencia de red contra Supabase/Neon y optimiza radicalmente los tiempos de ciclo de desarrollo:

| Herramienta / Operación | Producción (Cloud) | Base Local (PG 18) | Factor de Aceleración |
|:---|:---:|:---:|:---:|
| **Foto del Motor (441 métricas)** | 135.38 s | 17.86 s | **7.58x más rápido** |
| **Verificador testingadmin** | 29.86 s | 1.80 s | **16.61x más rápido** |
| **Suite WhatsApp (174 escenarios)** | ~180 - 220 s | ~51.38 s | **3.5x - 4.3x más rápido** |
| **Refresco Integral de DB** | - | ~45.00 s | *38/38 tablas idénticas* |

---

## 6. Script Integrador: `verificar_todo.py`

Para realizar una validación de punta a punta antes de realizar commits o pull requests:

```bash
# Verificación completa con refresco previo de la base local:
python scripts/local/verificar_todo.py

# Verificación rápida offline (reutiliza la base local existente sin refrescar):
python scripts/local/verificar_todo.py --sin-refrescar
```

El script integrador ejecuta secuencialmente:
1. `refrescar_base_local.py` (omitible con `--sin-refrescar`).
2. `pytest -q` contra base local.
3. `suite_regresion_whatsapp.py -v` (174/174 escenarios).
4. `verificar_testingadmin.py`.
5. Muestra una tabla consolidada con el estado (OK/FALLO) y la duración exacta de cada fase.
