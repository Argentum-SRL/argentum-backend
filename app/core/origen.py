"""
app/core/origen.py — Middleware ASGI para validar el encabezado Origin en pedidos mutables.
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class OrigenPermitidoMiddleware:
    """
    Middleware ASGI puro para validar el encabezado Origin en solicitudes mutables (POST, PUT, PATCH, DELETE).
    Rechaza con 403 pedidos con origen no autorizado. Permite peticiones sin Origin y exceptúa el webhook de WhatsApp.
    """

    def __init__(self, app: ASGIApp, origenes_permitidos: list[str]):
        self.app = app
        self.origenes_permitidos = {orig.rstrip("/") for orig in origenes_permitidos if orig}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "").upper()
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            path = scope.get("path", "")
            if not path.startswith("/api/whatsapp/webhook"):
                origin_val = None
                for name, value in scope.get("headers", []):
                    if name.lower() == b"origin":
                        origin_val = value.decode("latin1", errors="replace").strip()
                        break

                if origin_val:
                    origin_norm = origin_val.rstrip("/")
                    if origin_norm not in self.origenes_permitidos:
                        response = JSONResponse(
                            status_code=403,
                            content={
                                "success": False,
                                "error": {
                                    "code": "ORIGEN_NO_PERMITIDO",
                                    "message": "Pedido rechazado por seguridad.",
                                },
                            },
                        )
                        await response(scope, receive, send)
                        return

        await self.app(scope, receive, send)
