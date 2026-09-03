# Suite de Regresión WhatsApp (Argentum)

Esta carpeta contiene la suite consolidada de pruebas de regresión automatizadas para todos los flujos del webhook de WhatsApp (`whatsapp_ia.py`).

## Cobertura de la Suite
- **Punto 3**: Resolución determinística de billeteras (billetera principal, menús numéricos y por nombre, opciones fuera de rango, respuestas a números aislados, menús expirados, corrección en propuesta y usuarios con billetera única).
- **Punto 4**: Detección de intenciones, reseteo de slots y cambios de tema (cancelaciones, saludos, operaciones a medias, preguntas fuera de alcance, 6 variantes de negación y expiración de propuestas).
- **Punto 5**: Prevención de duplicados, concurrencia en confirmación y exclusión de cuotas hijas/planes de tarjeta.
- **Punto 6**: Veracidad en fechas relativas/absolutas, control de fechas >60 días o futuras, gastos en dólares, montos y límites, descarte anticipado en lotes y privacidad de saldos.

## Regla de Ejecución Obligatoria
**Debe ejecutarse y pasar al 100% antes de cualquier despliegue a producción que toque `app/routers/whatsapp_ia.py` o servicios relacionados de WhatsApp.**

## Cómo Ejecutar
Desde la raíz del repositorio (`argentum-backend`):

```bash
python scripts/regresion/suite_regresion_whatsapp.py
```

## Garantía de No Persistencia (Rollback)
Todas las pruebas corren dentro de transacciones aisladas con rollback automático y validación de conteos antes y después. La suite garantiza que la base de datos no sufre escrituras residuales ni modificaciones de saldos.
