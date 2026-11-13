# Argentum backend

## Cómo responderle a Sebastián (regla n.º 1, 26/10/2026)
Sin explicaciones y sin vueltas. Solo: indicaciones, prompts, pushear o no, comandos completos y decisiones de negocio, siempre en pasos simples. Nada de causas técnicas, diagnósticos ni hallazgos.

## Estructura vigente (actualizada el 21/10/2026)

Esta sección manda: si otra parte de este archivo dice algo distinto, vale lo que dice acá.
Plan vigente (único): Project de Claude.ai, claude/PLAN_VIGENTE_12-11.md (versión 4). Ante una contradicción entre este archivo y el plan, vale el plan.

### Reglas del motor
- Qué es gasto y qué es ingreso lo decide solo app/services/definiciones_service.py. Ningún otro archivo filtra gastos o ingresos por su cuenta.
- El saldo oficial de una billetera es conciliacion_service.calcular_saldo_teorico (incluye rendimientos y ajustes de saldo). Un movimiento mueve el saldo solo si está confirmado, no es de crédito y su fecha es hoy o anterior (transaccion_service._afecta_saldo).
- Los ajustes de "Actualizar saldo" (solo web) viven en la tabla ajustes_saldo (app/services/ajuste_saldo_service.py): mueven el saldo y nunca son gasto ni ingreso. Cada control queda guardado, aunque no haya diferencia, y su historia mide la cobertura.
- Las billeteras de inversión (es_inversion) quedan fuera de todo número: lo cargado en ellas no es gasto ni ingreso.
- Las cuentas las hace siempre el sistema; la IA solo entiende y redacta.
- Puerta única del saldo (Bloque D, 22/10/2026): solo app/services/saldo_billetera_service.py escribe saldo_actual, saldo_inicial, fecha_saldo y fecha_ultimo_control (lo controla tests/test_saldo_puerta_estatica.py). Para mover saldo: aplicar_impacto_saldo(billetera, impacto, fecha) (bloquea la fila solo) o las funciones con nombre registrar_control_en_saldo, revertir_control_en_saldo, aplicar_rendimiento_en_saldo y mover_fecha_saldo. Si una operación toca dos billeteras, primero bloquear_billeteras(db, [ids], usuario_id) con las dos juntas (orden fijo por id; hace flush antes de leer porque la sesión tiene autoflush=False).
- Foto del saldo (D-H3): fecha de la foto = la mayor entre la fecha del saldo (si tiene punto de partida) y el último "Actualizar saldo" (billeteras.fecha_ultimo_control). Lo anterior a la foto va a saldo_inicial; lo demás, a saldo_actual. Con un control cargado la fecha del saldo ya no se mueve (422). Un rendimiento con fecha futura da 400. Una cuota hija sin crédito que movió el saldo cuenta en el teórico y su cuota cuenta como pagada.
- Vigilancia: el job vigilar_deriva_saldos (05:30, app/services/deriva_saldo_service.py) avisa por mail al admin cuántas billeteras tienen saldo guardado distinto del teórico, sin datos de usuarios.
- Deuda que sigue (Bloque E): PeriodoPresupuesto.monto_usado y Meta.monto_actual son acumulados aparte del cálculo y no coinciden siempre con él (las compras con crédito no tocan monto_usado). Código nuevo: leer lo usado de un presupuesto con calcular_gasto_en_periodo.
- "Hoy" se calcula con app/utils/fecha.py (hoy_argentina). Todo monto que ve el usuario pasa por app/utils/formato.py (formatear_monto).

### Punto de partida del saldo y tipos de números (fase_saldo_punto_partida)
- **Punto de partida**: El saldo inicial es una foto fechada (`fecha_saldo`), no un movimiento ni un ingreso. Una billetera con saldo inicial 0 se crea sin punto de partida (`tiene_punto_partida=False`): todo movimiento suma o resta sin importar su fecha. Con saldo > 0 (`tiene_punto_partida=True`), rige la fecha de saldo (`fecha_saldo_efectiva`): movimientos con fecha igual o posterior impactan en `saldo_actual`; movimientos anteriores no mueven `saldo_actual` y solo descuentan de `saldo_inicial`. La fecha de saldo se puede mover hacia atrás mediante `fecha_saldo_service` con validación invariante contra `calcular_saldo_teorico`.
- **Tres tipos de números**:
  1. *Historia*: Lo que realmente ocurrió (movimientos registrados). El saldo inicial nunca es ingreso, sueldo ni ahorro ni entra en ingreso habitual, gasto típico, tasa de ahorro, patrones, presupuestos ni balance del ciclo.
  2. *Plata de hoy*: Dinero disponible ahora (`saldo_actual`), calculado como foto inicial más movimientos desde la fecha de saldo (o todos si no tiene punto de partida), más rendimientos y ajustes.
  3. *Proyecciones*: Flujo futuro a partir del saldo actual considerando compromisos futuros y pagos programados.
- **Avisos de saldo en WhatsApp**:
  - Máximo una sola línea por mensaje del bot (< 90 caracteres).
  - Dato primero: avisa únicamente si hay movimientos confirmados con fecha anterior a la fecha de saldo de billeteras con punto de partida (nunca con crédito ni billeteras sin punto de partida).
  - Textos concisos y directos: "Ojo: es anterior a tu saldo de «...» (dd/mm) y no lo modifica."

### Servicios del motor (app/services/)
- definiciones_service, ingreso_habitual_service, compromisos_service, conciliacion_service, datos_motor_service, perfil_financiero_service, resumen_tarjeta_service, pago_resumen_service, tarjeta_service.

### Escrituras
- transaccion_service (crear_transaccion, actualizar, eliminar, confirmar_transaccion_ia), transferencia_service, meta_service, pago_resumen_service, cuotas_service, rendimiento_billetera_service, ajuste_saldo_service.
- Toda operación que escribe recibe commit: bool = True. Con commit=False hace flush y nunca commit. Una operación que llama a otra le pasa commit=False. Los recálculos derivados (perfil) van después del commit y no pueden deshacer la operación.
- Idempotencia (Bloque D, 24/10/2026): las 7 rutas que mueven plata (POST /transacciones, /transferencias, /tarjetas/{id}/pagar, /goals/{id}/movimientos, /grupos-cuotas/{id}/prepagar, /billeteras/{id}/rendimiento y /billeteras/{id}/ajustes) llaman a idempotencia_service.ejecutar con su servicio en commit=False. Si el pedido trae Idempotency-Key, la misma clave con la misma ruta y el mismo cuerpo devuelve la respuesta guardada sin volver a operar; con otra ruta u otro cuerpo da 422. La fila de claves_idempotencia va en la misma transacción que la operación (si falla, no queda nada) y se borra a las 24 horas (job limpiar_tokens). Sin encabezado todo funciona como antes. Una ruta nueva que mueva plata sigue el mismo patrón.

### WhatsApp
- app/routers/whatsapp_ia.py es solo el orquestador. Las etapas están en app/routers/whatsapp/: etapa_entrada, etapa_handlers, etapa_ia, etapa_resolucion, etapa_despacho.
- Manejadores: handlers_confirmaciones, handlers_transferencias, handlers_deshacer, handlers_suscripciones, handlers_menus, handlers_permitirse. Otros módulos: contexto, registro, propuestas, transferencias, metas, deshacer_corregir, confirmaciones, verificacion_texto_ia, marcas, parsers, detectors, db_lookups, enriquecedores, gastos, constantes.
- Todo movimiento se graba con transaccion_service.crear_transaccion(commit=False). Los mensajes salen solo por whatsapp_service.enviar_whatsapp.
- El texto que escribe la IA entra marcado como TextoIA y se verifica antes de mandarlo (verificar_texto_ia): si trae un número que no está en el mensaje del usuario, se reemplaza por un texto fijo.
- Lectura de comprobantes en PDF (fase4c2b1): descarga con control de tamaño de metadatos (máximo 10 MB, sin segunda llamada si excede), extracción de texto con pypdf (máximo 6 páginas, mínimo 100 caracteres sin espacios, límite 20.000 caracteres, descifrado con clave vacía). Extracción de movimientos y cuotas con Structured Outputs de OpenAI y verificación determinística contra montos y fechas del texto antes de integrarse al flujo habitual de propuesta.
- "¿Me lo puedo permitir?" usa tools_service.calcular_puede_permitirse, la misma función que la web.
- Gasto en dólares desde una billetera en pesos (fase_f2a_wpp_dolares_espejos, app/routers/whatsapp/dolares_desde_pesos.py): un gasto solo (no lote, no crédito) en dólares que sale de una billetera en pesos (la nombrada, o la principal o la única en pesos si no hay billeteras en dólares) se propone en pesos con dolar_service.sugerir_pesos_para_usd, la misma cuenta que la web (oficial del día del gasto + percepción). Antes de confirmar, el usuario puede mandar otro monto en pesos. Se guarda en pesos con monto_original y moneda_original en dólares. En un lote sigue el aviso de que no se pudo.

### Suscripciones
- Catálogo en app/core/catalogo_suscripciones.py y su .json (idéntico al del frontend). Un cobro automático se reconoce por suscripcion_id.

### Tasas
- app/core/entidades.py tiene el catálogo de entidades, con los mismos ids que el selector del frontend (src/lib/constants/banks.ts), y su fuente de tasa. Si se saca una entidad del selector, se saca también del catálogo; nunca se borran billeteras de usuarios por eso. app/services/tasas_service.py guarda en tasas_entidades lo que publica ArgentinaDatos (job a las 10:00 y a las 19:00). Una tasa con más de 7 días no se usa; la tasa manual de la billetera manda. El rendimiento estimado se calcula con el saldo de cada día.

### Memoria por comercio y duplicados
- En WhatsApp, la categoría se busca primero en la memoria por comercio del usuario (app/services/memoria_comercio_service.py, tabla memoria_comercios), después en marcas y al final en la IA. La memoria solo se guarda cuando el usuario responde que sí a "¿Siempre así?", y los movimientos anteriores solo cambian si el usuario lo confirma. Por WhatsApp, la pregunta llega después de confirmar una corrección de categoría.
- Los duplicados se buscan con app/services/duplicados_service.py: mismo monto y medio de pago, fechas a 10 días o menos y, si pasan más de 3 días, misma descripción.

### Facturas pendientes y marcado automático
- Las facturas ingresadas por foto o PDF viven en la tabla `facturas` (app/services/factura_service.py). Al registrar un egreso (que no sea cuota hija), se busca una factura pendiente con mismo monto, moneda, en ventana de fechas (llegada a vencimiento + 10 días) y coincidencia de subcategoría, categoría (distinta de "Otros") o clave de comercio. Si hay exactamente una candidata, se marca pagada automáticamente vinculando transaccion_id. Al eliminar la transacción, la factura vuelve a pendiente.
- Flujo WhatsApp para facturas con vencimiento (fase4c2b2b): Al recibir foto o PDF de factura con vencimiento, la propuesta pregunta '¿Ya la pagaste?'. Con 'sí' se anota el gasto del primer vencimiento y las cuotas restantes se guardan como facturas pendientes en la web. Con 'no' se anotan como facturas pendientes sin crear movimientos. Vencimientos en PDFs no provistos por la IA se extraen determinísticamente del texto si están en ventana válida.

### Patrones repetidos y decisiones de usuario
- La página "Lo que se repite" se apoya en app/services/patrones_service.py y la tabla `decisiones_patrones` (app/models/decision_patron.py). Agrupa gastos en fijos, costumbre y día a día (usando app/utils/patrones.py) e ingresos habituales (app/services/ingreso_habitual_service.py). El usuario puede confirmar, descartar o mover ítems entre cajas sin alterar las transacciones originales.

### Categorías (fase_categorias)
- **Árbol y catálogo**: 20 categorías activas (15 egreso, 5 ingreso), 68 subcategorías visibles y 2 de sistema (Banco > Tarjeta de crédito e Impuestos). Centralizado en app/core/catalogo_categorias.py (fuente única de verdad con ALIAS_NOMBRES y divisiones IPC).
- **Semilla con guardas**: scripts/seed_categorias.py inicializa el catálogo; si detecta categorías viejas sin migración previa (a5f6a7b8c9d0) aborta con log de advertencia. Categorías activas fuera de catálogo solo se archivan si ninguna fila en las 6 tablas las utiliza.
- **Rescate y vocabulario**: app/core/vocabulario_categorias.py rescata determinísticamente categorías/subcategorías por vocabulario local ante "Otros" de la IA.
- **WhatsApp y Personas**: aviso_personas_wpp agrega recordatorio contextual en una línea (<90 caracteres) ante Pagos a personas ("decime «era el súper»") o Cobros de personas ("decime «era un regalo»").
- **Otros = subcategoría vacía**: la subcategoría NULL se muestra como "Otros".
- **Regla de integridad**: cada vez que una subcategoría cambia de categoría, todas las filas que la usan en las 6 tablas cambian de categoría en la misma transacción.

### Dashboard y Disponible libre (fase_dash_ciclo)
- Las cards del dashboard web muestran lo que pasa en el ciclo actual del usuario (del inicio al fin del ciclo). "Disponible libre" resta exactamente los compromisos impagos de "Próximos pagos" sin recortar (app/services/pagos_proximos_service.py). El disponible es uno solo: pagos_proximos_service.calcular_disponible_libre. contexto_financiero_service._calcular_saldo_disponible_sync lo envuelve para WhatsApp, el contexto de la IA, tools y "¿Me lo puedo permitir?". dashboard_service.calcular_saldo_disponible_ciclo_actual no tiene llamadas (código muerto, se borra en el Bloque E).

### Presupuestos y metas (fase_ea_presupuestos)
- **Fuente única del usado**: `presupuesto_service.uso_periodo` y `presupuesto_service.uso_actual` son los únicos lectores del gasto de un presupuesto (usando `calcular_gasto_en_periodo(..., hoy=...)`). `UsoPeriodo` expone `calculado`, `mostrado` (recortado a 0 si el período tiene más reintegros que gastos, D-P8), `supera_limite` y `porcentaje`.
- **Columnas sin uso**: `periodos_presupuesto.monto_usado` y `superado` quedan sin lecturas ni escrituras (se eliminarán al reconciliar modelos). Nadie lee `monto_usado_actual`.
- **Un solo camino de aviso**: `verificar_alertas_presupuesto(db, presupuesto, periodo, usado, commit=True, enviar_whatsapp_ya=True)` centraliza las notificaciones de umbral 1 (`PRESUPUESTO_LIMITE`) y 100% (`PRESUPUESTO_AGOTADO`) con grupo `presupuestos/{id}/{periodo.id}/{tipo}`, tanto al registrar egresos (incluidas compras con crédito en 1 pago) como en `_job_notificaciones_presupuestos` (`enviar_whatsapp_ya=False`).
- **Período único**: restricción única `uq_periodos_presupuesto_presupuesto_inicio` sobre `(presupuesto_id, fecha_inicio)` (migración `a6b7c8d9e0f2`). Toda creación de período pasa por `asegurar_periodo` con `on_conflict_do_nothing`.
- **Metas**: `meta_service.recalcular_acumulado(db, meta)` es el único punto que escribe `Meta.monto_actual` como suma redondeada movimiento por movimiento (`ROUND_HALF_UP`) y actualiza el estado (`ACTIVA`/`COMPLETADA`, respetando `PAUSADA`). Toda meta nueva arranca en 0.

### Reglas únicas (fase_eb_reglas)
- **Pesificación con el vendedor del día del pago**: `pesificacion_service.cotizacion_pesificacion(db, fecha_pago, vencimiento)` usa el dólar oficial vendedor de `fecha_pago` (o hoy) topeado en `vencimiento`; la usan `pagar_resumen_tarjeta`, `simular_pesificacion` y `calcular_resumen_actual`.
- **Monto de cuota**: `app/utils/cuotas.py` (`monto_efectivo_cuota` y `monto_efectivo_cuota_sql`) es el único lector del monto de una cuota en `app/` (fuera de modelos, esquemas e importación).
- **Redondeo de cuotas**: `total_financiado` y `monto_base` se redondean a centavos con `ROUND_HALF_UP`, ajustando la diferencia en la última cuota.
- **Dashboard con una sola lista de pagos**: `get_dashboard_resumen` arma `proximos_pagos_completos` una sola vez y toma `disponible_real` del resultado de `calcular_disponible_libre`. Se eliminó `calcular_saldo_disponible_ciclo_actual`.
- **Excepts sin tragar en silencio**: en los servicios de plata, todo `except` lanza, registra con `logger` o lleva comentario `# Se ignora:`.
- **Gastos hormiga y calculadora**: `_gastos_hormiga` en `notificacion_scheduler_service` excluye espejos de reintegro (`reintegro_de_id` nulo) del conteo manteniendo el total neto; `calcular_conveniencia_cuotas` usa `tna_a_mensual` (`app/utils/tasas.py`) y aplica el umbral del 1% y el signo del saldo final invertido cuando hay billetera de inversión.

### Cuotas y tasas (fase_ec1_cuotas_tna)
- `grupos_cuotas.tasa_interes` es TNA en % (migración `e7f8a9b0c1d2`). La conversión a mensual se hace únicamente con `tna_a_mensual` (`app/utils/tasas.py`).
- La cuenta de cuotas (valor de cuota, total financiado, última cuota y amortización francesa) se realiza exclusivamente en `calcular_plan_cuotas` (`app/utils/cuotas.py`).
- Endpoint GET `/grupos-cuotas/simular` para la simulación unificada de cuotas requerida por el front.

### Reintegros (fase5c, 21/10/2026)
- Un reintegro recibido es una transacción espejo: EGRESO con monto NEGATIVO, misma categoría, subcategoría, billetera, medio de pago y tarjeta que la compra, fecha = día en que llegó la plata, reintegro_de_id = la compra (app/services/reintegro_service.py). Sin crédito mueve el saldo con aplicar_impacto_saldo; con crédito crea padre, grupo de 1 cuota y cuota hija negativa en el primer resumen sin pago confirmado desde esa fecha.
- Pendiente = reintegro_esperado sin espejo: no suma en ningún número. Aviso web a los 30 días (notificar_reintegros_pendientes, dentro de _job_vencimientos_tarjetas).
- El reintegro cuenta en el mes en que llega (como YNAB): si llega en otro período que la compra, ese período puede quedar con usado negativo en un presupuesto.
- Los espejos no cuentan para patrones ni para el clasificador. Desde fase_f1_devoluciones cada espejo es su propia fila en el historial y en Últimos movimientos, y la compra muestra su monto completo. tipo_espejo separa "reintegro" (reintegro o devolución de la compra, por el mismo medio de pago; en cuotas, crédito en un pago y las cuotas siguen) de "parte" (¿Qué es esta plata? → Me devolvieron parte de un gasto: la plata que entró pasa a ser un espejo del gasto en la billetera donde entró). Borrar la compra borra sus espejos "reintegro" y devuelve los "parte" a ingreso. Por WhatsApp, deshacer una compra con espejos lo avisa en la propuesta y borra todo con eliminar_transaccion; un espejo no se corrige por WhatsApp; en las consultas de gastos los espejos restan pero no cuentan como movimientos. Por WhatsApp (fase_f2b_wpp_reintegros, app/routers/whatsapp/reintegro_wpp.py), «me devolvieron 25000 del súper» o «me llegó el reintegro del súper» busca la compra en pesos por descripción o subcategoría con los filtros de gastos_para_vincular: con una sola la registra directo con registrar_o_actualizar_reintegro; con varias pregunta cuál con una lista numerada; sin monto usa el reintegro esperado; una compra con reintegro ya recibido no se ofrece. «Borrá eso» la deja como estaba (pendiente si estaba pendiente).

### ¿Qué es esta plata? y Pasó por mi cuenta (fase_f1_devoluciones)
- `que_es_plata_service` centraliza la gestión de devoluciones vinculadas a gastos (`gastos_para_vincular`, `responder`, `revertir_parte`).
- Dos rutas en `/transacciones`: `GET /gastos-para-vincular` y `POST /{transaccion_id}/que-es-esta-plata` (con idempotencia).
- La subcategoría "Pasó por mi cuenta" (en categorías "Otros" de egreso y de ingreso) no es gasto ni ingreso y la excluye exclusivamente `definiciones_service`.
- Por WhatsApp, cuando un mensaje registra un solo cobro en Cobros de personas, el aviso de una línea es «¿Qué era? 1) Me pagaron 2) Me devolvieron parte de un gasto 3) Solo pasó por mi cuenta» y la respuesta (app/routers/whatsapp/que_es_plata_wpp.py, paso 2.05 de etapa_handlers) llama a que_es_plata_service.responder; el 2 lista hasta 5 gastos de gastos_para_vincular.

### WhatsApp: costo por mensaje (desde el 01/10/2026)
- Meta cobra cada mensaje que manda el bot dentro de la ventana de 24 horas (1.000 entregados gratis por número por mes, sin acumular). Cada mensaje que llega produce como máximo un mensaje saliente (fase_c_wpp, 22/10/2026): el orquestador abre whatsapp_service.buzon_de_respuesta y todo lo que se manda con enviar_whatsapp a ese número sale junto en un solo mensaje (partes unidas con una línea en blanco). Un aviso extra se agrega con buzon_actual().agregar_aviso (una línea de 90 caracteres o menos, al final; el recordatorio del resumen impago va así, desde recordatorio_impago_wpp.agregar_recordatorio_impago). Nunca mandar avisos como mensaje aparte ni llamar a Meta fuera de whatsapp_service._post_meta.
- Reintentos: solo ante errores de conexión (ConnectError, ConnectTimeout, PoolTimeout); nunca ante timeout de lectura ni 5xx. Cada saliente queda en la tabla mensajes_salientes_wpp (sin texto ni teléfono en claro) con lo que Meta dice que cobró (eventos de estado, pricing.type); tope de 150 salientes por teléfono por día, salvo alertas de seguridad. El Monitor WPP|AI del admin lo muestra en la tarjeta "Mensajes del bot". El aviso de ráfaga y los textos de tope de la IA salen una sola vez por ventana (rate_limit_service.avisar_una_vez).
- En la suite, run_isolated parchea whatsapp_service._enviar_registrado (no enviar_whatsapp) y controla "Envíos por mensaje entrante: máximo 1". comparar_salidas.py --unir-mensajes compara las respuestas unidas.

### Base de datos
- Las migraciones se aplican a mano a producción antes del push; Railway corre "alembic upgrade head" al deployar.

### PC, copia local y producción (fase_v3_entorno_local)
- **Modo PC (app/core/entorno.py)**: Fuera de Railway, Settings rechaza bases que no sean locales salvo ARGENTUM_PRODUCCION_CONFIRMADA=1; apaga envíos reales (WhatsApp, Resend, SMTP) y tareas automáticas (ENABLE_SCHEDULER=False); fija ES_MODO_PC=True.
- **Secretos y producción**: La URL de producción vive en D:\Argentum\secretos\produccion.env (separar_produccion.py); solo scripts/local/produccion.py la lee.
- **Acceso a producción**: Solo con scripts/local/con_produccion.py [--solo-lectura] <cmd>, interactivo desde terminal con confirmación explícita y registro en registro_produccion.log.
- **Opciones de conexión**: opciones_conexion aplica -csearch_path=public y, con ARGENTUM_PRODUCCION_SOLO_LECTURA=1, -cdefault_transaction_read_only=on.
- **Refresco y migraciones**: refrescar_base_local.py vuelca a D:\Argentum\base_local\dumps\ y corre alembic upgrade head en la copia local. Las migraciones pendientes en producción las aplica el usuario con con_produccion.py antes del push.
- **Base local en D**: scripts/local/rutas.py centraliza base_local_dir (D:\Argentum\base_local), pgsql_dir, data_dir, log_pg, pg_local_env y dumps_dir.

### Ya no existen (no los busques ni los vuelvas a crear)
- analisis_financiero_service.py, el handlers.py de WhatsApp, el módulo de transacciones recurrentes, la columna es_recurrente, la rama de transacción pendiente de IA en WhatsApp.
- El cálculo de gasto inusual (se rehace en la Fase 7). Siguen sin uso el tipo de notificación GASTO_INUSUAL y los campos gasto_inusual_* de la configuración.

### Pruebas
- Tests que cuidan reglas (nunca se les agregan excepciones): test_atomicidad_operaciones, test_cobros_suscripciones_aislados, test_formato_montos, test_whatsapp_motor_unico, test_verificacion_texto_ia, test_permitirse_whatsapp, test_herramienta_paso, test_ajustes_saldo, test_whatsapp_webhook_http.
- Después de separar_produccion.py, el .env apunta a la copia local. Producción va solo con scripts/local/con_produccion.py y las migraciones nuevas se aplican solo en la copia local. Suite, foto, personas y verificador se corren sobre la copia local con scripts/local/con_base_local.py <comando> o con scripts/local/verificar_todo.py. Cada paso de trabajo empieza y termina con scripts/local/paso.py inicio|cierre <paso>.
- Carpeta de auditorías (crudos, copias, fotos): D:\Argentum\auditorias, desde el paso fase_a_rutas (la ruta sale de scripts/local/rutas.py y de la variable ARGENTUM_AUDITORIAS_DIR). Nunca C:\...\GitHub\auditorias.
- CI (.github/workflows/ci.yml): carga el esquema real de producción en a3d4e5f6a7b8 (scripts/ci/esquema_a3d4e5f6a7b8.sql, sin datos; scripts/ci/cargar_esquema.py solo corre contra argentum_ci o argentum_ci_prueba en localhost), hace stamp a3d4e5f6a7b8, upgrade head, downgrade -1, upgrade head y corre pytest con tests/pg (ARGENTUM_PG_TESTS_CI=1). alembic check queda afuera a propósito: hay diferencias conocidas entre los modelos y producción (tabla analisis_ia, columna transacciones.es_reintegro, varios índices) que se arreglan en un paso propio.
- Pruebas de saldo en PostgreSQL: tests/pg (corren con con_base_local.py o en la CI; usan la base aparte argentum_pruebas_saldo del mismo servidor y nunca argentum_local ni argentum_ci). Importar tests/pg/conftest.py con una URL de PostgreSQL que no sea la local ni la de la CI da RuntimeError: nunca se importan tests desde scripts propios. TD.7 es el generador de invariantes con oráculo (ARGENTUM_INVARIANTES_COMPLETO=1 para la corrida larga).
- Suite de WhatsApp: scripts/regresion/suite_regresion_whatsapp.py (escenarios en scripts/regresion/suite/). Las grabaciones de la IA en scripts/regresion/grabaciones_ia/ nunca se editan a mano. Comparación de salidas: scripts/regresion/comparar_salidas.py. Paridad web/WhatsApp: scripts/regresion/paridad_web_whatsapp.py.
- Foto del motor: scripts/motor/foto_motor.py. Personas: scripts/motor/evaluar_personas.py. Verificador: scripts/testingadmin/verificar_testingadmin.py.
- Ningún archivo de más de 1.300 líneas. Nunca se juntan líneas para cumplirlo: si un archivo se pasa, se parte en archivos.

### Protección de las pruebas (fase_h1_libre_fin_mes)
- pytest solo corre contra SQLite, la copia local (localhost:5433/argentum_local) o la CI (localhost:5432/argentum_ci con ARGENTUM_PG_TESTS_CI=1), nunca con ARGENTUM_PRODUCCION_CONFIRMADA ni en Railway; en las pruebas no se inicializa la base ni se precargan feriados (INICIALIZAR_BASE_AL_ARRANCAR=false) y el scheduler está apagado.

### ¿Cómo llegás a fin de mes? (fase_h2a_tarjeta_fin_de_mes)
- Un solo número de plata libre en el inicio (el Disponible libre); el rango se calcula al fin del ciclo sumando los cobros regulares que caen dentro del ciclo; la tarjeta aparece solo con estado "completo"; la línea "Hasta tu cobro..." solo si el cobro cae dentro del ciclo; la línea del saldo del ciclo va en la tarjeta de saldo; WhatsApp usa la misma función y sin rango contesta con el libre de hoy.
### Pesificación y transferencias (fase_ec2_front_sin_cuentas)
- `calcular_montos_pesificacion` única en `pago_resumen_service.py`.
- `calcular_monto_destino` única en `transferencia_service.py` y `GET /transferencias/simular`.
- `GET /transacciones/sugerencia-pesos` devuelve `cotizacion_tarjeta`.
- Todo redondeo financiero en centavos con ROUND_HALF_UP.

### Medición de uso y Descargar mis datos (fase_g1_uso_datos)
- `eventos_uso`: tabla propia para registrar solo cuatro eventos de la web (visita, permitirse, calculadora_cuotas, descargar_datos), como máximo uno por usuario, evento y día. El uso por WhatsApp no lleva eventos y sale directamente de conversaciones_wpp y transacciones.
- Reporte semanal (`reporte_uso_service`): se ejecuta cada lunes a las 09:07 con lock advisory; calcula altas, activación en 24 horas, retención a 7 y 30 días, costo de IA y de WhatsApp Meta por usuario activo, uso de funciones y transacciones que caen en Otros. Sin datos personales salvo descripciones de usuarios con `permiso_lectura_mensajes_en` activo. Se envía a `REPORTE_USO_EMAILS` o `ADMIN_ALERT_EMAIL`.
- Purga de datos: el job semanal purga eventos de `eventos_uso` con más de 400 días de antigüedad (`dia < hoy - 400`).
- `GET /usuarios/me/exportar`: descarga de portabilidad ("Descargar mis datos") con rate limit de 3/minuto. Devuelve un ZIP con nueve planillas CSV delimitadas por ";" con BOM (utf-8-sig), coma decimal, números negativos con signo "-" y fin de línea CRLF.

### Datos viejos, contrato de número y pregunta de saldo (fase_g2_saldo_confianza)
- Frescura de datos (`frescura_service`): mide si los datos están viejos usando los días con carga (transacciones manuales/WhatsApp, transferencias internas y ajustes de saldo) en una ventana de 180 días. Umbral adaptativo: mediana + 3 desvíos absolutos medianos (MAD, Leys et al. 2013), con piso de 7 días (menos de 5 intervalos devuelve 7). Condición de datos viejos: estricto (`dias_sin_cargar > umbral`). Los números nunca se ocultan ni se bloquean.
- WhatsApp: aviso breve de una línea al pie de la respuesta a `consultar_saldo` (`linea_frescura_wpp`), exactamente 90 caracteres o menos, sin signos de pregunta, solo si hay datos viejos.
- Contrato del Disponible libre (`contrato_numero_service`): declara valor, base (ciclo), fecha de corte, cobertura (mínima entre billeteras activas no de inversión) y supuestos desde una sola fuente (`contrato_disponible_libre`). Reutiliza el Disponible libre ya calculado por `get_dashboard_resumen` sin recalcularlo ni consultar próximos pagos.
- Pregunta de saldo (`pregunta_saldo_service`): selecciona una billetera activa no de inversión con 30 días o más sin control (`(hoy - ref).days >= 30`), priorizando mayor uso en los últimos 90 días (mínimo 1 movimiento). "Saltear" pospone la pregunta por 30 días (`pregunta_saldo_pospuesta_hasta = hoy + 30`). Si la diferencia es chica, sin duplicados ni rendimiento (`guardar_directo`), el dashboard guarda directamente sin abrir modal.

### Excel de presupuestos (fase_excel_presupuestos2)
- **Ruta y alcance**: `GET /presupuestos/exportar-excel` con rate limit de 3/minuto. Parámetro `alcance` ("12" para últimos 12 meses o "todo" para historial completo hasta 120 ciclos). Devuelve un libro `.xlsx` con Content-Disposition `attachment; filename="Argentum_presupuestos_<inicio>_<fin>.xlsx"`.
- **Ciclo con datos**: Un ciclo candidato entra al Excel únicamente si tiene al menos una transacción que cumpla `condicion_gasto` o `condicion_ingreso` dentro de las fechas del ciclo (hasta `hoy`). Si ningún ciclo tiene datos devuelve 404 con detail exacto.
- **Nombre y orden de hojas**: Hoja 1 "Comparativo" (color de pestaña 1F3864, hoja activa), Hoja 2 "Anual" (color 2E75B6), seguidas por una hoja por cada ciclo con datos del más viejo al más nuevo, y Hoja final "Cómo leerlo" (color 595959). El nombre de cada hoja de ciclo corresponde al mes que más días abarca el ciclo (en empate, el mes de la fecha de fin; desambiguación con " (2)", " (3)").
- **Diseño limpio y sin artefactos**: Sin `freeze_panes`, sin `auto_filter` y sin cuadrículas explícitas de bordes finos en todas las celdas. Tipografía uniforme Calibri, bordes sutiles solo para jerarquía (separadores, totales), anchos de columna automáticos con holgura.
- **Hoja de ciclo rediseñada**:
  - Ya NO existe la regla X-6 (se eliminó la hoja y sección "Otros presupuestos").
  - "Tus presupuestos": un presupuesto por fila, sin repetir nombre, ordenados cronológicamente por inicio y nombre. Columnas: Presupuesto, Período, Asignado, Gastado, Te queda y % usado.
  - "En qué gastaste": categorías ordenadas de mayor a menor gasto con comparativo contra mes anterior (Mes anterior, Este mes, % del total con `=IF($B$T=0,"",B{r}/$B$T)`, Variación con flecha ▲/▼/—).
  - Subcategorías indentadas con tres espacios bajo cada categoría que tenga desglose.
  - Sección USD separada al pie si existen gastos en dólares.
- **Comparativo**: Incluye tabla "Presupuestos de cobro a cobro" antes del desglose de categorías, con semáforo de escala de color según % de uso y columna de conteo `Meses que te pasaste` con `=COUNTIF(... , ">1")`.
- **Separación de monedas**: Montos en ARS y USD van estrictamente en tablas y bloques separados en todas las hojas.
- **Solo lectura**: La generación del Excel nunca escribe en la base de datos, nunca crea ni renueva períodos de presupuesto y nunca ejecuta commit ni flush.
- **Estructura modular (< 1.300 líneas)**: Servicio dividido en 4 archivos para respetar el límite de líneas:
  - `app/services/presupuestos_excel_datos.py`: Dataclasses inmutables y extracción de datos desde BD.
  - `app/services/presupuestos_excel_libro.py`: Orquestador principal del libro openpyxl, paleta de colores y estilos compartidos.
  - `app/services/presupuestos_excel_hojas.py`: Generación de hojas Comparativo, Anual y Cómo leerlo.
  - `app/services/presupuestos_excel_hoja_ciclo.py`: Generación detallada de cada hoja de ciclo.
- **Métricas de uso**: La descarga registra el evento `"descargar_presupuestos"` en `eventos_uso`, reportado en `reporte_uso_service`.

### Lista de movimientos y usuario demo (fase_lista_movimientos_demo)
- **Lista de movimientos pasada**: La lista principal (`listar_movimientos`), el detalle de billetera y "Últimos movimientos" del dashboard muestran únicamente eventos pasados (`fecha <= ref_hoy`). Los eventos con fecha futura se separan en el bloque `proximos` (primeros 50 en orden cronológico ascendente) y `proximos_total` para la cuenta total sin paginar.
- **Sin chips ni carteles en filas**: Se eliminaron todos los carteles/chips ("Por confirmar", "Pendiente IA", "Cuota N/M", débito automático, etiqueta redundante). Cada fila muestra a lo sumo 2 íconos pequeños junto al monto según la función unificada `iconos_movimiento`: `reintegro`, `suscripcion`, `credito`, `paso_cuenta`, `devolucion_gasto`, `devolucion_compra`, `cuota`.
- **Títulos consistentes sin prefijos redundantes**: Títulos calculados por `titulo_movimiento`: las transferencias internas reflejan la contraparte según el contexto de la billetera ("Hacia X" / "Desde X"), los aportes/retiros a metas indican "Aporte a meta" / "Retiro de meta" sin prefijos duplicados, y los reintegros muestran el detalle limpio.
- **Usuario Demo (`scripts.demo`)**: Creación determinística del usuario `demo.argentum@argentum.com` (Lucía Demo, diseñadora en CABA) con datos completos desde el 25/05/2026 hasta el 21/08/2027 a través de las funciones de servicio reales de la aplicación.
- **Comandos del script**: Soporta `--crear` (con contraseña por `DEMO_PASSWORD` o interactiva), `--ponerse-al-dia` (idempotente para vencimientos ocurridos) y `--borrar` (requiere confirmación explícita con el email exacto).
- **Aislamiento y visualización**: La cuenta `testingadmin` es exclusivamente para auditoría interna y tests del motor, nunca para demostraciones ni visualización pública de producto. Todas las operaciones del usuario demo están estrictamente aisladas de otras cuentas.

### Consejos (fase_i1_consejos)
- **Tablas y modelos**: `consejos_emitidos`, `consejos_silenciados` y columna `usuarios.prioridades_consejos` (migración `e3f4a5b6c7d8`). Modelos `ConsejoEmitido` y `ConsejoSilenciado` en `app/models/consejo.py`.
- **Reglas puras**: `app/services/consejos_reglas.py` con constantes, dataclasses inmutables y evaluación de 7 consejos de texto fijo calculados por el sistema (C1 hormiga, C2 plata quieta, C3 colchón, C4 comisiones, C5 pico, C14 sin rubro, C15 sin ingresos), ordenamiento por prioridades y elección única (máximo 1/día, 2/semana, espera 30/60 días, vence a los 7 días).
- **Recolección solo lectura**: `app/services/consejos_datos.py::recolectar` obtiene datos con `calcular_fin_de_mes`, ciclos cerrados, transacciones y cotizaciones sin commit, flush ni red.
- **Servicio y ciclo de vida**: `app/services/consejos_service.py` gestiona `consejo_activo`, `emitir_si_corresponde`, `responder` (sirvio, no_sirve, no_avisar), `marcar_vencidos` y `medir_pendientes` (tras 2 ciclos cerrados).
- **Job diario**: `_job_consejos_diarios` a las 08:40 ART (`job_id="consejos_diarios"`), registrado en `JOBS_CATALOGO` y `JOBS_MANUALES_PERMITIDOS`. La emisión corre exclusivamente por el job.
- **API**: `GET /consejos/actual` (solo lectura de lo guardado), `POST /consejos/{id}/respuesta` y `PUT /usuarios/me/prioridades-consejos` (hasta 3 prioridades de 1 a 8). `UsuarioResponse` expone `prioridades_consejos`. Ningún consejo sale por WhatsApp.

### Presupuestos inteligentes (fase_i2a_presupuestos)
- **Funciones puras y constantes (`app/services/presupuesto_sugerencias_reglas.py`)**: `SUBCATEGORIAS_FIJAS` (18 subcategorías fijas no proyectables), `proyectar_ritmo` (proyección lineal a fin de período con corte a partir del 30% transcurrido y sólo si no superó el límite), `linea_ritmo` y `sugerir_limite` (escalón mensual de aumento de pago único o superación en los últimos 3 períodos consecutivos).
- **Lecturas compartidas (`app/services/gasto_filas.py`)**: `filas_gasto`, `ciclos_cerrados` e `indices_ipc` extraídas para uso común entre consejos y presupuestos sin commit, flush ni llamadas de red.
- **Consultas de ritmo y sugerencia (`app/services/presupuesto_sugerencias.py`)**: `ritmo_presupuesto`, `sugerencia_limite`, `ritmos_activos`, `categorias_con_presupuesto`, `ids_categorias_egreso` y `rubro_sin_presupuesto`.
- **Integración con motor de consejos (`app/services/consejos_reglas.py`)**: Consejos C6 (`ritmo`) y C7 (`sin_presupuesto`) incorporados a `evaluar_todos` y `medir_pendientes`, junto con `elegir_rubro_sin_presupuesto` (mediana de 3 ciclos >= 5% del gasto típico).
- **Actualización de presupuestos (`app/services/presupuesto_service.py`)**: `calcular_gasto_en_periodo` soporta `condicion_extra`, extracción de `condiciones_categorias` y `contar_compras_en_periodo`. En `renovar_presupuestos` se eliminó la notificación obsoleta de sugerencia.
- **API (`app/routers/presupuestos.py`, `app/schemas/presupuesto.py`)**: `PresupuestoResponse` incluye `ritmo_texto` y `sugerencia_limite`. Endpoint `GET /presupuestos/sugerencia-rubro` para detectar rubros frecuentes sin presupuesto asignado.

### Consejos C8 a C13 (fase_i2b_consejos)
- **Funciones puras y estructuras (`app/services/consejos_extra.py`)**: Dataclasses `SaldoArrastradoDato`, `ResumenUsdDato`, `IngresoDato`, `MetaDato`, `DatosExtra`. Seis nuevos consejos calculados por el sistema: C8 carga (cuotas que pesan), C9 extra (aguinaldo, bono o ingreso grande), C10 meta (atrasada y sin fecha), C11 subió (servicio con aumento superior a inflación), C12 arrastre (saldo financiado impago) y C13 dólares (ahorro de percepción abonando con dólares propios). Orquestador puro `evaluar_candidatos`.
- **Recolección en solo lectura (`app/services/consejos_extra_datos.py`)**: `recolectar_extra` obtiene saldos arrastrados activos, resúmenes en moneda extranjera, dólares propios disponibles, cuotas futuras, ingresos habituales y metas sin commit, flush ni llamadas de red.
- **Consejo cumplido (`app/services/consejos_service.py`, `app/routers/consejos.py`)**: `consejo_cumplido` detecta automáticamente si el usuario realizó la sugerencia (creó el presupuesto, aportó a la meta, asignó fecha, saldó la tarjeta, registró el ingreso o aumentó inversiones). `resolver_cumplidos` marca respuesta "resuelto" en `emitir_si_corresponde` y `GET /consejos/actual`, haciendo desaparecer la tarjeta sin recalcular consejos. Acciones en front 'aportar_meta' y 'poner_fecha_meta' navegan a metas.

### Avisos del Bloque I (fase_i3_avisos)
- **Aviso de fin de mes (Decisión 6A, `app/services/aviso_fin_de_mes_service.py`)**: Si el disponible libre proyectado no cubre los compromisos que vencen en el ciclo, la persona recibe un aviso por ciclo y por moneda (`PROYECCION_NEGATIVA`). Se entrega en la campana web y como última línea en WhatsApp (`app/routers/whatsapp/aviso_fin_de_mes_wpp.py`), evaluado con los datos de hoy. El job diario `_job_consejos_diarios` ejecuta `avisar_fin_de_mes`.
- **Condición de aviso con saldo y compromisos**: El aviso de fin de mes sale solo si la persona tiene saldo cargado (billetera activa no de inversión con punto de partida o control de saldo) y tiene compromisos que vencen en el ciclo en esa moneda.
- **D11 en "¿Me lo puedo permitir?" (`app/services/permitirse_textos.py`, `CanAffordResult`)**: Si el resultado es "justo" o "ahorros", suma la línea "Si no es urgente, esperá 24 horas y volvé a fijarte." tanto en web como en WhatsApp.
- **Línea de consejos en el reporte semanal (`app/services/reporte_uso_service.py`)**: Mide consejos mostrados, respuestas recibidas, vencidos y medidos (con mejora) sin datos personales.
- **Hormiga única (`app/services/consejos_reglas.py`, `notificacion_scheduler_service.py`)**: El resumen de cierre de ciclo utiliza `grupos_hormiga` unificando la regla de detección con el consejo C1 (20 compras o más, mediana 90 días, >=4 compras por subcategoría, <=50% de la mediana 90d, >=5% del gasto).

### Costos y jobs (fase_j1_costos_jobs)
- **Topes del mes (`app/services/costos_service.py`, `app/core/config.py`)**: Topes mensuales estimados de OpenAI (`IA_PRESUPUESTO_MENSUAL_USD` = 10.0 USD) y Meta (`META_TOPE_MENSUAL_ARS` = 5000 ARS, `WHATSAPP_TARIFA_SERVICIO_ARS` = 37.68 ARS). Al 50%, 80% y 100% envía un email a los administradores (`destinatarios_reporte`). En OpenAI, al 100% el bot de WhatsApp deja de usar la IA hasta el 1° del mes siguiente respondiendo `TEXTO_MES_SIN_IA` mientras la web sigue activa. En Meta el tope es solo un aviso y nunca corta WhatsApp. La función `exento_tope_mensual` queda preparada para el plan pago (hoy siempre `False`).
- **Regla de jobs (R1, R2 y R3)**: Un job que falla nunca debe esconder su error atrapando todo en un except silencioso:
  - R1: El except exterior de cada job conserva el registro y alerta, pero suma `raise` al final para que APScheduler anote "error" y el vigilante alerte.
  - R2: Los jobs que recorren personas o presupuestos continúan con los siguientes si uno falla, pero al final lanzan `RuntimeError(f"{NOMBRE}: {fallados} de {total} con error")`.
  - R3: En `_job_actualizar_ipc`, si datos.gob.ar no responde o devuelve dato estimado, lanza `RuntimeError`.
- **IP real y anonimización (`app/core/ip_cliente.py`)**: Se utiliza `ip_cliente(request)` para leer el encabezado `X-Real-IP` inyectado por Railway en su borde (o `request.client.host` en PC/tests). Nunca se usa `request.client` directo. La clave de slowapi se genera con `clave_limite(request)` mediante un HMAC-SHA256 truncado a 32 caracteres, asegurando que la tabla `rate_limits` nunca guarde direcciones IP en claro.

### Cuentas y claves (fase_j2_auth)
- **Registro sin revelar cuentas (Decisión 2A, `app/routers/auth.py`, `src/pages/auth/register/RegisterPage.tsx`)**: `POST /auth/register` devuelve siempre 201 con `AuthResponse(usuario=None, requiere_verificacion_email=True, requiere_verificacion_telefono=False)`. Si ya existía una cuenta activa o inactiva, se envía el correo `template_cuenta_existente` ("Ya tenés una cuenta en Argentum") sin crear otra ni revelar la existencia al solicitante. Si la cuenta existía pero en estado `PENDIENTE_VERIFICACION` con email no verificado, se actualizan nombre, apellido y contraseña, enviando un código nuevo de activación. El frontend navega directamente a `/auth/verificar-email` pasando `emailParaVerificar(email)`.
- **Login sin revelar existencia de cuentas (`app/routers/auth.py`)**: Devuelve un 401 unificado (`BODY_401`: "El email o la contraseña no son correctos. Revisalos e intentá de nuevo.") ante email inexistente, contraseña errónea o cuentas creadas con Google sin contraseña, utilizando `_hash_relleno()` para igualar los tiempos de cómputo y evitar ataques de enumeración y timing. El control de cuenta bloqueada (429) y el 401 de `EMAIL_NO_VERIFICADO` se evalúan únicamente tras comprobar que la contraseña provista es correcta.
- **PyJWT como único motor de tokens (`app/core/auth.py`, `app/services/auth_service.py`)**: Eliminado `python-jose` en todo el backend y reemplazado por `PyJWT[crypto]==2.13.0`. `decodificar_access_token` centraliza la lectura y verificación de access tokens en la aplicación.
- **Tokens fuera de URLs (`app/services/auth_service.py`, `app/routers/notificaciones.py`)**: Eliminado el fallback al endpoint `tokeninfo` de Google; la validación de Google ID tokens se realiza estrictamente offline mediante firmas criptográficas RS256 con claves públicas JWKS (respetando `_REFETCH_MIN_SEGUNDOS = 300`). El endpoint SSE `/notificaciones/sse` autentica de forma exclusiva mediante la cookie httpOnly `access_token` (`autenticar_sse`), eliminando tokens de query parameters o headers.
- **Separación de claves criptográficas (`app/core/config.py`, `app/core/claves.py`)**: `SECRET_KEY` queda reservada exclusivamente para la firma y verificación de tokens JWT. `HMAC_SECRET_KEY` se emplea para resúmenes HMAC (refresh tokens, códigos de verificación y recuperación, anonimización de IP y números de WhatsApp). Período de transición hasta el 10/12/2026 en `claves_hmac_verificacion` que acepta resúmenes generados con `SECRET_KEY`.
- **Impacto de rotación de claves**:
  - Rotar `SECRET_KEY`: invalida los access tokens vigentes cerrando todas las sesiones activas en un máximo de 15 minutos (tiempo de vida del token).
  - Rotar `HMAC_SECRET_KEY` (sin período de transición): invalida inmediatamente todos los refresh tokens y códigos de verificación/recuperación vigentes, y reinicia los contadores de rate limiting por IP y del límite diario de WhatsApp por número.
- **Medidas previas consolidadas**: Códigos de verificación y recupero generados con `secrets.randbelow` y almacenados como HMAC-SHA256 (`test_12`); `create_engine` con `hide_parameters=True` (`test_14`); rate limit estricto de 10 peticiones/minuto en `/auth/google`; logs de Google sin email ni nombre del usuario (`test_15`); cookie de refresh token limitada al path `/auth`.

### Análisis (fase_n_analisis y fase_n2_analisis)
- Ciclos: solo los del usuario, con analisis_ciclos_service.generar_ventana_ciclos sobre dashboard_service.get_ciclo_fechas. Ningún otro lugar de Análisis arma ciclos.
- Sumas: las mismas que el inicio (definiciones_service.condicion_gasto y condicion_ingreso); los reintegros y devoluciones restan. Cada ciclo da igual que calcular_balance_ciclo.
- Ciclo en curso: se compara contra los mismos días del ciclo anterior; promedios y porcentaje de lo que te quedó, solo con ciclos completos; porcentaje null sin ingresos o por debajo de −100 %.
- Dólares: cada movimiento se pasa con el dólar oficial vendedor de su fecha (dolar_service.obtener_cotizaciones_por_fechas); sin cotización no se suma y se avisa. Nunca un valor fijo.
- Informes guardados: tabla informes_personalizados con migración (b6c7d8e9f0a1); todo se filtra por usuario_id.
- Archivos: analisis_ciclos_service, analisis_resumen_service, analisis_insights_service, analisis_comparativa_service, analisis_proyeccion_service, analisis_informes_service y analisis_excel_service. Sin re-exports ni imports adentro de funciones.
### Textos sin tecnicismos (12/11/2026)
- Ningún texto que ve el usuario usa jerga técnica ni financiera (KPI, delta, REM, drill-down, tasa, etc.): todo en palabras simples. En Análisis lo controlan tests/test_analisis_n.py (TN16) y src/pages/app/analisis/sinTecnicismos.test.ts del front.
