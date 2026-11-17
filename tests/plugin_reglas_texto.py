"""Plugin de pytest: revisa cada texto que el bot manda por WhatsApp."""
from __future__ import annotations

import threading
from unittest import mock

import pytest
from _pytest.compat import NOTSET as _notset
from _pytest.monkeypatch import MonkeyPatch

from app.core.reglas_texto_wpp import problemas_texto_wpp
from app.routers.whatsapp.verificacion_texto_ia import TextoIA
from app.services import whatsapp_service as _ws

_lock = threading.Lock()
_capturados: list[tuple[str, str]] = []
_NOMBRES = {"enviar_whatsapp", "_enviar_registrado"}


def limpiar_captura_wpp() -> None:
    """Limpia la lista de textos anotados en el test actual."""
    with _lock:
        _capturados.clear()


def _anotar(origen: str, texto: object, *, lleva_texto_ia: bool = False) -> None:
    if lleva_texto_ia or isinstance(texto, TextoIA):
        return
    if not isinstance(texto, str) or not texto.strip():
        return
    with _lock:
        _capturados.append((origen, texto))


def _texto_de_llamada(nombre: str, args: tuple, kwargs: dict) -> tuple[object, bool]:
    if nombre == "_enviar_registrado":
        return kwargs.get("texto"), bool(kwargs.get("lleva_texto_ia", False))
    texto = args[1] if len(args) >= 2 else kwargs.get("mensaje")
    return texto, isinstance(texto, TextoIA)


def _envolver(nombre: str, fn, origen: str):
    if getattr(fn, "__wrapped_captura__", False):
        return fn

    def envuelta(*args, **kwargs):
        texto, es_ia = _texto_de_llamada(nombre, args, kwargs)
        _anotar(origen, texto, lleva_texto_ia=es_ia)
        return fn(*args, **kwargs)

    envuelta.__wrapped_captura__ = True
    return envuelta


# 1) Envío real final
_ws._enviar_registrado = _envolver(
    "_enviar_registrado",
    _ws._enviar_registrado,
    "real",
)

# 2) Reemplazos con mock.patch / mock.patch.object
_orig_exit = mock._patch.__exit__


def _exit_con_captura(self, *exc):
    try:
        attr = getattr(self, "attribute", None)
        if attr in _NOMBRES:
            objetivo = (
                getattr(self.target, attr, None)
                if hasattr(self, "target")
                else None
            )
            if isinstance(objetivo, mock.Mock):
                for llamada in objetivo.call_args_list:
                    texto, es_ia = _texto_de_llamada(
                        attr,
                        tuple(llamada.args),
                        dict(llamada.kwargs),
                    )
                    _anotar("mock", texto, lleva_texto_ia=es_ia)
    except Exception:
        pass
    return _orig_exit(self, *exc)


mock._patch.__exit__ = _exit_con_captura

# 3) Reemplazos con monkeypatch.setattr
_orig_setattr = MonkeyPatch.setattr


def _setattr_con_captura(self, target, name, value=_notset, raising=True):
    try:
        if value is _notset and isinstance(target, str) and callable(name):
            attr = target.rsplit(".", 1)[-1]
            if attr in _NOMBRES:
                name = _envolver(attr, name, "monkeypatch")
        elif isinstance(name, str) and name in _NOMBRES and callable(value):
            value = _envolver(name, value, "monkeypatch")
    except Exception:
        pass
    return _orig_setattr(self, target, name, value, raising=raising)


MonkeyPatch.setattr = _setattr_con_captura


def revisar_capturados_wpp() -> None:
    """Falla el test si alguno de los textos capturados rompe REGLAS_TEXTOS_WHATSAPP."""
    with _lock:
        items = list(_capturados)
    errores: list[str] = []
    for origen, texto in items:
        codigos = problemas_texto_wpp(texto)
        if codigos:
            errores.append(
                f"[{origen}] {', '.join(codigos)}\nTexto:\n{texto}"
            )
    if errores:
        pytest.fail(
            "Textos de WhatsApp que rompen las reglas:\n" + "\n---\n".join(errores),
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def controlar_reglas_texto_wpp(request):
    """Captura los envíos a WhatsApp del test y valida las reglas al terminar."""
    limpiar_captura_wpp()
    yield
    if request.node.get_closest_marker("sin_reglas_texto"):
        return
    revisar_capturados_wpp()
