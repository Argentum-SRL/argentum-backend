import sys
sys.path.insert(0, ".")
from datetime import date
from uuid import uuid4
from decimal import Decimal
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.main import app as fastapi_app
from app.core.database import Base, get_db
from app.core.auth import get_current_user
from app.models.usuario import Usuario, RolUsuario, EstadoUsuario, AuthProvider, CicloTipo, CicloRegla, CicloAjusteDireccion, Moneda
from app.models.billetera import Billetera, EstadoBilletera
from app.models.categoria import Categoria, TipoCategoria
from app.models.subcategoria import Subcategoria
from app.models.presupuesto import Presupuesto, PeriodoPresupuestoTipo, RenovacionPresupuesto, EstadoPresupuesto
from app.models.periodo_presupuesto import PeriodoPresupuesto
from app.models.transaccion import TipoTransaccion, MetodoPago, OrigenTransaccion, EstadoVerificacionTransaccion
from app.models.feriado import FeriadoAR
from app.services.dias_habiles_service import _feriados_cache
from app.services.dashboard_service import get_ciclo_fechas
from app.utils.fecha import hoy_argentina

# In-memory SQLite engine
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def run_smoke_test():
    import app.models
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    # Populate 2026 holidays
    feriados = [date(2026, 1, 1), date(2026, 5, 1), date(2026, 5, 25), date(2026, 7, 9), date(2026, 12, 25)]
    for f in feriados:
        session.add(FeriadoAR(fecha=f, nombre="Feriado Test", anio=2026))
    session.commit()
    _feriados_cache[2026] = sorted(feriados)

    admin_user = Usuario(
        id=uuid4(),
        email="testingadmin@argentum.com",
        rol=RolUsuario.ADMIN,
        estado=EstadoUsuario.ACTIVO,
        auth_provider=AuthProvider.EMAIL,
        moneda_principal=Moneda.ARS,
        onboarding_completo=True,
        ciclo_tipo=CicloTipo.DIA_FIJO,
        ciclo_valor="1",
        ciclo_ajuste_direccion=CicloAjusteDireccion.ANTERIOR
    )
    session.add(admin_user)

    billetera = Billetera(
        id=uuid4(),
        usuario_id=admin_user.id,
        nombre="Billetera Principal",
        moneda=Moneda.ARS,
        saldo_actual=Decimal("100000.00"),
        saldo_inicial=Decimal("100000.00"),
        estado=EstadoBilletera.ACTIVA,
        es_principal=True
    )
    session.add(billetera)

    categoria = Categoria(
        id=uuid4(),
        nombre="Servicios",
        tipo=TipoCategoria.EGRESO,
        es_global=True
    )
    session.add(categoria)

    subcategoria = Subcategoria(
        id=uuid4(),
        categoria_id=categoria.id,
        nombre="Luz",
        es_global=True
    )
    session.add(subcategoria)
    session.commit()

    # Override dependencies
    fastapi_app.dependency_overrides[get_db] = lambda: session
    fastapi_app.dependency_overrides[get_current_user] = lambda: admin_user
    client = TestClient(fastapi_app)

    results = []

    try:
        # a. Configurar ciclo DIA_FIJO con dirección "posterior" desde perfil, confirmar que persiste y preview
        res_a = client.put("/usuarios/me/ciclo-financiero", json={
            "ciclo_tipo": "dia_fijo",
            "ciclo_valor": "25",
            "ciclo_ajuste_direccion": "posterior"
        })
        assert res_a.status_code == 200, f"Error en paso a: {res_a.text}"
        data_a = res_a.json()
        assert data_a["ciclo_tipo"] == "dia_fijo"
        assert data_a["ciclo_valor"] == "25"
        assert data_a["ciclo_ajuste_direccion"] == "posterior"
        
        # Preview de cobro
        res_prev_a = client.get("/onboarding/preview-fecha-cobro?tipo=dia_fijo&valor=25&direccion=posterior")
        assert res_prev_a.status_code == 200
        prev_data_a = res_prev_a.json()
        assert "proxima_fecha_cobro" in prev_data_a
        results.append(("Paso a (DIA_FIJO 'posterior' + Preview)", "OK", f"Persistido: {data_a['ciclo_tipo']} {data_a['ciclo_valor']} {data_a['ciclo_ajuste_direccion']}, Próxima fecha: {prev_data_a['proxima_fecha_cobro']}"))

        # b. Configurar ciclo REGLA desde onboarding, confirmar que persiste y preview
        onboarding_user = Usuario(
            id=uuid4(),
            email="nuevo_onboarding@argentum.com",
            rol=RolUsuario.USUARIO,
            estado=EstadoUsuario.ACTIVO,
            auth_provider=AuthProvider.EMAIL,
            nombre="Juan",
            apellido="Perez",
            fecha_nacimiento=date(1995, 5, 20),
            sexo=Usuario.sexo.type.enums[0] if hasattr(Usuario.sexo.type, 'enums') else "masculino",
            onboarding_completo=False
        )
        session.add(onboarding_user)
        session.commit()

        fastapi_app.dependency_overrides[get_current_user] = lambda: onboarding_user
        res_b = client.post("/onboarding/ciclo-financiero", json={
            "ciclo_tipo": "regla",
            "ciclo_valor": "ultimo_viernes",
            "ciclo_ajuste_direccion": "anterior"
        })
        assert res_b.status_code == 200, f"Error en paso b: {res_b.text}"
        session.refresh(onboarding_user)
        assert onboarding_user.ciclo_tipo == CicloTipo.REGLA
        assert onboarding_user.ciclo_valor == "ultimo_viernes"
        assert onboarding_user.ciclo_ajuste_direccion == CicloAjusteDireccion.ANTERIOR

        res_prev_b = client.get("/onboarding/preview-fecha-cobro?tipo=regla&valor=ultimo_viernes&direccion=anterior")
        assert res_prev_b.status_code == 200
        prev_data_b = res_prev_b.json()
        results.append(("Paso b (REGLA 'ultimo_viernes' + Preview)", "OK", f"Persistido en onboarding: {onboarding_user.ciclo_tipo.value} {onboarding_user.ciclo_valor} {onboarding_user.ciclo_ajuste_direccion.value}, Próxima fecha: {prev_data_b['proxima_fecha_cobro']}"))

        # Volver a admin_user y actualizarle el ciclo a REGLA via /usuarios/me/ciclo-financiero
        fastapi_app.dependency_overrides[get_current_user] = lambda: admin_user
        res_admin_regla = client.put("/usuarios/me/ciclo-financiero", json={
            "ciclo_tipo": "regla",
            "ciclo_valor": "ultimo_viernes",
            "ciclo_ajuste_direccion": "anterior"
        })
        assert res_admin_regla.status_code == 200
        session.refresh(admin_user)

        # c. GET /dashboard/periodo-actual coincide con get_ciclo_fechas
        res_c = client.get("/dashboard/periodo-actual")
        assert res_c.status_code == 200, f"Error en paso c: {res_c.text}"
        data_c = res_c.json()
        expected_inicio, expected_fin = get_ciclo_fechas(admin_user, hoy_argentina())
        assert data_c["fecha_inicio"] == expected_inicio.isoformat()
        assert data_c["fecha_fin"] == expected_fin.isoformat()
        results.append(("Paso c (GET /dashboard/periodo-actual)", "OK", f"Rango: {data_c['fecha_inicio']} a {data_c['fecha_fin']} (Coincide exacto con get_ciclo_fechas)"))

        # d. GET /presupuestos con uno MENSUAL coincide con ciclo
        presu = Presupuesto(
            id=uuid4(),
            usuario_id=admin_user.id,
            nombre="Presupuesto Mensual Test",
            monto=Decimal("50000.00"),
            moneda=Moneda.ARS,
            periodo=PeriodoPresupuestoTipo.MENSUAL,
            renovacion=RenovacionPresupuesto.AUTOMATICA,
            estado=EstadoPresupuesto.ACTIVO
        )
        session.add(presu)
        session.flush()

        periodo_p = PeriodoPresupuesto(
            id=uuid4(),
            presupuesto_id=presu.id,
            fecha_inicio=expected_inicio,
            fecha_fin=expected_fin,
            monto_limite=Decimal("50000.00"),
            monto_usado=Decimal("12000.00"),
            superado=False
        )
        session.add(periodo_p)
        session.commit()

        res_d = client.get("/presupuestos")
        assert res_d.status_code == 200, f"Error en paso d: {res_d.text}"
        presu_list = res_d.json()
        assert len(presu_list) >= 1
        p_data = next(p for p in presu_list if p["id"] == str(presu.id))
        assert p_data["periodo_actual"]["fecha_inicio"] == expected_inicio.isoformat()
        assert p_data["periodo_actual"]["fecha_fin"] == expected_fin.isoformat()
        results.append(("Paso d (GET /presupuestos ciclo MENSUAL)", "OK", f"Presupuesto {p_data['nombre']} inicia {p_data['periodo_actual']['fecha_inicio']} y finaliza {p_data['periodo_actual']['fecha_fin']}"))

        # e. Crear transacción manual SIN descripción (201)
        res_e = client.post("/transacciones", json={
            "tipo": "egreso",
            "monto": 4500.00,
            "moneda": "ARS",
            "fecha": hoy_argentina().isoformat(),
            "billetera_id": str(billetera.id),
            "categoria_id": str(categoria.id),
            "metodo_pago": "debito",
            "origen": "manual"
        })
        assert res_e.status_code == 201, f"Error en paso e: {res_e.text}"
        tx_e = res_e.json()
        assert tx_e["descripcion"] == ""
        assert float(tx_e["monto"]) == 4500.00
        results.append(("Paso e (Crear tx SIN descripción)", "OK", f"Status 201, ID={tx_e['id']}, descripcion=''"))

        # f. Crear transacción manual con datos inválidos / sin campos obligatorios (422)
        res_f = client.post("/transacciones", json={
            "tipo": "egreso",
            "moneda": "ARS",
            "fecha": hoy_argentina().isoformat(),
            "billetera_id": str(billetera.id)
            # Falta monto (obligatorio)
        })
        assert res_f.status_code == 422, f"Paso f esperaba 422 y obtuvo {res_f.status_code}: {res_f.text}"
        results.append(("Paso f (Validación payload inválido / sin monto)", "OK", "Status 422 Unprocessable Content"))

        # g. Editar SOLO la fecha de una transacción que ya tenga descripción cargada
        res_g_crear = client.post("/transacciones", json={
            "tipo": "egreso",
            "monto": 9900.00,
            "moneda": "ARS",
            "fecha": "2026-08-20",
            "descripcion": "Descripción Original Intacta",
            "billetera_id": str(billetera.id),
            "categoria_id": str(categoria.id),
            "metodo_pago": "debito",
            "origen": "manual"
        })
        assert res_g_crear.status_code == 201
        tx_g_id = res_g_crear.json()["id"]

        # PATCH enviando únicamente fecha
        nueva_fecha = "2026-08-28"
        res_g_patch = client.patch(f"/transacciones/{tx_g_id}", json={
            "fecha": nueva_fecha
        })
        assert res_g_patch.status_code == 200, f"Error en paso g patch: {res_g_patch.text}"
        tx_g_updated = res_g_patch.json()
        assert tx_g_updated["fecha"] == nueva_fecha
        assert tx_g_updated["descripcion"] == "Descripción Original Intacta", f"Se perdió la descripción: {tx_g_updated}"
        results.append(("Paso g (PATCH fecha manteniendo descripción)", "OK", f"Fecha actualizada a {tx_g_updated['fecha']}, descripcion intacta='{tx_g_updated['descripcion']}'"))

        # h. GET /transacciones con filtro de ciclo actual (200)
        res_h = client.get(f"/transacciones?fecha_desde={expected_inicio.isoformat()}&fecha_hasta={expected_fin.isoformat()}")
        assert res_h.status_code == 200, f"Error en paso h: {res_h.text}"
        txs_h = res_h.json()
        assert isinstance(txs_h, list)
        results.append(("Paso h (GET /transacciones filtro ciclo actual)", "OK", f"Status 200, {len(txs_h)} transacciones encontradas en el ciclo"))

        print("=== RESULTADOS SMOKE TEST END-TO-END ===")
        for name, status_str, detail in results:
            print(f"[{status_str}] {name} -> {detail}")
        print("=== TODOS LOS PASOS COMPLETADOS CON ÉXITO ===")

    finally:
        fastapi_app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(bind=engine)

if __name__ == "__main__":
    run_smoke_test()
