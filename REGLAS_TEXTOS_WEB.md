REGLAS DE LOS TEXTOS DE NOTIFICACIONES DE LA WEB — ARGENTUM
Vigentes desde el paso fase_notif_web1 (decisiones 1A, 2A y 3A de Sebastián, 16/11/2026); los errores del servidor, desde fase_notif_web2; el mail de contacto y "nuestro" en primera persona, desde fase_notif_web3.

Valen para todo texto que la web muestra sola, sin que la persona lo esté leyendo en una pantalla: los cartelitos (sileo: success, error, info, warning, action) y los avisos de la campanita (centro de notificaciones), incluido el cartelito que aparece cuando llega un aviso. Un texto nuevo o cambiado que no las cumple no se puede subir: lo frenan las pruebas automáticas (sección G). Si una regla choca con un caso nuevo, se cambia este archivo primero y después el código. Los textos de WhatsApp siguen REGLAS_TEXTOS_WHATSAPP.md; un texto que sale por los dos lados cumple los dos archivos.

Fuentes: Material Design, snackbars (mensajes breves sobre lo que se acaba de hacer, de uno o dos renglones); Nielsen, heurística 4 (consistencia: lo mismo se llama igual en todos lados) y heurística 2 (las palabras de la persona); Nielsen Norman Group, guía de mensajes de error (claro, preciso, sin culpar y con qué hacer); lectura en forma de F y frases cortas (Nielsen Norman Group; GOV.UK); avisos repetidos que se dejan de ver (Vance, Anderson y otros, Brigham Young University); memoria de trabajo de unas 4 cosas (Cowan 2001).


A. LAS 8 REGLAS

1. Lo importante, primero. El primer renglón dice qué pasó (y el monto, si hay). En la campanita ese renglón es el título: va en negrita y es lo único que se ve en el cartelito que aparece cuando llega el aviso.
2. Corto. Primer renglón de 90 caracteres o menos (el mismo límite que WhatsApp). Un cartelito es un solo renglón (title), sin description. Un aviso de la campanita tiene 7 renglones como mucho.
3. Hecho = qué cosa y cómo quedó, con punto: "Meta creada.", "Transacción eliminada.", "Billetera Galicia archivada.". Nunca "con éxito", "exitosamente", "correctamente" ni "Listo" al principio: el tilde verde ya dice que salió bien.
4. Error = qué no se pudo y qué hacer: "No se pudo eliminar la meta. Probá de nuevo.". Siempre "No se pudo" (o "No se pudieron" si la cosa va en plural), nunca "Error al", "No pudimos" ni "Intentá de nuevo". Si hay otra salida mejor que probar de nuevo, se dice esa: "No se puede eliminar una tarjeta con transacciones. Archivala.".
5. Faltan datos = qué completar, en positivo: "Ingresá un monto mayor a 0.", "Elegí una categoría.". Nunca "Seleccioná", "Debe", "válido", "inválido", "requerido", "excede".
6. Las palabras de la pantalla (decisión 1A): la cosa se llama como en el menú, el título o el botón de la pantalla donde está la persona (Billeteras → "billetera"; Transacciones → "transacción"; botón Eliminar → "eliminada"; Presupuestos, Metas, Suscripciones, Tarjetas, Informes). Los nombres de los campos, como en el formulario. Un aviso de la campanita usa la palabra de la pantalla a la que lleva al tocarlo.
7. Habla la app, no una persona: impersonal ("Se cambió...", "No se pudo...", "Te va a llegar un código..."). Nunca primera persona, ni singular ni plural ("anoté", "guardé", "voy a", "pudimos", "enviamos", "te mandamos", "vamos a", "nuestro", "nuestra", "nuestros", "nuestras", "nosotros": nunca "de nuestro lado"). Siempre "vos" (nunca "tú": tienes, puedes, quieres, dime, escribe, envía, elige, ingresa, intenta, verifica).
8. Mismo formato en toda la web (sección C).


B. PALABRAS PROHIBIDAS

"con éxito", "exitosamente", "correctamente", "Por favor", "realizar", "gestionar", "procesar", "operación", "válido", "inválido", "Error al", "debe/debés/deben", "requerido", "exceder", "ha sido/han sido", "intentá", tuteo, primera persona (regla 7, también "nuestro/a/os/as" y "nosotros"), "la web", siglas (REM, TNA, CFT, ARS, USD; "US$" sí, es como se ve un monto en dólares). "Listo" no puede ser la primera palabra. La tasa se dice "tasa ... por año" (nunca TNA).


C. FORMATO

1. Cada renglón que no es de una lista termina en ".", "?", "!" o ":".
2. Sin marcas: nunca "*" ni "_" (la negrita del título la pone la web). Sin comillas de ningún tipo: los nombres (de billeteras, metas, comercios, presupuestos, suscripciones) van sin comillas.
3. Listas: renglones que empiezan con "- " (la web los muestra como lista de verdad). Nunca "• ", "* " ni "1) " al principio de un renglón. Las tarjetas sí llevan "••••" en el nombre ("Visa Galicia •••• 1506").
4. Montos con formatear_monto (backend) o formatMonto (front). Sin centavos (redondeo al peso), salvo cuotas y facturas, que muestran el monto exacto.
5. Fechas: "hoy", "mañana", "ayer" o "d/mm" con el día sin cero y el mes con dos dígitos ("4/12", "22/11", "9/10"). Nunca el año, nunca "04/12", nunca "anteayer" ni "el 9 de octubre".
6. Tarjetas como en el menú de tarjetas: nombre_tarjeta_wpp(tarjeta) del backend ("Visa Galicia •••• 1506").
7. Sin raya larga (—, –), sin plurales con paréntesis ("movimiento(s)": se elige singular o plural según el número), sin emojis, sin texto sin rellenar (None, null, undefined, nan, llaves).
8. Sin links escritos: lo que se toca es el aviso entero (deep_link) o un botón del cartelito. Un mail de contacto sí se escribe tal cual, porque no es un link: "Tu cuenta está suspendida. Si creés que es un error, escribí a srlargentum@gmail.com." (el control saca los mails antes de buscar links).


D. AVISOS DE LA CAMPANITA: TEXTOS EXACTOS

Cada renglón va separado por un salto de línea ("\n"). {fecha} es "d/mm". Montos de C4. Todos los textos se arman en un solo archivo nuevo del backend, app/services/textos_avisos_web.py (funciones puras), y los servicios lo llaman.

N1 Cambio de contraseña (MENSAJE_CAMBIO_CONTRASENA, deep_link /app/perfil):
  Se cambió la contraseña de tu cuenta.
  Si no fuiste vos, cambiala ya.
N2 Cambio de email (deep_link /app/perfil):
  Se cambió el email de tu cuenta.
  Si no fuiste vos, cambiá tu contraseña ya.
N3 Billetera sin saldo (los 4 lugares de SALDO_CERO): Tu billetera {nombre} se quedó sin saldo.
N4 Meta cumplida:
  ¡Completaste tu meta {nombre}!
  Juntaste {monto objetivo}.
N5 Factura: a 3 días: La factura de {descripcion} de {monto exacto} vence el {fecha}.   El día: La factura de {descripcion} de {monto exacto} vence hoy.
N6 Resumen de tarjeta por vencer: El resumen de tu {tarjeta} cierra el {fecha cierre} y vence el {fecha vencimiento}.
N7 Cuota sin tarjeta por vencer: La cuota {n} de {total} de {descripcion} vence el {fecha}: {monto exacto}.
N8 Suscripción, el día del cobro: Hoy se cobra {nombre}: {monto}.
N9 Suscripción, aviso antes: {nombre} se cobra el {fecha}: {monto}.   Con 1 día de anticipación: Mañana se cobra {nombre}: {monto}.
N10 Inactividad (deep_link /app/transacciones):
  Hace {dias} días que no cargás nada.
  Cargá tus gastos para ver en qué se va tu plata.
N11 Resumen de la semana:
  Una moneda: Esta semana entraron {ingresos} y gastaste {gastos}.
              Lo que más pesó: {categoría}.        (solo si hay categoría)
  Pesos y dólares: Tu resumen de la semana.
                   - En pesos: entraron {ingresos} y gastaste {gastos}.
                   - En dólares: entraron {ingresos} y gastaste {gastos}.
                   Lo que más pesó: {categoría}.   (solo si hay categoría)
N12 Fin del mes (RESUMEN_CICLO; reemplaza en la web el texto de formatear_resumen_ciclo):
  Una moneda, primer renglón según lo que quedó: Terminó tu mes: te sobraron {balance}.  /  Terminó tu mes: gastaste {lo que falta, sin signo} más de lo que entró.  /  Terminó tu mes: gastaste lo mismo que entró.
              Entraron {ingresos} y gastaste {gastos}.
  Pesos y dólares: Terminó tu mes.
                   - En pesos: entraron {ingresos} y gastaste {gastos}.
                   - En dólares: entraron {ingresos} y gastaste {gastos}.
  Después, en los dos casos: Lo que más pesó: {categoría} ({monto}).   (solo si hay categoría)
                             Gastos chicos que se repiten:             (solo si hay; como mucho 2)
                             - {categoría}: {monto} en {n} compras      ("en 1 compra")
N13 Presupuesto superado (también sale por WhatsApp si falla la plantilla: cumple los dos archivos de reglas): Te pasaste del presupuesto {nombre}: gastaste {gastado} de {límite}.
N14 Presupuesto cerca del límite (ídem): Ya usaste el {p}% del presupuesto {nombre}: {gastado} de {límite}.
N15 Reintegro pendiente: ¿Te llegó el reintegro de {monto} de {descripcion}?   (queda igual)
N16 Resumen sin pagar:
  El resumen de tu {tarjeta} venció el {fecha}: {monto}.
  ¿Ya lo pagaste?
N17 Cobros de suscripción que faltaban: Se anotaron {n} cobros de {nombre} que faltaban.   Con 1: Se anotó 1 cobro de {nombre} que faltaba.
N18 Aviso de fin de mes (PROYECCION_NEGATIVA): queda igual (texto_aviso_fin_de_mes, compartido con WhatsApp).

Los datos de las plantillas de Meta (datos_template) no cambian: las plantillas viven en Meta.


E. CARTELITOS

1. Cada texto viejo y su texto nuevo están en TABLA_CARTELITOS_WEB.txt (aprobada con este archivo). Un cartelito nuevo sigue las reglas A a C con las mismas formas de la tabla.
2. Todo error que viene del servidor pasa por getErrorMessage(error, '<texto propio>'), y el texto propio cumple estas reglas. Nunca se muestra error.response.data.detail ni un mensaje del servidor sin pasar por getErrorMessage. getErrorMessage muestra el texto del servidor solo si cumple estas reglas (problemasTextoWeb da []); si no, muestra el texto propio del lugar. ERROR_MAP (src/utils/errorMessages.ts) solo traduce los textos técnicos que el servidor no puede evitar (validaciones automáticas en inglés y errores HTTP genéricos), y sus traducciones cumplen estas reglas.
3. Cartelito de un aviso que llega: muestra solo el título (primer renglón) del aviso.
4. Cartelito con botón (action): el botón dice la acción con una palabra ("Deshacer").
5. Errores del servidor (backend): todo texto que el servidor manda como error a la web cumple estas reglas: el detail de HTTPException, el message de un error con el formato del proyecto ({"error": {"message": ...}}), los mensajes de los manejadores de errores de app/main.py, las constantes que se usan como detail y los mensajes de las excepciones que un router devuelve con str(e). Los textos de cada error están en TABLA_ERRORES_SERVIDOR.txt del backend.


F. CÓMO SE GUARDA Y SE MUESTRA UN AVISO

1. Backend: el aviso se guarda como texto plano, un renglón por línea, listas con "- ". limpiar_mensaje_web saca links renglón por renglón y nunca junta renglones.
2. Front: parsearAviso(mensaje) (src/utils/avisoNotificacion.ts) devuelve el título y los bloques (párrafos y listas). La campanita muestra el título en negrita, los párrafos debajo y las listas como lista, con los componentes y estilos de la propia campanita (misma letra, mismos colores, sin chips ni carteles nuevos). Los avisos viejos guardados con marcas de WhatsApp (*, _, "• ") se muestran sin las marcas.


G. EXCEPCIONES (las únicas)

1. Paneles de administración (src/pages/admin y AdminPage): pueden usar palabras técnicas de su pantalla ("scheduler", "monitor"), pero cumplen el resto.
2. Webhooks de WhatsApp y Meta (app/routers/whatsapp_ia.py y app/routers/whatsapp/): sus errores los recibe Meta, no la persona.
3. Plantillas de Meta: viven en Meta y se cambian aparte.


H. CÓMO SE CONTROLA (pruebas automáticas)

1. Backend: app/core/reglas_texto_web.py::problemas_texto_web(texto) -> list[str]. Front: src/utils/reglasTextoWeb.ts::problemasTextoWeb(texto). Dan los mismos códigos para los mismos textos (W1 a W13).
2. Backend: cada familia de D tiene un test que compara el texto exacto y que pasa problemas_texto_web; un test estático revisa todo texto que llega a crear_notificacion.
3. Front: un test de Vitest recorre todo src y revisa con problemasTextoWeb cada texto de cada cartelito (también los que salen de variables, constantes y funciones); si un cartelito no se puede resolver a textos fijos ni pasa por getErrorMessage o parsearAviso, el test falla.
4. Backend: tests/test_reglas_texto_web_errores.py revisa con problemas_texto_web todo texto de error del servidor de E5 (salvo G2).
5. Todo texto nuevo lleva un test que compara el texto exacto.
