# Argentum backend

## Estructura vigente (actualizada el 02/10/2026; Fase 4 en curso)

Esta sección manda: si otra parte de este archivo dice algo distinto, vale lo que dice acá.

### Reglas del motor
- Qué es gasto y qué es ingreso lo decide solo app/services/definiciones_service.py. Ningún otro archivo filtra gastos o ingresos por su cuenta.
- El saldo oficial de una billetera es conciliacion_service.calcular_saldo_teorico (incluye rendimientos y ajustes de saldo). Un movimiento mueve el saldo solo si está confirmado, no es de crédito y su fecha es hoy o anterior (transaccion_service._afecta_saldo).
- Los ajustes de "Actualizar saldo" (solo web) viven en la tabla ajustes_saldo (app/services/ajuste_saldo_service.py): mueven el saldo y nunca son gasto ni ingreso. Cada control queda guardado, aunque no haya diferencia, y su historia mide la cobertura.
- Las billeteras de inversión (es_inversion) quedan fuera de todo número: lo cargado en ellas no es gasto ni ingreso.
- Las cuentas las hace siempre el sistema; la IA solo entiende y redacta.
- "Hoy" se calcula con app/utils/fecha.py (hoy_argentina). Todo monto que ve el usuario pasa por app/utils/formato.py (formatear_monto).

### Servicios del motor (app/services/)
- definiciones_service, ingreso_habitual_service, compromisos_service, conciliacion_service, datos_motor_service, perfil_financiero_service, calibracion_service, proyeccion_service, resumen_tarjeta_service, pago_resumen_service, tarjeta_service.

### Escrituras
- transaccion_service (crear_transaccion, actualizar, eliminar, confirmar_transaccion_ia), transferencia_service, meta_service, pago_resumen_service, cuotas_service, rendimiento_billetera_service, ajuste_saldo_service.
- Toda operación que escribe recibe commit: bool = True. Con commit=False hace flush y nunca commit. Una operación que llama a otra le pasa commit=False. Los recálculos derivados (perfil) van después del commit y no pueden deshacer la operación.

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

### Dashboard y Disponible libre (fase_dash_ciclo)
- Las cards del dashboard web muestran lo que pasa en el ciclo actual del usuario (del inicio al fin del ciclo). "Disponible libre" resta exactamente los compromisos impagos de "Próximos pagos" sin recortar (app/services/pagos_proximos_service.py). WhatsApp y "¿Me lo puedo permitir?" todavía usan _calcular_saldo_disponible_sync (migran en la Fase 5).

### Base de datos
- Las migraciones se aplican a mano a producción antes del push; Railway corre "alembic upgrade head" al deployar.

### Ya no existen (no los busques ni los vuelvas a crear)
- analisis_financiero_service.py, el handlers.py de WhatsApp, el módulo de transacciones recurrentes, la columna es_recurrente, la rama de transacción pendiente de IA en WhatsApp.
- El cálculo de gasto inusual (se rehace en la Fase 7). Siguen sin uso el tipo de notificación GASTO_INUSUAL y los campos gasto_inusual_* de la configuración.

### Pruebas
- Tests que cuidan reglas (nunca se les agregan excepciones): test_atomicidad_operaciones, test_cobros_suscripciones_aislados, test_formato_montos, test_whatsapp_motor_unico, test_verificacion_texto_ia, test_permitirse_whatsapp, test_herramienta_paso, test_ajustes_saldo, test_whatsapp_webhook_http.
- El .env apunta a PRODUCCIÓN. Suite, foto, personas y verificador se corren sobre la copia local con scripts/local/con_base_local.py <comando> o con scripts/local/verificar_todo.py. Cada paso de trabajo empieza y termina con scripts/local/paso.py inicio|cierre <paso>.
- Suite de WhatsApp: scripts/regresion/suite_regresion_whatsapp.py (escenarios en scripts/regresion/suite/). Las grabaciones de la IA en scripts/regresion/grabaciones_ia/ nunca se editan a mano. Comparación de salidas: scripts/regresion/comparar_salidas.py. Paridad web/WhatsApp: scripts/regresion/paridad_web_whatsapp.py.
- Foto del motor: scripts/motor/foto_motor.py. Personas: scripts/motor/evaluar_personas.py. Verificador: scripts/testingadmin/verificar_testingadmin.py.
- Ningún archivo de más de 1.300 líneas. Nunca se juntan líneas para cumplirlo: si un archivo se pasa, se parte en archivos.