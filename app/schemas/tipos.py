from decimal import Decimal
from typing import Annotated
from pydantic import PlainSerializer

# Tipo anotado único para serialización de montos y valores Decimal a JSON.
# En el backend y en model_dump() (modo Python) se preserva el objeto Decimal intacto
# para garantizar precisión matemática y evitar errores de redondeo en cálculos financieros.
# Únicamente durante la serialización a JSON (when_used="json", ej. respuestas HTTP de FastAPI
# y model_dump(mode="json")) se transforma a float numérico, garantizando que el frontend
# reciba siempre números (type: number) y nunca cadenas de texto numéricas.
DecimalJSON = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]
