# Argentum backend

## Cómo responderle a Sebastián (regla n.º 1, 26/10/2026)
Sin explicaciones y sin vueltas. Solo: indicaciones, prompts, pushear o no, comandos completos y decisiones de negocio, siempre en pasos simples. Nada de causas técnicas, diagnósticos ni hallazgos.

## Estructura vigente (actualizada el 21/10/2026)

Esta sección manda: si otra parte de este archivo dice algo distinto, vale lo que dice acá.
Plan vigente (único): Project de Claude.ai, claude/PLAN_VIGENTE_21-10.md (versión 3), con claude/DIAGNOSTICO_21-10.md, claude/DECISIONES_21-10.md y claude/MATRIZ_PRUEBAS_21-10.md. Ante una contradicción entre este archivo y el plan, vale el plan.

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
- definiciones_service, ingreso_habitual_service, compromisos_service, conciliacion_service, datos_motor_service, perfil_financiero_service, calibracion_service, proyeccion_service, resumen_tarjeta_service, pago_resumen_service, tarjeta_service.

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

### Reintegros (fase5c, 21/10/2026)
- Un reintegro recibido es una transacción espejo: EGRESO con monto NEGATIVO, misma categoría, subcategoría, billetera, medio de pago y tarjeta que la compra, fecha = día en que llegó la plata, reintegro_de_id = la compra (app/services/reintegro_service.py). Sin crédito mueve el saldo con aplicar_impacto_saldo; con crédito crea padre, grupo de 1 cuota y cuota hija negativa en el primer resumen sin pago confirmado desde esa fecha.
- Pendiente = reintegro_esperado sin espejo: no suma en ningún número. Aviso web a los 30 días (notificar_reintegros_pendientes, dentro de _job_vencimientos_tarjetas).
- El reintegro cuenta en el mes en que llega (como YNAB): si llega en otro período que la compra, ese período puede quedar con usado negativo en un presupuesto.
- Los espejos no cuentan para patrones ni para el clasificador; el historial junta compra y espejo cuando están en el mismo rango. Borrar la compra borra sus espejos. WhatsApp todavía no maneja reintegros.

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