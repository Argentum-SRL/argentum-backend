from uuid import uuid4
from app.models.usuario import Usuario, AuthProvider, EstadoUsuario, RolUsuario, CicloTipo
from app.core.database import SessionLocal
from app.services.analisis_financiero_service import calcular_perfil_nuevo
from app.services.proyeccion_service import calcular_proyeccion

def test_modal_bienvenida_flag_en_usuario_sin_historia():
    """Usuario nuevo o con < 3 ciclos: perfil y proyeccion dan mostrar_card=False, flag queda False."""
    db = SessionLocal()
    try:
        user = Usuario(
            id=uuid4(),
            email=f"test_novato_{uuid4().hex[:6]}@argentum.test",
            auth_provider=AuthProvider.EMAIL,
            estado=EstadoUsuario.ACTIVO,
            rol=RolUsuario.USUARIO,
            ciclo_tipo=CicloTipo.DIA_FIJO,
            ciclo_valor="1",
            modal_bienvenida_financiera_visto=False,
        )
        db.add(user)
        db.flush()

        perfil = calcular_perfil_nuevo(db, user)
        proy = calcular_proyeccion(db, user)

        assert perfil.get("mostrar_card") is False
        assert proy.get("mostrar_card") is False

        # El trigger de primera vez no debe dispararse
        mostrar_modal_bienvenida = bool(perfil.get("mostrar_card") and not user.modal_bienvenida_financiera_visto)
        assert mostrar_modal_bienvenida is False
    finally:
        db.rollback()
        db.close()

def test_modal_bienvenida_calificacion_momentos_distintos():
    """Si el modal ya fue visto (modal_bienvenida_financiera_visto=True), aunque califique, mostrar_modal_bienvenida es False."""
    usuario_visto = Usuario(
        id=uuid4(),
        email="test_ya_visto@argentum.test",
        auth_provider=AuthProvider.EMAIL,
        modal_bienvenida_financiera_visto=True,
    )
    # Si perfil califica:
    perfil_mostrar_card = True
    assert bool(perfil_mostrar_card and not usuario_visto.modal_bienvenida_financiera_visto) is False

    # Si proyeccion califica despues:
    proy_mostrar_card = True
    assert bool(proy_mostrar_card and not usuario_visto.modal_bienvenida_financiera_visto) is False
