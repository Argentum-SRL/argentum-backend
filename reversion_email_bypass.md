# Reversión del Bypass de Verificación de Email

## Contexto
Debido a problemas temporales con el servidor SMTP y la revisión de la cuenta de Google, se implementó un bypass completo de la verificación de email el 30 de abril de 2026. Esto permitió que el desarrollo continuara sin depender del envío de correos.

## Cambios realizados
Se modificaron los siguientes archivos para auto-verificar los emails y permitir el acceso:

1.  **`app/routers/auth.py`**:
    - En `register`: Se fuerza `email_verificado=True` y `estado=EstadoUsuario.ACTIVO`. Se comentó la llamada a `generar_y_enviar_verificacion_email`.
    - En `login_google`: Se eliminó la restricción de email no verificado, ahora lo auto-verifica en el momento.
    - En `completar_perfil`: Se fuerza `email_verificado=True` y se comentó el envío del código.
2.  **`app/services/usuario_service.py`**:
    - En `actualizar_email`: Se fuerza `email_verificado=True` y se comentó el envío del código.
3.  **`app/services/email_service.py`**:
    - `verificar_codigo_email`: Modificada para devolver siempre `(True, None)`.
    - `verificar_codigo_recuperacion`: Modificada para devolver siempre `True`.

## Instrucciones para revertir
Cuando el servicio SMTP esté configurado y la cuenta de Google aprobada, sigue estos pasos:

1.  **Restaurar `app/routers/auth.py`**:
    - En `register`: Cambiar `email_verificado` a `False`, `estado` a `EstadoUsuario.PENDIENTE_VERIFICACION` y des-comentar `generar_y_enviar_verificacion_email`.
    - En `login_google`: Restaurar el `raise HTTPException` si `user.email_verificado` es `False`.
    - En `completar_perfil`: Cambiar `email_verificado` a `False` y des-comentar el envío del código.
2.  **Restaurar `app/services/usuario_service.py`**:
    - En `actualizar_email`: Cambiar `email_verificado` a `False` y des-comentar `email_service.generar_y_enviar_verificacion_email`.
3.  **Restaurar `app/services/email_service.py`**:
    - Recuperar la lógica original de validación de códigos contra el cache en `verificar_codigo_email` y `verificar_codigo_recuperacion`.

## Prompt para Antigravity
> "Hola Antigravity. Ya tenemos solucionado el tema del SMTP y la revisión de Google. Necesito que reviertas el bypass temporal de verificación de email que se hizo anteriormente. Por favor, revisa `app/routers/auth.py`, `app/services/usuario_service.py` y `app/services/email_service.py`. Debes restaurar la obligatoriedad de la verificación por código para nuevos registros, cambios de email y logins de Google. Asegúrate de que las funciones de verificación en `email_service.py` vuelvan a validar contra el cache de códigos."
