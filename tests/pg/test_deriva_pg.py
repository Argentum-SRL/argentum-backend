"""
tests/pg/test_deriva_pg.py — TD.10 Verificación del conteo y alerta de deriva de saldos.
"""
from unittest.mock import patch
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.usuario import Usuario
from app.services.deriva_saldo_service import contar_billeteras_con_diferencia, _job_vigilar_deriva_saldos
from app.utils.fecha import hoy_argentina
from tests.pg.conftest import crear_fixture_a, crear_fixture_b


def test_td10_vigilar_deriva_saldos(db: Session, usuario_test: Usuario):
    hoy = hoy_argentina()
    b1 = crear_fixture_a(db, usuario_test.id, hoy, "Billetera Deriva 1")
    b2 = crear_fixture_a(db, usuario_test.id, hoy, "Billetera Deriva 2")
    b3 = crear_fixture_b(db, usuario_test.id, hoy, "Billetera Deriva 3")

    # 1. Al empezar, contar_billeteras_con_diferencia == 0 y no llama a enviar_alerta_admin
    diff_count = contar_billeteras_con_diferencia(db, hoy=hoy)
    assert diff_count == 0

    with patch("app.services.alerta_service.enviar_alerta_admin") as mock_alerta:
        res = _job_vigilar_deriva_saldos(db=db)
        assert res == 0
        assert mock_alerta.call_count == 0

        # 2. Con 1 billetera forzada a +1.00
        db.execute(text("UPDATE billeteras SET saldo_actual = saldo_actual + 1 WHERE id = :id"), {"id": b1.id})
        db.commit()

        assert contar_billeteras_con_diferencia(db, hoy=hoy) == 1

        mock_alerta.reset_mock()
        res1 = _job_vigilar_deriva_saldos(db=db)
        assert res1 == 1
        assert mock_alerta.call_count == 1

        call_kwargs = mock_alerta.call_args.kwargs
        asunto = call_kwargs.get("asunto")
        cuerpo = call_kwargs.get("cuerpo")
        assert asunto == "[Argentum] Saldos con diferencia"
        assert cuerpo == "1 billetera con diferencia.\nRevisalo con el verificador. Este aviso no incluye datos de usuarios."
        assert "@" not in cuerpo
        assert str(b1.id) not in cuerpo

        # 3. Con 3 forzadas: cuerpo empieza con "3 billeteras con diferencia."
        db.execute(text("UPDATE billeteras SET saldo_actual = saldo_actual + 1 WHERE id = :id"), {"id": b2.id})
        db.execute(text("UPDATE billeteras SET saldo_actual = saldo_actual + 1 WHERE id = :id"), {"id": b3.id})
        db.commit()

        assert contar_billeteras_con_diferencia(db, hoy=hoy) == 3

        mock_alerta.reset_mock()
        res3 = _job_vigilar_deriva_saldos(db=db)
        assert res3 == 3
        assert mock_alerta.call_count == 1

        call_kwargs3 = mock_alerta.call_args.kwargs
        cuerpo3 = call_kwargs3.get("cuerpo")
        assert cuerpo3.startswith("3 billeteras con diferencia.")

        # 4. Al terminar deja las 3 como estaban
        db.execute(text("UPDATE billeteras SET saldo_actual = saldo_actual - 1 WHERE id = :id"), {"id": b1.id})
        db.execute(text("UPDATE billeteras SET saldo_actual = saldo_actual - 1 WHERE id = :id"), {"id": b2.id})
        db.execute(text("UPDATE billeteras SET saldo_actual = saldo_actual - 1 WHERE id = :id"), {"id": b3.id})
        db.commit()

        assert contar_billeteras_con_diferencia(db, hoy=hoy) == 0
