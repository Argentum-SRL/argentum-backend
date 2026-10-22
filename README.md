# Argentum backend

API de Argentum, app de finanzas personales para Argentina con carga de movimientos por WhatsApp (miargentum.com, api.miargentum.com).

Stack: FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 18 (Railway), APScheduler, OpenAI (gpt-4o-mini, gpt-4o para imágenes y PDF, Whisper), Meta WhatsApp Cloud API, Resend para mails.

Dónde está cada cosa
- Reglas del código y del motor: CLAUDE.md (manda sobre cualquier otro documento del repo).
- Plan vigente y decisiones: Project "Argentum" de Claude.ai, claude/PLAN_VIGENTE_21-10.md.
- Reglas de cada paso de trabajo con agentes: D:\Argentum\auditorias\REGLAS_ANTIGRAVITY.md (copia en .agents/rules/, que no se versiona).

Cómo se trabaja
- El .env local apunta a PRODUCCIÓN. Todo lo que lee o escribe datos se corre con scripts/local/con_base_local.py <comando>, contra la copia local en PostgreSQL (puerto 5433).
- Cada paso empieza y termina con scripts/local/paso.py inicio|cierre <paso>, que corre pytest, la suite de WhatsApp, la foto del motor, personas, verificador y build del front, y deja los crudos en D:\Argentum\auditorias\raw\<paso>\.
- Pruebas: python -m pytest; suite: scripts/regresion/suite_regresion_whatsapp.py (las grabaciones de la IA nunca se editan a mano).

Migraciones
- Se aplican a mano a producción antes del push; Railway corre "alembic upgrade head" en el Pre-Deploy y publica cuando la CI de GitHub da verde.

CI
- .github/workflows/ci.yml corre pytest contra PostgreSQL. Hoy marca la base con "alembic stamp head" en vez de ejecutar las migraciones (se corrige en el Bloque D del plan).
