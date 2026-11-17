REGLAS DE LOS TEXTOS DEL BOT DE WHATSAPP — ARGENTUM
Vigentes desde el paso fase_textos_wpp (aprobadas por Sebastián el 16/11/2026).

Todo mensaje que el bot manda por WhatsApp cumple estas reglas, siempre y en todos los casos. Un texto nuevo o cambiado que no las cumple no se puede subir: lo frenan las pruebas automáticas (sección F). Si una regla choca con un caso nuevo, se cambia este archivo primero y después el código.

Fuentes de cada regla: lectura en forma de F y frases cortas (Nielsen Norman Group; GOV.UK: con frases de 14 palabras se entiende más del 90%, con 43 menos del 10%); avisos repetidos que se dejan de ver (Vance, Anderson y otros, estudios con resonancia, Universidad Brigham Young); memoria de trabajo de unas 4 cosas (Cowan 2001); hábito por sensación de logro inmediata (BJ Fogg, Tiny Habits); consistencia y palabras de la persona (reglas 2, 4 y 6 de Nielsen); preguntas cerradas y una por vez (guía de conversación de Google); errores con salida (guía de mensajes de error de Nielsen Norman Group); formato de WhatsApp (centro de ayuda de WhatsApp: _cursiva_, listas con "- " y "1. ").


A. LAS 8 REGLAS

1. Lo importante, primero. La primera línea dice qué pasó y con qué monto. Lo demás va abajo y solo si hace falta.
2. Nada repetido. Lo que es igual para todo el mensaje (la cuenta, la fecha, el aviso) se dice una sola vez. Como mucho un "Ojo:" por mensaje, que junta todo.
3. Corto. Una idea por línea, 90 caracteres como máximo por línea. Un registro es la confirmación y, como mucho, una línea más (más la lista si son varios). Con nombres largos (de cuentas, comercios o metas), el envío parte la línea en dos en el último ": ", ". " o ", " antes del carácter 90 (partir_lineas_wpp), con las mismas palabras; los textos de las pruebas, con los nombres de las pruebas, ya tienen que entrar sin partir.
4. Que se sienta logro. "Listo, anoté..." va primero y limpio; los avisos van después.
5. Las palabras de la persona (tabla B).
6. Una sola pregunta, que se contesta con sí o no (o con un número de una lista), y va al final del mensaje: después de la línea con la pregunta solo puede haber opciones de una lista ("- ", "1. ") o líneas que empiezan con "Sí:" o "No:". La pregunta dice la acción: "¿Lo anoto?", "¿Lo borro?", "¿Lo cambio?", "¿La doy de baja?".
7. Errores con salida: qué pasó, con palabras simples, y qué hacer ahora. Nunca solo "No pude procesar...".
8. Mismo formato en todo el bot (sección C).


B. PALABRAS

Se dice / no se dice nunca:
- "cuenta" / "billetera" (tampoco "billetera de inversión": "cuenta de inversión").
- "borrar", "borré", "lo borro" / "eliminar", "anular", "deshacer", "deshecha".
- "reintegro" / "devolución" (el verbo "te devolvieron" sí se puede).
- "anoté", "lo anoto", "guardé" / "registrado", "registrada", "registrados", "registrar".
- "gasto", "ingreso" ("movimientos" solo cuando en el mismo mensaje hay gastos e ingresos) / "egreso", "egresos".
- "este mes" (con las fechas si hace falta: "este mes (desde el 30/10)") / "ciclo", "período", "balance", "proyección".
- "pase" o "pasaste" para transferencias entre cuentas propias.
- "miargentum.com" / "la web", "la web de Argentum".
- Nunca: "Por favor", "gestionar", "realizar", "corresponde", "operación", "válido", "inválido", "importe", "percepción", "con éxito", "procesar", "Aviso:", siglas (REM, TNA, CFT, ARS, USD). "US$" sí (es como se ve un monto en dólares).
- Siempre "vos" (nunca "tú": tienes, puedes, quieres, dime, escribe, envía, elige, ingresa, intenta, verifica, debes).
- Siempre en singular cuando habla el bot ("anoté", "di de baja", "no encontré"); nunca "dimos", "encontramos", "anotamos", "contactanos".


C. FORMATO

1. Listas: cada opción en su renglón, empezando con "- " (viñetas) o con "1. ", "2. "... (numeradas). WhatsApp las muestra con sangría y separadas, como listas de verdad. Nunca "• ", "* " ni "1) " al principio de un renglón. Sin renglón vacío antes de la lista ni entre opciones. La única excepción es el aviso de una sola línea "¿Qué era? 1) Me pagaron 2) Me devolvieron parte de un gasto 3) Solo pasó por mi cuenta" (decisión del 27/10: tiene que ser una línea).
2. Ejemplos y palabras de la persona en cursiva de WhatsApp (guion bajo de los dos lados, pegado al texto): _gasté 5000 en el kiosco_, _borrá eso_, _kiosco_. Nunca comillas de ningún tipo («», "", ''). Los nombres de cuentas, comercios, metas y suscripciones van sin comillas ni cursiva.
3. Sin negritas: nunca un asterisco.
4. Montos con formatear_monto y sin centavos (redondeo al peso), salvo en los renglones que hablan de cuotas o de una factura, que muestran el monto exacto con centavos si los tiene.
5. Fechas: hoy no se dice; "ayer"; cualquier otra, "9/10" o "el 9/10". Nunca el año completo, nunca "anteayer" ni "el 9 de octubre". Los meses solos sí se nombran ("el resumen de diciembre").
6. Tarjetas: red, banco y terminación, como en el menú de tarjetas: "la Visa Galicia •••• 1506". Si falta el banco: "la Visa •••• 1506".
7. Sin raya larga (—, –), sin plurales con paréntesis ("movimiento(s)"), sin emojis, sin texto sin rellenar (None, nan, null, llaves).
8. Avisos: empiezan con "Ojo:", una línea de 90 caracteres o menos, al final del mensaje, uno solo por mensaje.
9. Link: cuando el mensaje manda a la web dice "miargentum.com" (sin vista previa).


D. TEXTOS EXACTOS POR FAMILIA

{x} es un dato. {fecha} es "", " (ayer)" o " (9/10)" según C5. {tarjeta} es como C6. Una línea por renglón. Si un texto de abajo choca con las reglas A a C, manda este texto y se avisa.

D1 Registro de un gasto (directo o al confirmar):
  Listo, anoté {monto} en {subcategoria} desde {cuenta}{fecha}.
  Si la cuenta queda en negativo, segunda línea: {cuenta} quedó en negativo.
D2 Registro de un ingreso:
  Listo, anoté un ingreso de {monto} en {cuenta} ({subcategoria}).
  Con fecha: Listo, anoté un ingreso de {monto} en {cuenta} ({subcategoria}, {ayer o 9/10}).
D3 Varios en una misma cuenta:
  Listo, anoté {n} gastos desde {cuenta}:      (todos gastos)
  Listo, anoté {n} ingresos en {cuenta}:       (todos ingresos)
  Listo, anoté {n} movimientos en {cuenta}:    (mezclados)
  - {monto} en {subcategoria}                  (gasto, hoy)
  - {monto} en {subcategoria} · {ayer o 9/10}   (gasto con fecha)
  - {monto} en {subcategoria} (ingreso)        (ingreso dentro de una lista mezclada; con fecha: "(ingreso) · 9/10")
  Varias cuentas: Listo, anoté {n} gastos:  y cada renglón "- {monto} en {subcategoria} · {cuenta}" (más " · {fecha}" si tiene).
  Compra con tarjeta en una lista: "- {cuotas de D5 sin "Listo, anoté "} en {subcategoria} · {tarjeta sin "la "}".
  Nunca la línea "Registrados.".
D4 Ya anotado: sí después de un registro directo → Ya lo había anotado.  /  Ya confirmado antes → Ya estaba anotado.  /  Nada para confirmar → No tengo nada pendiente para confirmar.  /  Pasaron más de 30 minutos → Pasó mucho tiempo y lo descarté. Mandámelo de nuevo.  /  Cancelado → Listo, cancelado.  /  Duplicado que no se anota → Listo, no lo anoto.
D5 Tarjeta de crédito:
  Propuesta en cuotas: {n} cuotas de {cuota} en {subcategoria} con {tarjeta} ({total}).
                       La primera vence el {dd/mm}. ¿Las anoto?
  Propuesta en un pago: {total} en {subcategoria} con {tarjeta}, en un pago.
                        Vence el {dd/mm}. ¿Lo anoto?
  Anotado en cuotas: Listo, anoté {n} cuotas de {cuota} en {subcategoria} con {tarjeta}.
                     La primera entra en el resumen de {mes}.
  Anotado en un pago: Listo, anoté {total} en {subcategoria} con {tarjeta}, en un pago.
                      Entra en el resumen de {mes}.
  ¿Total o cuota?: ¿Los {monto} son el total o cada cuota?   (si no se entiende: Decime si es el total o cada cuota.)
  ¿Con qué tarjeta?: ¿Con qué tarjeta fue?  + lista numerada "1. Visa Galicia •••• 1506"
  Sin tarjetas: No tenés tarjetas de crédito cargadas.
                Agregala en miargentum.com o decime de qué cuenta salió y lo anoto como gasto.
  Pago del resumen: El pago del resumen se carga en miargentum.com, no por acá.
D6 Propuestas (cuando hay que confirmar):
  Gasto: ¿Anoto {monto} en {subcategoria} desde {cuenta}{fecha}?
  Ingreso: ¿Anoto un ingreso de {monto} en {cuenta} ({subcategoria})?
  Varios: ¿Anoto estos {n} gastos desde {cuenta}?  + la lista de D3 (o "¿Anoto estos {n} gastos?" con la cuenta en cada renglón).
  Las líneas extra (ya tenías cargado, rendimientos, salteados) van ANTES de la pregunta.
  Dólares desde una cuenta en pesos: {usd} en {subcategoria} desde {cuenta}: son {pesos} (oficial {cotizacion} + {n}%).
                                     ¿Lo anoto? Si te cobraron otro monto en pesos, decime cuánto.
    Sin cotización: {usd} en {subcategoria} desde {cuenta}. ¿Cuántos pesos te cobraron?
D7 Preguntas de cuenta:
  ¿De qué cuenta salió?   /   ¿A qué cuenta entró?   /   ¿De qué cuenta salieron los gastos?   /   ¿A qué cuenta entraron los ingresos?
  Con monto: ¿De qué cuenta salieron los {monto} de {subcategoria}?
  + lista numerada sin renglón vacío: 1. Efectivo Pesos
  Número fuera de la lista: Ese número no está en la lista. Elegí del 1 al {n}.
  No encontrada: No encontré esa cuenta. Elegí una:  + la lista.  (tarjeta: No encontré esa tarjeta. Elegí una: + la lista.)
  Otra moneda: {cuenta} es en {moneda de la cuenta} y el gasto es en {moneda del gasto}. Elegí otra:  + la lista.
  ¿Cuenta o tarjeta?: ¿Fue con la cuenta o con la tarjeta?  + "1. Cuenta {cuenta}" y "2. Tarjeta {tarjeta sin "la "}".
D8 Duplicados:
  A las {hora} ya anotaste {monto} en {subcategoria}. ¿Lo anoto otra vez?
  Lista con dos iguales: Mandaste 2 veces {monto} en {subcategoria}. ¿Anoto los dos?
  Ya cobrada una suscripción: {suscripcion} ({monto}) ya se cobró solo este mes. ¿Lo anoto igual?
D9 Borrar:
  ¿Borro {monto} en {subcategoria} desde {cuenta}{fecha}?   /   ¿Borro el ingreso de {monto} en {cuenta}?
  ¿Borro {monto} en {subcategoria} con {tarjeta}?   /   ¿Borro la compra de {monto} en {n} cuotas con {tarjeta}?
  ¿Borro el pase de {monto} de {origen} a {destino}?   /   ¿Borro los {n} pases que anotaste recién?
  ¿Borro los {n} gastos que anotaste recién?   /   ¿Borro el aporte de {monto} a tu meta {meta}?
  ¿Borro el reintegro de {monto} de {compra}?
  Si borrar arrastra otra cosa, una línea ANTES de la pregunta: Si lo borro, también se borra su reintegro de {monto}.  /  Si lo borro, los {monto} que te devolvieron vuelven a ser un cobro.
  Hecho: Listo, lo borré.  /  Listo, borré los {n} gastos.  /  Listo, borré los {n} pases.  /  Listo, borré el reintegro.
  Nada que borrar: No hay nada para borrar.
  Más de 30 minutos: Pasaron más de 30 minutos. Borralo en miargentum.com
  Nada anotado por acá: No anotaste nada por acá en los últimos 30 minutos. Borralo en miargentum.com
  Cuota de tarjeta: Las cuotas de tarjeta no se borran por acá. Borrala en miargentum.com
  Pago de resumen / aporte a meta / reintegro: {Los pagos de resumen | Los aportes a metas | Los reintegros} no se borran por acá. Hacelo en miargentum.com
  Cobro de suscripción: Los cobros de suscripciones se cambian en miargentum.com
  Error: Hubo un problema y no borré nada. Probá de nuevo en un rato.
D10 Corregir:
  Cambia un solo dato: ¿Lo cambio de {viejo} a {nuevo}?   (monto: "de $30.000 a $3.000"; cuenta: "de Galicia a Santander"; categoría: "de Kiosco a Supermercado"; fecha: "de hoy a ayer")
  Cambian varios: ¿Lo dejo en {monto} en {subcategoria} desde {cuenta}{fecha}?
  Hecho: Listo, quedó {monto} en {subcategoria} desde {cuenta}{fecha}.
  Hecho con categoría nueva + memoria: Listo, lo pasé a {subcategoria}.
                                       ¿Siempre que digas _{palabra}_ lo pongo ahí?
                                       (las dos líneas juntas, sin renglón vacío en el medio: van en la misma parte del mensaje)
  Memoria sí: Listo, lo voy a recordar.   Con anteriores: Listo, lo voy a recordar.
                                                        ¿Paso también {el gasto anterior | los {n} gastos anteriores} de _{palabra}_ a {subcategoria}?
  Hecho: Listo, pasé {1 gasto | {n} gastos} a {subcategoria}.   Memoria no: Listo, solo esta vez.   Anteriores no: Listo, quedan como estaban.
  No entendí qué cambiar: No entendí qué querés cambiar. Por ejemplo: _eran 3000_ o _fue con Santander_.
  Reintegro en el medio: Primero sacá el reintegro en miargentum.com y después cambiá la cuenta.
D11 Transferencias entre cuentas (el modelo a seguir; quedan igual salvo lo que choca con B y C):
  Listo. Pasaste {monto} de {origen} a {destino}{fecha}.   (con comisión: ", con {monto} de comisión")
  Propuesta: ¿Paso {monto} de {origen} a {destino}{fecha}?   Con cuenta nueva: Creé {cuenta}. ¿Paso {monto} de {origen} a {destino}?
  Compra de dólares: Salen {pesos} de {cuenta en pesos} y entran {usd} a {cuenta en dólares} (a {cotizacion}).
                     ¿Anoto la compra?
  Venta: Salen {usd} de {cuenta en dólares} y entran {pesos} a {cuenta en pesos} (a {cotizacion}).
         ¿Anoto la venta?
  Hecho: Listo. Compraste {usd} por {pesos}.  /  Listo. Vendiste {usd} por {pesos}.
  Cotización: ¿A cuánto compraste o cuántos pesos pagaste?  /  ¿A cuánto vendiste o cuántos pesos recibiste?  /  ¿A cuánto compraste o cuántos dólares entraron en {cuenta}?  /  ¿A cuánto vendiste o cuántos pesos entraron en {cuenta}?
  Cotización rara: ¿{cotizacion} por dólar? Hoy el dólar está a {referencia}. Revisá el número y mandámelo de nuevo.
  Misma cuenta: Pusiste la misma cuenta de los dos lados. ¿A qué otra cuenta lo pasaste?
  Sin cuenta de efectivo: No tenés una cuenta de efectivo en {pesos | dólares}. Creala en miargentum.com
  Mezcla con gastos: Mandame aparte las transferencias, retiros de cajero y compras de dólares.
D12 Errores y límites:
  Error general: Hubo un problema y no anoté nada. Probá de nuevo en un rato.
  No entendí (una sola versión): No te entendí. Probá así: _gasté 5000 en el kiosco_ o _cuánta plata tengo_.
  Solo un número: Mandaste solo un número. Decime en qué, por ejemplo: _5000 kiosco_.
  Más de 10 en un mensaje: Mandame hasta 10 movimientos por mensaje.
  Fecha futura: Todavía no pasó esa fecha: va con fecha de hoy.
  Más de 60 días: Solo anoto hasta 60 días para atrás: va con fecha de hoy.
  ("va" sirve igual en una propuesta y en un registro directo.)
  Cuotas fuera de rango: Las cuotas tienen que ser entre 1 y 48.
  Muchos mensajes: Estás mandando muchos mensajes seguidos. Esperá un minuto y seguimos.
  Muchos audios o fotos: Estás mandando muchos audios o fotos seguidos. Esperá un minuto y seguimos.
  Tope del día (una sola versión para texto y audio): Por hoy llegué al máximo de mensajes. Mañana seguimos; mientras, cargá en miargentum.com
  Tope del día de fotos: Por hoy no leo más fotos ni archivos. Escribime el gasto, por ejemplo: _2500 súper_.
  Tope del mes: Este mes ya no puedo responder por acá. Cargá tus movimientos en miargentum.com
  Mensaje largo: El mensaje es muy largo (máximo 1.500 caracteres). Mandámelo más corto.
  Audio largo / pesado / ilegible: El audio es muy largo (máximo 2 minutos). Mandá uno más corto o escribime.  /  El audio es muy pesado (máximo 8 MB). Mandá uno más corto o escribime.  /  No pude escuchar el audio. Escribime el gasto.
  Foto pesada / ilegible: La foto es muy pesada (máximo 5 MB). Mandá una más liviana.  /  No pude leer la foto. Escribime los datos.
  Cuenta, tarjeta o moneda que no aparece: No encontré esa cuenta. Mandame el gasto de nuevo con el nombre de la cuenta.  (tarjeta: ... con el nombre de la tarjeta.  moneda: Esa cuenta es en otra moneda. Mandame el gasto de nuevo con la cuenta correcta.)
  Un ítem de una lista que no se anota: No pude anotar {descripcion} ({monto}): {motivo corto}.  Motivos: "es en dólares y {cuenta} es en pesos", "no tenés cuentas en dólares", "el monto no está claro", "el monto es muy grande". Ninguno: No pude anotar ninguno.
  Sin categoría: No pude anotar {descripcion}. Decime en qué lo gastaste y lo anoto.
D13 Consultas:
  ¿Cuánto tengo?: Tenés {total} en tus cuentas.
                  Libres: {libre}, ya descontado lo que vence hasta el {dd/mm}.
                  En dólares: {total usd} (libres {libre usd}).
  Gastos: Este mes (desde el {dd/mm}) gastaste {total} en {n} gastos.
          Lo que más pesó: {cat1} ({monto1}) y {cat2} ({monto2}).
  Sin gastos: Hoy no anotaste gastos.  /  Este mes (desde el {dd/mm}) no encontré gastos de _{palabra}_.  /  El mes pasado (del {dd/mm} al {dd/mm}) no anotaste gastos en {categoria}.
  Ingresos y gastos del mes: Este mes (desde el {dd/mm}) entraron {ingresos} y gastaste {gastos}.
  Dólar: Dólar hoy:  + "- Blue: $1.450", "- MEP: $1.400", "- Oficial: $1.050".
  Metas: Tus metas:  + "- {meta}: {actual} de {objetivo} ({p}%)".
  Presupuestos: Tus presupuestos:  + "- {nombre}: usaste {usado} de {limite} (te quedan {resto})" o "(te pasaste por {exceso})".
  Suscripciones: Tus suscripciones:  + "- {nombre}: {monto} por mes"  y al final: Total por mes: {total}
  Errores de consulta: No pude ver {tu saldo | tus gastos | tus metas | tus presupuestos | el dólar} ahora. Probá de nuevo en unos minutos.
D14 ¿Me lo puedo permitir? y fin de mes: quedan como los dejó T1, con estos cambios (web y WhatsApp):
  "No te alcanza." + "No te alcanza ni para un mes." → una sola línea: No te alcanza ni para un mes.
  "No te alcanza." + "Con la plata de tus cuentas lo bancás {n} meses." → No te alcanza: con la plata de tus cuentas lo pagás solo {n} meses.  ("1 mes")
  "Cada mes vas a usar unos {x} de la plata que ya tenés en tus cuentas." → De cada cuota, unos {x} salen de la plata que ya tenés en tus cuentas.
  "Este ciclo ya te faltan..." → Este mes ya te faltan {x} para tus pagos; con esta compra, {y}.
  "Son 1 cuotas de {x}." → nunca: con 1 cuota se dice "en un pago".
  Nota de interés: Lo calculé sin interés. Si tiene, decime cuánto por año (por ejemplo, 60%).
  Precio pendiente (solo WhatsApp): Decime cuánto sale. Por ejemplo: _¿me puedo permitir algo de 300.000 en 6 cuotas?_   (cuotas: Decime en cuántas cuotas. Por ejemplo: ... )
  Fin de mes: "Puede que no hayas cargado todo: el rango ya lo tiene en cuenta." → Como puede faltar algo por cargar, te doy un número más bajo y uno más alto.
  En fin de mes, los avisos ("Ojo:") van al final, después de la línea de dólares.
D15 Avisos de una línea:
  Saldo, un movimiento: Ojo: es de antes del {dd/mm}: cuenta en el mes, pero no cambia tu saldo.
  Saldo, varios de una cuenta y todos anteriores: Ojo: son de antes del {dd/mm}: cuentan en el mes, pero no cambian tu saldo.
  Saldo, algunos de una lista: Ojo: {el de {monto} es | {n} son} de antes del {dd/mm}: {cuenta | cuentan} en el mes, pero no {cambia | cambian} tu saldo.
  Saldo, varias cuentas, misma fecha de saldo y todos anteriores: Ojo: son de antes del {dd/mm}: cuentan en el mes, pero no cambian tus saldos.
  Saldo, varias cuentas en cualquier otro caso: Ojo: {n} son de antes de que cargaras tus saldos: cuentan en el mes, pero no los cambian.
  Resumen sin pagar: Ojo: el resumen de {tarjeta corta} de {monto} venció el {dd/mm} y sigue sin pagar.  (varios: Ojo: tenés {n} resúmenes sin pagar; el más viejo venció el {dd/mm}.)
  Frescura y fin de mes: quedan como están.
  Personas: Si te acordás para qué era, decime _era el súper_ y lo cambio.  /  Si te acordás qué era, decime _era un regalo_ y lo cambio.
  "Si fue con otra, decime cuál." y "Si fue con otra tarjeta, decime cuál." SE SACAN de todos los mensajes (se enseña una vez en la bienvenida).
D16 Facturas y fotos:
  Factura: Factura de {empresa}: {monto}, vence el {dd/mm}. ¿Ya la pagaste?   ("venció el", "vence hoy")
           Sí: la anoto como gasto de hoy desde {cuenta}.
           No: la guardo en miargentum.com y ahí te aviso antes de que venza.
  Factura en cuotas: Factura de {empresa} en {n} cuotas de {monto}: vencen el {dd/mm} y el {dd/mm}.   (montos distintos: "Factura de {empresa} en {n} cuotas: {monto} el {dd/mm} y {monto} el {dd/mm}.")
                     ¿Ya pagaste la primera?
                     Sí: la anoto como gasto de hoy desde {cuenta} y guardo {la otra | las otras {n}} en miargentum.com.
                     No: guardo las {n} en miargentum.com y ahí te aviso antes de que venzan.
  Guardada: Listo, la guardé en miargentum.com. Ahí te aviso 3 días antes y el día que vence.  /  vencida: Listo, la guardé en miargentum.com como vencida.  /  cuotas: Listo, guardé las {n} cuotas en miargentum.com. Ahí te aviso antes de cada una.
  Segunda cuota: Guardé la segunda cuota ({monto}, vence el {dd/mm}) en miargentum.com.  /  Guardé las otras {n} cuotas en miargentum.com.
  Ya guardada: Ya tenía guardada esa factura.   Pagada: Marqué pagada la factura de {empresa}.
  Comprobante sin vencimiento: ¿Anoto {monto} en {subcategoria} desde {cuenta}? Si todavía no la pagaste, decime que no.
  Foto: Ya tenías cargado todo lo que hay en la foto.  /  No encontré gastos para anotar en la foto.  /  Vi {n} movimientos y anoto los primeros 10. Mandame otra foto con el resto.
        Ya tenías cargado: {descripcion} ({monto}, {fecha}).
        Salteé {un pase | {n} pases} entre tus cuentas. Mandame cada uno aparte:  + lista "- $15.000".
        Salteé {un pago | {n} pagos} con tarjeta. Mandámelos escritos, con la tarjeta y las cuotas:  + lista "- Meli+ $20.990".
        Salteé {uno que no figura | {n} que no figuran} como aprobado{s}.
  Rendimientos: Además anoto el rendimiento de {monto} en {cuenta}{fecha}: no es un ingreso.  /  Anoté el rendimiento de {monto} en {cuenta}.  /  Ya tenías el rendimiento de {fecha} en {cuenta}.  /  Ya tenías cargados los rendimientos de {cuenta} hasta {fecha}.  /  Vi un rendimiento de {monto} que no pude anotar. Cargalo en miargentum.com  /  Veo solo rendimientos: cargalos en miargentum.com o mandame gastos o ingresos.  /  No pude anotar el rendimiento{: motivo}.
  PDF: los textos de hoy quedan, salvo lo que choca con B y C.
D17 Reintegros y "¿Qué es esta plata?":
  Listo, anoté el reintegro de {monto} de {compra}{fecha}.
  Si no era esa compra, decime _borrá eso_.
  ¿Cuánto te devolvieron? Por ejemplo: _me devolvieron 25000 del súper_.
  ¿De qué compra?  /  ¿De qué gasto?   + lista numerada "1. Súper $100.000" (con fecha: "2. Súper $80.000 · ayer"), sin "Respondé con el número.".
  Esa compra ya tiene su reintegro. Para cambiarlo, entrá a miargentum.com
  El reintegro no puede ser más que la compra.
  Listo, queda como cobro.  /  Listo, los {monto} se restan de {gasto}.  /  Listo: solo pasó por tu cuenta, no es un ingreso.
  No encontré un gasto para restarlo. Hacelo en miargentum.com
D18 Suscripciones:
  ¿Spotify lo pagás todos los meses?   (sí → suscripción; no → gasto de una vez)
  ¿Cada cuánto se paga {servicio}? Mensual, bimestral, trimestral, semestral o anual.   (con lo habitual: "... Lo habitual es {frecuencia}.")
  Respuesta que no es una frecuencia: Decime una: mensual, bimestral, trimestral, semestral o anual.
  ¿Cuánto pagás por {servicio}?
  Propuesta: {servicio}: {monto} {por mes | cada 2 meses | ...} desde {cuenta}, primer cobro el {dd/mm}.
             ¿Anoto la suscripción?
  Hecho: Listo, anoté {servicio}: {monto} {por mes | ...} desde {cuenta}.
         Primer cobro: {dd/mm}.
  Ya existe: Ya tenés {servicio} ({monto} {por mes}). ¿Querés sumar otra?
  Baja: ¿Doy de baja {servicio} ({monto} {por mes})?  →  Listo, di de baja {servicio}.
  Precio: ¿Cambio {servicio} de {viejo} a {nuevo}?  →  Listo, {servicio} ahora es {nuevo}.
  No existe: No tenés ninguna suscripción a {servicio}.
  Cobros atrasados: Anoté {n} cobros de {servicio} que faltaban.
D19 Metas:
  ¿Aporto {monto} a tu meta {meta} desde {cuenta}?
  Listo, aporté {monto} a tu meta {meta}.   Completa: Listo, aporté {monto} a {meta}. ¡Completaste tu meta!
  ¿De qué cuenta sale el aporte a {meta}?  + lista numerada.
  No tenés metas activas.  /  No encontré la meta {nombre}. Preguntame _mis metas_ para verlas.  /  ¿A cuál meta? + lista numerada con las parecidas.
  No te alcanza: {cuenta} tiene {monto}. Elegí otra cuenta o un monto menor.
D20 Saludo, bienvenida y cuenta:
  Saludo: ¡Hola! Mandame un gasto, como _gasté 5000 en el kiosco_, o preguntá _cuánta plata tengo_.
  Saludo con algo a medias: ¡Hola! Quedó a medias anotar {monto} en {subcategoria}.
                            Contestá la pregunta de arriba o mandame otro gasto.
  Otro gasto con algo a medias: Dejé sin anotar los {monto} de {subcategoria}. Vamos con los {monto} de {subcategoria}:  + la pregunta.
  Bienvenida: ¡Listo, ya estamos conectados!
              Mandame tus gastos como se los contarías a un amigo: _almuerzo 3500_.
              También entiendo audios y fotos de tickets.
              Si me equivoco, decime _fue con Galicia_ o _borrá eso_.
  Código vencido: El código venció. Pedí uno nuevo en miargentum.com
  Número ya usado: Ese número ya está conectado a otra cuenta de Argentum.
  Desconectado: Este número se desconectó de tu cuenta porque conectaste otro WhatsApp.
                Si no fuiste vos, cambiá tu contraseña en miargentum.com
  No registrado: No encontré tu cuenta. Creala en miargentum.com
  Cambio de contraseña (texto de respaldo): Se cambió la contraseña de tu cuenta de Argentum.
                                            Si no fuiste vos, cambiala ya: https://miargentum.com/auth/recuperar-password


E. EXCEPCIONES (las únicas)

1. Textos escritos por la IA (TextoIA) que salen tal cual: hasta M2 no se controlan, porque M2 cambia el modelo de OpenAI y vuelve a grabar las respuestas; en M2 estas reglas entran en el prompt de la IA y el control pasa a valer también para ellos.
2. Plantillas de Meta (resumen del ciclo, cambio de mail, cambio de contraseña): viven en Meta y se cambian aparte (Meta tiene que volver a aprobarlas).
3. La web no sigue C2 (cursiva) ni C9 (link): esos dos son solo de WhatsApp.


F. CÓMO SE CONTROLA (pruebas automáticas)

1. app/core/reglas_texto_wpp.py::problemas_texto_wpp(texto) -> list[str] revisa las reglas que se pueden medir (lista de códigos en el prompt fase_textos_wpp).
2. Cada test de pytest: todo texto que el bot manda durante el test (envío real, mock.patch o monkeypatch de enviar_whatsapp) pasa por problemas_texto_wpp; si hay un problema, el test falla.
3. tests/test_reglas_texto_estatico.py: todo texto escrito en el código que va a WhatsApp cumple las reglas de palabras y formato.
4. Suite de regresión: al final, cada mensaje del bot de cada escenario pasa por problemas_texto_wpp; tiene que dar 0.
5. Todo texto nuevo lleva un test que compara el texto exacto (REGLAS_ANTIGRAVITY sección 5).
