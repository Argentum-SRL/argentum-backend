# Argentum backend
Antes de cambiar código, leé CLAUDE.md: sus reglas valen para cualquier herramienta.
Todo texto que el bot manda por WhatsApp sigue REGLAS_TEXTOS_WHATSAPP.md y lo controlan los tests (tests/plugin_reglas_texto.py).

Textos de la web (cartelitos y campanita): siguen REGLAS_TEXTOS_WEB.md. Un texto nuevo se
escribe primero en ese archivo. Lo controlan app/core/reglas_texto_web.py y
tests/test_reglas_texto_web*.py en backend, y reglasTextoWeb.ts y textosCartelitos.test.ts.
