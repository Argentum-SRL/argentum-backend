from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.database import Base, engine


def import_all_models() -> None:
    """Importa todos los módulos de app.models para registrar la metadata."""
    models_package = importlib.import_module("app.models")

    for module_info in pkgutil.iter_modules(models_package.__path__):
        if module_info.name.startswith("_"):
            continue
        importlib.import_module(f"app.models.{module_info.name}")


def main() -> None:
    import_all_models()

    Base.metadata.create_all(bind=engine)

    print("Tablas creadas correctamente")
    print("Listado de tablas creadas:")
    for table in Base.metadata.sorted_tables:
        print(f"- {table.name}")


if __name__ == "__main__":
    main()