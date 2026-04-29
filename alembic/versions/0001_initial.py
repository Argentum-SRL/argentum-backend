"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-04-28 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

tipo_usuario_enum = sa.Enum("email", "google", "telefono", name="auth_provider_enum")
rol_usuario_enum = sa.Enum("usuario", "admin", name="rol_usuario_enum")
estado_usuario_enum = sa.Enum("activo", "inactivo", name="estado_usuario_enum")
moneda_enum = sa.Enum("ARS", "USD", name="moneda_enum")
ciclo_tipo_enum = sa.Enum("dia_fijo", "regla", name="ciclo_tipo_enum")
estado_billetera_enum = sa.Enum("activa", "archivada", name="estado_billetera_enum")
tipo_categoria_enum = sa.Enum("ingreso", "egreso", name="tipo_categoria_enum")
estado_categoria_enum = sa.Enum("activa", "archivada", name="estado_categoria_enum")
estado_subcategoria_enum = sa.Enum("activa", "archivada", name="estado_subcategoria_enum")
tipo_transaccion_enum = sa.Enum("ingreso", "egreso", name="tipo_transaccion_enum")
metodo_pago_enum = sa.Enum("efectivo", "debito", "transferencia", "credito", name="metodo_pago_enum")
origen_transaccion_enum = sa.Enum(
    "manual", "ia_wpp", "ia_chat", "ia_pdf", "recurrente", name="origen_transaccion_enum"
)
estado_verificacion_enum = sa.Enum("confirmada", "pendiente", name="estado_verificacion_transaccion_enum")
frecuencia_tr_enum = sa.Enum("semanal", "quincenal", "mensual", name="frecuencia_transaccion_recurrente_enum")
estado_tr_enum = sa.Enum("activa", "pausada", name="estado_transaccion_recurrente_enum")
estado_presupuesto_enum = sa.Enum("activo", "pausado", "finalizado", name="estado_presupuesto_enum")
renovacion_presupuesto_enum = sa.Enum("automatica", "manual", name="renovacion_presupuesto_enum")
periodo_presupuesto_enum = sa.Enum("semanal", "quincenal", "mensual", name="periodo_presupuesto_enum")
estado_meta_enum = sa.Enum("activa", "completada", "pausada", name="estado_meta_enum")
tipo_movimiento_meta_enum = sa.Enum("aporte", "retiro", name="tipo_movimiento_meta_enum")
tipo_notificacion_enum = sa.Enum(
    "presupuesto_80",
    "presupuesto_100",
    "cuota_vence",
    "suscripcion_cobro",
    "meta_proxima",
    "sugerencia_presupuesto",
    "resumen_semanal",
    "resumen_mensual",
    "ia_pendiente",
    "inactividad",
    name="tipo_notificacion_enum",
)
tipo_mensaje_wpp_enum = sa.Enum("texto", "audio", name="tipo_mensaje_wpp_enum")
frecuencia_suscripcion_enum = sa.Enum(
    "mensual", "bimestral", "trimestral", "semestral", "anual", name="frecuencia_suscripcion_enum"
)
estado_suscripcion_enum = sa.Enum("activa", "pausada", "cancelada", name="estado_suscripcion_enum")


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("apellido", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("telefono", sa.String(length=20), nullable=False),
        sa.Column("foto_url", sa.String(length=500), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("auth_provider", tipo_usuario_enum, nullable=False),
        sa.Column("rol", rol_usuario_enum, nullable=False, server_default="usuario"),
        sa.Column("estado", estado_usuario_enum, nullable=False, server_default="activo"),
        sa.Column("moneda_principal", moneda_enum, nullable=False, server_default="ARS"),
        sa.Column("moneda_secundaria_activa", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tipo_dolar", sa.String(length=30), nullable=False, server_default="blue"),
        sa.Column("ciclo_tipo", ciclo_tipo_enum, nullable=True),
        sa.Column("ciclo_valor", sa.String(length=50), nullable=True),
        sa.Column("onboarding_completo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fecha_registro", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ultimo_acceso", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email", name="uq_usuarios_email"),
        sa.UniqueConstraint("telefono", name="uq_usuarios_telefono"),
    )

    op.create_table(
        "billeteras",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("saldo_actual", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("saldo_inicial", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("es_principal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("es_efectivo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("estado", estado_billetera_enum, nullable=False, server_default="activa"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_billeteras_usuario_id_usuarios"),
    )

    op.create_table(
        "categorias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("tipo", tipo_categoria_enum, nullable=False),
        sa.Column("icono", sa.String(length=50), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column("es_global", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("creador_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("estado", estado_categoria_enum, nullable=False, server_default="activa"),
        sa.ForeignKeyConstraint(["creador_id"], ["usuarios.id"], name="fk_categorias_creador_id_usuarios"),
    )

    op.create_table(
        "subcategorias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("categoria_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("es_global", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("creador_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("estado", estado_subcategoria_enum, nullable=False, server_default="activa"),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"], name="fk_subcategorias_categoria_id_categorias"),
        sa.ForeignKeyConstraint(["creador_id"], ["usuarios.id"], name="fk_subcategorias_creador_id_usuarios"),
    )

    op.create_table(
        "categorias_excluidas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("categoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subcategoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_categorias_excluidas_usuario_id_usuarios"),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"], name="fk_categorias_excluidas_categoria_id_categorias"),
        sa.ForeignKeyConstraint(["subcategoria_id"], ["subcategorias.id"], name="fk_categorias_excluidas_subcategoria_id_subcategorias"),
        sa.CheckConstraint(
            "categoria_id IS NOT NULL OR subcategoria_id IS NOT NULL",
            name="ck_categoria_excluida_categoria_or_subcategoria",
        ),
    )

    op.create_table(
        "transacciones_recurrentes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", tipo_transaccion_enum, nullable=False),
        sa.Column("monto", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("descripcion", sa.String(length=200), nullable=False),
        sa.Column("categoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subcategoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("billetera_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frecuencia", frecuencia_tr_enum, nullable=False),
        sa.Column("dia_registro", sa.Integer(), nullable=False),
        sa.Column("estado", estado_tr_enum, nullable=False, server_default="activa"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_transacciones_recurrentes_usuario_id_usuarios"),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"], name="fk_transacciones_recurrentes_categoria_id_categorias"),
        sa.ForeignKeyConstraint(["subcategoria_id"], ["subcategorias.id"], name="fk_transacciones_recurrentes_subcategoria_id_subcategorias"),
        sa.ForeignKeyConstraint(["billetera_id"], ["billeteras.id"], name="fk_transacciones_recurrentes_billetera_id_billeteras"),
    )

    op.create_table(
        "transacciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", tipo_transaccion_enum, nullable=False),
        sa.Column("monto", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("descripcion", sa.String(length=300), nullable=False),
        sa.Column("categoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subcategoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metodo_pago", metodo_pago_enum, nullable=True),
        sa.Column("billetera_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("es_recurrente", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("recurrente_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("es_cuota_hija", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("es_padre_cuotas", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("grupo_cuotas_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origen", origen_transaccion_enum, nullable=False),
        sa.Column("estado_verificacion", estado_verificacion_enum, nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_transacciones_usuario_id_usuarios"),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"], name="fk_transacciones_categoria_id_categorias"),
        sa.ForeignKeyConstraint(["subcategoria_id"], ["subcategorias.id"], name="fk_transacciones_subcategoria_id_subcategorias"),
        sa.ForeignKeyConstraint(["billetera_id"], ["billeteras.id"], name="fk_transacciones_billetera_id_billeteras"),
        sa.ForeignKeyConstraint(["recurrente_id"], ["transacciones_recurrentes.id"], name="fk_transacciones_recurrente_id_transacciones_recurrentes"),
    )

    op.create_table(
        "grupos_cuotas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaccion_padre_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("descripcion", sa.String(length=300), nullable=False),
        sa.Column("monto_total", sa.Numeric(15, 2), nullable=False),
        sa.Column("cantidad_cuotas", sa.Integer(), nullable=False),
        sa.Column("tiene_interes", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("tasa_interes", sa.Numeric(8, 4), nullable=True),
        sa.Column("total_financiado", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_grupos_cuotas_usuario_id_usuarios"),
        sa.ForeignKeyConstraint(["transaccion_padre_id"], ["transacciones.id"], name="fk_grupos_cuotas_transaccion_padre_id_transacciones"),
    )

    op.create_table(
        "cuotas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("grupo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaccion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("numero_cuota", sa.Integer(), nullable=False),
        sa.Column("monto_proyectado", sa.Numeric(15, 2), nullable=False),
        sa.Column("monto_real", sa.Numeric(15, 2), nullable=True),
        sa.Column("fecha_vencimiento", sa.Date(), nullable=False),
        sa.Column("ajustada_manual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pagada", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.ForeignKeyConstraint(["grupo_id"], ["grupos_cuotas.id"], name="fk_cuotas_grupo_id_grupos_cuotas"),
        sa.ForeignKeyConstraint(["transaccion_id"], ["transacciones.id"], name="fk_cuotas_transaccion_id_transacciones"),
    )

    op.create_table(
        "transferencias_internas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("billetera_origen_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("billetera_destino_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monto", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("notas", sa.String(length=300), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_transferencias_internas_usuario_id_usuarios"),
        sa.ForeignKeyConstraint(["billetera_origen_id"], ["billeteras.id"], name="fk_transferencias_internas_billetera_origen_id_billeteras"),
        sa.ForeignKeyConstraint(["billetera_destino_id"], ["billeteras.id"], name="fk_transferencias_internas_billetera_destino_id_billeteras"),
    )

    op.create_table(
        "presupuestos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("monto", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("periodo", periodo_presupuesto_enum, nullable=False),
        sa.Column("renovacion", renovacion_presupuesto_enum, nullable=False),
        sa.Column("estado", estado_presupuesto_enum, nullable=False, server_default="activo"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_presupuestos_usuario_id_usuarios"),
    )

    op.create_table(
        "presupuestos_categorias",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("presupuesto_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("categoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subcategoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["presupuesto_id"], ["presupuestos.id"], name="fk_presupuestos_categorias_presupuesto_id_presupuestos"),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"], name="fk_presupuestos_categorias_categoria_id_categorias"),
        sa.ForeignKeyConstraint(["subcategoria_id"], ["subcategorias.id"], name="fk_presupuestos_categorias_subcategoria_id_subcategorias"),
        sa.CheckConstraint(
            "categoria_id IS NOT NULL OR subcategoria_id IS NOT NULL",
            name="ck_presupuesto_categoria_categoria_or_subcategoria",
        ),
    )

    op.create_table(
        "periodos_presupuesto",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("presupuesto_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fecha_inicio", sa.Date(), nullable=False),
        sa.Column("fecha_fin", sa.Date(), nullable=False),
        sa.Column("monto_limite", sa.Numeric(15, 2), nullable=False),
        sa.Column("monto_usado", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("superado", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["presupuesto_id"], ["presupuestos.id"], name="fk_periodos_presupuesto_presupuesto_id_presupuestos"),
    )

    op.create_table(
        "metas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("monto_objetivo", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("monto_actual", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("fecha_limite", sa.Date(), nullable=True),
        sa.Column("color", sa.String(length=7), nullable=True),
        sa.Column("nota", sa.Text(), nullable=True),
        sa.Column("estado", estado_meta_enum, nullable=False, server_default="activa"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_metas_usuario_id_usuarios"),
    )

    op.create_table(
        "movimientos_meta",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("meta_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", tipo_movimiento_meta_enum, nullable=False),
        sa.Column("monto", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda_movimiento", moneda_enum, nullable=False),
        sa.Column("cotizacion_usada", sa.Numeric(10, 4), nullable=True),
        sa.Column("tipo_dolar_usado", sa.String(length=30), nullable=True),
        sa.Column("billetera_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fecha", sa.Date(), nullable=False),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meta_id"], ["metas.id"], name="fk_movimientos_meta_meta_id_metas"),
        sa.ForeignKeyConstraint(["billetera_id"], ["billeteras.id"], name="fk_movimientos_meta_billetera_id_billeteras"),
    )

    op.create_table(
        "suscripciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nombre", sa.String(length=100), nullable=False),
        sa.Column("categoria_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("frecuencia", frecuencia_suscripcion_enum, nullable=False),
        sa.Column("proximo_cobro", sa.Date(), nullable=False),
        sa.Column("estado", estado_suscripcion_enum, nullable=False, server_default="activa"),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_suscripciones_usuario_id_usuarios"),
        sa.ForeignKeyConstraint(["categoria_id"], ["categorias.id"], name="fk_suscripciones_categoria_id_categorias"),
    )

    op.create_table(
        "historial_suscripciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("suscripcion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monto", sa.Numeric(15, 2), nullable=False),
        sa.Column("moneda", moneda_enum, nullable=False),
        sa.Column("vigente_desde", sa.Date(), nullable=False),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["suscripcion_id"], ["suscripciones.id"], name="fk_historial_suscripciones_suscripcion_id_suscripciones"),
    )

    op.create_table(
        "notificaciones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", tipo_notificacion_enum, nullable=False),
        sa.Column("titulo", sa.String(length=200), nullable=False),
        sa.Column("mensaje", sa.Text(), nullable=False),
        sa.Column("leida", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("modulo_ref", sa.String(length=200), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_notificaciones_usuario_id_usuarios"),
    )

    op.create_table(
        "configuraciones_notificacion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tipo", tipo_notificacion_enum, nullable=False),
        sa.Column("canal_wpp", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("canal_app", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("anticipacion_dias", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_configuraciones_notificacion_usuario_id_usuarios"),
        sa.UniqueConstraint("usuario_id", "tipo", name="uq_configuracion_notificacion_usuario_tipo"),
    )

    op.create_table(
        "conversaciones_wpp",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mensaje_usuario", sa.Text(), nullable=False),
        sa.Column("tipo_mensaje", tipo_mensaje_wpp_enum, nullable=False, server_default="texto"),
        sa.Column("transcripcion", sa.Text(), nullable=True),
        sa.Column("mensaje_bot", sa.Text(), nullable=False),
        sa.Column("intent_detectado", sa.String(length=100), nullable=True),
        sa.Column("entidades", sa.JSON(), nullable=True),
        sa.Column("accion_ejecutada", sa.String(length=100), nullable=True),
        sa.Column("confianza", sa.Numeric(4, 3), nullable=True),
        sa.Column("slot_filling_activo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("slot_filling_estado", sa.JSON(), nullable=True),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_conversaciones_wpp_usuario_id_usuarios"),
    )

    op.create_table(
        "perfiles_financieros",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("usuario_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tasa_ahorro", sa.Numeric(6, 4), nullable=True),
        sa.Column("score_impulsividad", sa.Integer(), nullable=True),
        sa.Column("ratio_cuotas", sa.Numeric(6, 4), nullable=True),
        sa.Column("cumplimiento_presupuesto", sa.Numeric(6, 4), nullable=True),
        sa.Column("consistencia_registro", sa.Numeric(6, 4), nullable=True),
        sa.Column("porcentaje_suscripciones", sa.Numeric(6, 4), nullable=True),
        sa.Column("ultima_actualizacion", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fecha_creacion", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["usuario_id"], ["usuarios.id"], name="fk_perfiles_financieros_usuario_id_usuarios"),
        sa.UniqueConstraint("usuario_id", name="uq_perfiles_financieros_usuario_id"),
    )

    op.create_foreign_key(
        "fk_transacciones_grupo_cuotas_id_grupos_cuotas",
        "transacciones",
        "grupos_cuotas",
        ["grupo_cuotas_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_transacciones_grupo_cuotas_id_grupos_cuotas", "transacciones", type_="foreignkey")
    op.drop_table("perfiles_financieros")
    op.drop_table("conversaciones_wpp")
    op.drop_table("configuraciones_notificacion")
    op.drop_table("notificaciones")
    op.drop_table("historial_suscripciones")
    op.drop_table("suscripciones")
    op.drop_table("movimientos_meta")
    op.drop_table("metas")
    op.drop_table("periodos_presupuesto")
    op.drop_table("presupuestos_categorias")
    op.drop_table("presupuestos")
    op.drop_table("transferencias_internas")
    op.drop_table("cuotas")
    op.drop_table("grupos_cuotas")
    op.drop_table("transacciones")
    op.drop_table("transacciones_recurrentes")
    op.drop_table("categorias_excluidas")
    op.drop_table("subcategorias")
    op.drop_table("categorias")
    op.drop_table("billeteras")
    op.drop_table("usuarios")

    estado_suscripcion_enum.drop(op.get_bind(), checkfirst=True)
    frecuencia_suscripcion_enum.drop(op.get_bind(), checkfirst=True)
    tipo_mensaje_wpp_enum.drop(op.get_bind(), checkfirst=True)
    tipo_notificacion_enum.drop(op.get_bind(), checkfirst=True)
    estado_meta_enum.drop(op.get_bind(), checkfirst=True)
    tipo_movimiento_meta_enum.drop(op.get_bind(), checkfirst=True)
    periodo_presupuesto_enum.drop(op.get_bind(), checkfirst=True)
    renovacion_presupuesto_enum.drop(op.get_bind(), checkfirst=True)
    estado_presupuesto_enum.drop(op.get_bind(), checkfirst=True)
    estado_tr_enum.drop(op.get_bind(), checkfirst=True)
    frecuencia_tr_enum.drop(op.get_bind(), checkfirst=True)
    estado_verificacion_enum.drop(op.get_bind(), checkfirst=True)
    origen_transaccion_enum.drop(op.get_bind(), checkfirst=True)
    metodo_pago_enum.drop(op.get_bind(), checkfirst=True)
    tipo_transaccion_enum.drop(op.get_bind(), checkfirst=True)
    estado_subcategoria_enum.drop(op.get_bind(), checkfirst=True)
    estado_categoria_enum.drop(op.get_bind(), checkfirst=True)
    tipo_categoria_enum.drop(op.get_bind(), checkfirst=True)
    estado_billetera_enum.drop(op.get_bind(), checkfirst=True)
    ciclo_tipo_enum.drop(op.get_bind(), checkfirst=True)
    estado_usuario_enum.drop(op.get_bind(), checkfirst=True)
    rol_usuario_enum.drop(op.get_bind(), checkfirst=True)
    tipo_usuario_enum.drop(op.get_bind(), checkfirst=True)
    moneda_enum.drop(op.get_bind(), checkfirst=True)
