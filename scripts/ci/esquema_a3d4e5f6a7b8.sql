-- Esquema de producción en la revisión a3d4e5f6a7b8, sin datos. Generado el 2026-10-22 por fase_d2_idempotencia.
SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;
CREATE SCHEMA public;
CREATE TYPE public.auth_provider_enum AS ENUM (
    'email',
    'google',
    'telefono'
);
CREATE TYPE public.ciclo_ajuste_direccion_enum AS ENUM (
    'anterior',
    'posterior'
);
CREATE TYPE public.ciclo_tipo_enum AS ENUM (
    'dia_fijo',
    'regla'
);
CREATE TYPE public.estado_billetera_enum AS ENUM (
    'activa',
    'archivada'
);
CREATE TYPE public.estado_categoria_enum AS ENUM (
    'activa',
    'archivada'
);
CREATE TYPE public.estado_grupo_cuotas_enum AS ENUM (
    'activo',
    'cancelado',
    'completado'
);
CREATE TYPE public.estado_importacion_enum AS ENUM (
    'procesando',
    'pendiente_revision',
    'importado',
    'error',
    'cancelado'
);
CREATE TYPE public.estado_meta_enum AS ENUM (
    'activa',
    'completada',
    'pausada'
);
CREATE TYPE public.estado_presupuesto_enum AS ENUM (
    'activo',
    'pausado',
    'finalizado'
);
CREATE TYPE public.estado_saldo_arrastrado_enum AS ENUM (
    'activo',
    'saldado'
);
CREATE TYPE public.estado_subcategoria_enum AS ENUM (
    'activa',
    'archivada'
);
CREATE TYPE public.estado_suscripcion_enum AS ENUM (
    'activa',
    'pausada',
    'cancelada'
);
CREATE TYPE public.estado_tarjeta_enum AS ENUM (
    'activa',
    'archivada'
);
CREATE TYPE public.estado_usuario_enum AS ENUM (
    'activo',
    'inactivo',
    'pendiente_verificacion'
);
CREATE TYPE public.estado_verificacion_transaccion_enum AS ENUM (
    'confirmada',
    'pendiente'
);
CREATE TYPE public.frecuencia_suscripcion_enum AS ENUM (
    'mensual',
    'bimestral',
    'trimestral',
    'semestral',
    'anual'
);
CREATE TYPE public.metodo_pago_enum AS ENUM (
    'efectivo',
    'debito',
    'transferencia',
    'credito'
);
CREATE TYPE public.moneda_enum AS ENUM (
    'ARS',
    'USD'
);
CREATE TYPE public.nivel_notificacion_enum AS ENUM (
    'CRITICA',
    'FINANCIERA_IMPORTANTE',
    'FINANCIERA_INFORMATIVA',
    'SOFT'
);
CREATE TYPE public.nivel_notificacion_sa_enum AS ENUM (
    'CRITICA',
    'FINANCIERA_IMPORTANTE',
    'FINANCIERA_INFORMATIVA',
    'SOFT'
);
CREATE TYPE public.origen_transaccion_enum AS ENUM (
    'manual',
    'ia_wpp',
    'ia_pdf',
    'recurrente'
);
CREATE TYPE public.periodo_presupuesto_enum AS ENUM (
    'semanal',
    'quincenal',
    'mensual'
);
CREATE TYPE public.red_tarjeta_enum AS ENUM (
    'visa',
    'mastercard',
    'amex',
    'naranja',
    'cabal',
    'otro'
);
CREATE TYPE public.renovacion_presupuesto_enum AS ENUM (
    'automatica',
    'manual'
);
CREATE TYPE public.rol_usuario_enum AS ENUM (
    'usuario',
    'admin'
);
CREATE TYPE public.sexo_enum AS ENUM (
    'masculino',
    'femenino',
    'no_binario',
    'prefiero_no_decir'
);
CREATE TYPE public.tipo_categoria_enum AS ENUM (
    'ingreso',
    'egreso'
);
CREATE TYPE public.tipo_correccion_enum AS ENUM (
    'categoria_cambiada',
    'monto_ajustado',
    'fecha_ajustada',
    'cuota_corregida',
    'marcado_como_duplicado',
    'transaccion_excluida',
    'titular_reasignado'
);
CREATE TYPE public.tipo_mensaje_wpp_enum AS ENUM (
    'texto',
    'audio',
    'imagen'
);
CREATE TYPE public.tipo_movimiento_meta_enum AS ENUM (
    'aporte',
    'retiro'
);
CREATE TYPE public.tipo_notificacion_enum AS ENUM (
    'CAMBIO_CONTRASENA',
    'NUEVO_DISPOSITIVO',
    'CAMBIO_EMAIL',
    'INTENTOS_LOGIN_FALLIDOS',
    'WHATSAPP_NUEVO_VINCULADO',
    'CUOTA_VENCE',
    'PRESUPUESTO_AGOTADO',
    'SALDO_CERO',
    'SUSCRIPCION_HOY',
    'PRESUPUESTO_LIMITE',
    'SUSCRIPCION_PROXIMA',
    'RESUMEN_SEMANAL',
    'META_ALCANZADA',
    'GASTO_INUSUAL',
    'INACTIVIDAD'
);
CREATE TYPE public.tipo_notificacion_sa_enum AS ENUM (
    'CAMBIO_CONTRASENA',
    'NUEVO_DISPOSITIVO',
    'CAMBIO_EMAIL',
    'INTENTOS_LOGIN_FALLIDOS',
    'WHATSAPP_NUEVO_VINCULADO',
    'CUOTA_VENCE',
    'PRESUPUESTO_AGOTADO',
    'SALDO_CERO',
    'SUSCRIPCION_HOY',
    'PRESUPUESTO_LIMITE',
    'SUSCRIPCION_PROXIMA',
    'RESUMEN_SEMANAL',
    'META_ALCANZADA',
    'GASTO_INUSUAL',
    'INACTIVIDAD',
    'RESUMEN_CICLO',
    'PROYECCION_NEGATIVA',
    'FACTURA_VENCE',
    'RESUMEN_IMPAGO',
    'REINTEGRO_PENDIENTE'
);
CREATE TYPE public.tipo_transaccion_enum AS ENUM (
    'ingreso',
    'egreso'
);
SET default_tablespace = '';
SET default_table_access_method = heap;
CREATE TABLE public.ajustes_saldo (
    id uuid NOT NULL,
    billetera_id uuid NOT NULL,
    monto numeric(15,2) NOT NULL,
    saldo_anterior numeric(15,2) NOT NULL,
    saldo_declarado numeric(15,2) NOT NULL,
    fecha date NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);
CREATE TABLE public.analisis_ia (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    usuario_id uuid NOT NULL,
    tipo_analisis character varying(30) DEFAULT 'completo'::character varying NOT NULL,
    ciclos_analizados integer NOT NULL,
    periodo_inicio date NOT NULL,
    periodo_fin date NOT NULL,
    perfil_detectado jsonb DEFAULT '{}'::jsonb NOT NULL,
    payload_enviado jsonb DEFAULT '{}'::jsonb NOT NULL,
    resultado text,
    resultado_secciones jsonb,
    estado character varying(20) DEFAULT 'pendiente'::character varying NOT NULL,
    error_detalle text,
    modelo_usado character varying(50) DEFAULT 'gpt-4o-mini'::character varying NOT NULL,
    input_tokens integer,
    output_tokens integer,
    costo_usd numeric(10,6),
    generado_por character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.billeteras (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    saldo_actual numeric(15,2) NOT NULL,
    saldo_inicial numeric(15,2) NOT NULL,
    es_principal boolean NOT NULL,
    es_efectivo boolean NOT NULL,
    estado public.estado_billetera_enum NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL,
    es_inversion boolean DEFAULT false NOT NULL,
    tna numeric(6,2),
    fecha_ultimo_rendimiento timestamp with time zone,
    entidad_id character varying(50),
    nivel_tasa character varying(120),
    fecha_saldo date,
    tiene_punto_partida boolean DEFAULT true NOT NULL,
    fecha_ultimo_control date
);
CREATE TABLE public.calibraciones_usuario (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    moneda character varying(10) NOT NULL,
    inicio_ciclo date NOT NULL,
    pasa_puerta boolean NOT NULL,
    motivo character varying(50),
    mensaje text,
    ciclos_evaluados integer DEFAULT 0 NOT NULL,
    cobertura_50 numeric(6,4),
    cobertura_80 numeric(6,4),
    cobertura_95 numeric(6,4),
    ancho_medio_80_rel numeric(8,4),
    detalles jsonb,
    fecha_calculo timestamp with time zone NOT NULL,
    duracion_ms numeric(10,2)
);
CREATE TABLE public.categorias (
    id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    tipo public.tipo_categoria_enum NOT NULL,
    icono character varying(50),
    color character varying(7),
    estado public.estado_categoria_enum NOT NULL
);
CREATE TABLE public.categorias_excluidas (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    categoria_id uuid,
    subcategoria_id uuid,
    fecha_exclusion timestamp with time zone NOT NULL,
    CONSTRAINT ck_categoria_excluida_categoria_or_subcategoria CHECK (((categoria_id IS NOT NULL) OR (subcategoria_id IS NOT NULL)))
);
CREATE TABLE public.codigos_verificacion (
    id uuid NOT NULL,
    tipo character varying(50) NOT NULL,
    identificador character varying(255) NOT NULL,
    codigo character varying(64) NOT NULL,
    expiracion timestamp with time zone NOT NULL,
    intentos_fallidos integer DEFAULT 0 NOT NULL,
    max_intentos integer DEFAULT 3 NOT NULL,
    creado_en timestamp with time zone DEFAULT now() NOT NULL,
    consumido boolean DEFAULT false NOT NULL,
    consumido_en timestamp with time zone
);
CREATE TABLE public.configuracion_notificaciones (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    cuota_vence_anticipacion_dias integer CONSTRAINT configuracion_notificacione_cuota_vence_anticipacion_d_not_null NOT NULL,
    cuota_vence_web boolean NOT NULL,
    cuota_vence_whatsapp boolean NOT NULL,
    presupuesto_umbral_1 integer NOT NULL,
    presupuesto_umbral_1_activo boolean CONSTRAINT configuracion_notificacione_presupuesto_umbral_1_activ_not_null NOT NULL,
    presupuesto_umbral_1_web boolean NOT NULL,
    presupuesto_umbral_1_whatsapp boolean CONSTRAINT configuracion_notificacione_presupuesto_umbral_1_whats_not_null NOT NULL,
    presupuesto_umbral_2_web boolean NOT NULL,
    presupuesto_umbral_2_whatsapp boolean CONSTRAINT configuracion_notificacione_presupuesto_umbral_2_whats_not_null NOT NULL,
    suscripcion_recordatorio_activo boolean CONSTRAINT configuracion_notificacione_suscripcion_recordatorio_a_not_null NOT NULL,
    suscripcion_recordatorio_dias integer CONSTRAINT configuracion_notificacione_suscripcion_recordatorio_d_not_null NOT NULL,
    suscripcion_recordatorio_web boolean CONSTRAINT configuracion_notificacione_suscripcion_recordatorio_w_not_null NOT NULL,
    suscripcion_recordatorio_whatsapp boolean CONSTRAINT configuracion_notificacion_suscripcion_recordatorio_w_not_null1 NOT NULL,
    meta_alcanzada_activo boolean NOT NULL,
    meta_alcanzada_web boolean NOT NULL,
    meta_alcanzada_whatsapp boolean NOT NULL,
    gasto_inusual_activo boolean NOT NULL,
    gasto_inusual_web boolean NOT NULL,
    gasto_inusual_whatsapp boolean NOT NULL,
    resumen_semanal_activo boolean NOT NULL,
    resumen_semanal_web boolean NOT NULL,
    resumen_semanal_whatsapp boolean NOT NULL,
    inactividad_activo boolean NOT NULL,
    inactividad_dias integer NOT NULL,
    inactividad_web boolean NOT NULL,
    inactividad_whatsapp boolean NOT NULL,
    whatsapp_hora_envio integer NOT NULL,
    whatsapp_minuto_envio integer NOT NULL,
    saldo_cero_web boolean NOT NULL,
    saldo_cero_whatsapp boolean NOT NULL,
    updated_at timestamp with time zone,
    suscripcion_hoy_web boolean DEFAULT true NOT NULL,
    suscripcion_hoy_whatsapp boolean DEFAULT true NOT NULL,
    saldo_cero_activo boolean DEFAULT true NOT NULL,
    resumen_ciclo_activo boolean DEFAULT true NOT NULL,
    resumen_ciclo_web boolean DEFAULT false NOT NULL,
    resumen_ciclo_whatsapp boolean DEFAULT true NOT NULL,
    proyeccion_negativa_activo boolean DEFAULT true CONSTRAINT configuracion_notificacione_proyeccion_negativa_activo_not_null NOT NULL,
    proyeccion_negativa_web boolean DEFAULT true NOT NULL,
    proyeccion_negativa_whatsapp boolean DEFAULT true CONSTRAINT configuracion_notificacione_proyeccion_negativa_whatsa_not_null NOT NULL
);
CREATE TABLE public.conversaciones_wpp (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    mensaje_usuario text NOT NULL,
    tipo_mensaje public.tipo_mensaje_wpp_enum NOT NULL,
    transcripcion text,
    mensaje_bot text NOT NULL,
    intent_detectado character varying(100),
    entidades json,
    accion_ejecutada text,
    confianza numeric(4,3),
    slot_filling_activo boolean NOT NULL,
    slot_filling_estado json,
    fecha timestamp with time zone NOT NULL,
    wamid character varying(255),
    tiempo_respuesta_ms integer,
    error_ia boolean DEFAULT false NOT NULL
);
CREATE TABLE public.correcciones_importacion (
    id uuid NOT NULL,
    importacion_id uuid NOT NULL,
    banco character varying(30) NOT NULL,
    capa_parser_usada character varying(30) NOT NULL,
    tipo_correccion public.tipo_correccion_enum NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.cotizaciones_dolar (
    id uuid NOT NULL,
    fecha date NOT NULL,
    tipo character varying(30) NOT NULL,
    compra numeric(15,4) NOT NULL,
    venta numeric(15,4) NOT NULL,
    promedio numeric(15,4) NOT NULL,
    fecha_registro timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.cuotas (
    id uuid NOT NULL,
    grupo_id uuid NOT NULL,
    transaccion_id uuid NOT NULL,
    numero_cuota integer NOT NULL,
    monto_proyectado numeric(15,2) NOT NULL,
    monto_real numeric(15,2),
    fecha_vencimiento date NOT NULL,
    ajustada_manual boolean NOT NULL,
    pagada boolean NOT NULL,
    transaccion_pago_id uuid
);
CREATE TABLE public.decisiones_patrones (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    clave_item character varying(200) NOT NULL,
    decision character varying(12) NOT NULL,
    caja_destino character varying(12),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_decisiones_patrones_caja_destino CHECK (((caja_destino IS NULL) OR ((caja_destino)::text = ANY (ARRAY[('fijo'::character varying)::text, ('costumbre'::character varying)::text, ('dia_a_dia'::character varying)::text])))),
    CONSTRAINT chk_decisiones_patrones_caja_destino_movido CHECK (((((decision)::text = 'movido'::text) AND (caja_destino IS NOT NULL)) OR (((decision)::text <> 'movido'::text) AND (caja_destino IS NULL)))),
    CONSTRAINT chk_decisiones_patrones_decision CHECK (((decision)::text = ANY (ARRAY[('confirmado'::character varying)::text, ('descartado'::character varying)::text, ('movido'::character varying)::text])))
);
CREATE TABLE public.ejecuciones_jobs (
    id uuid NOT NULL,
    job_id character varying(100) NOT NULL,
    inicio timestamp with time zone NOT NULL,
    fin timestamp with time zone NOT NULL,
    resultado character varying(20) NOT NULL,
    duracion_ms integer NOT NULL,
    detalle character varying(300)
);
CREATE TABLE public.eventos_actualizacion (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    entidad character varying(50) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.facturas (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    descripcion character varying(120) NOT NULL,
    monto numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    fecha_vencimiento date NOT NULL,
    categoria_id uuid,
    subcategoria_id uuid,
    estado character varying(12) DEFAULT 'pendiente'::character varying NOT NULL,
    pagada_automaticamente boolean DEFAULT false NOT NULL,
    transaccion_id uuid,
    origen character varying(20) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT chk_facturas_estado CHECK (((estado)::text = ANY (ARRAY[('pendiente'::character varying)::text, ('pagada'::character varying)::text, ('descartada'::character varying)::text]))),
    CONSTRAINT chk_facturas_monto_positivo CHECK ((monto > (0)::numeric)),
    CONSTRAINT chk_facturas_origen CHECK (((origen)::text = ANY (ARRAY[('whatsapp_foto'::character varying)::text, ('whatsapp_pdf'::character varying)::text])))
);
CREATE TABLE public.fechas_resumen_tarjeta (
    id uuid NOT NULL,
    tarjeta_id uuid NOT NULL,
    fecha_cierre date NOT NULL,
    fecha_vencimiento date NOT NULL,
    fecha_creacion timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.feriados_ar (
    id uuid NOT NULL,
    fecha date NOT NULL,
    nombre character varying(255),
    anio integer NOT NULL,
    fecha_actualizacion timestamp with time zone NOT NULL
);
CREATE TABLE public.grupos_cuotas (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    transaccion_padre_id uuid NOT NULL,
    descripcion character varying(300) NOT NULL,
    monto_total numeric(15,2) NOT NULL,
    cantidad_cuotas integer NOT NULL,
    tiene_interes boolean NOT NULL,
    tasa_interes numeric(8,4),
    total_financiado numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL,
    tarjeta_id uuid,
    primer_vencimiento date,
    estado public.estado_grupo_cuotas_enum DEFAULT 'activo'::public.estado_grupo_cuotas_enum NOT NULL
);
CREATE TABLE public.historial_perfiles_financieros (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    periodo_inicio date NOT NULL,
    periodo_fin date NOT NULL,
    consistencia_registro numeric(6,4),
    fecha_snapshot timestamp with time zone NOT NULL,
    tasa_ahorro_ars numeric(6,4),
    tasa_ahorro_usd numeric(6,4),
    ratio_cuotas_ars numeric(6,4),
    ratio_cuotas_usd numeric(6,4)
);
CREATE TABLE public.historial_suscripciones (
    id uuid NOT NULL,
    suscripcion_id uuid NOT NULL,
    monto numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    vigente_desde date NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.importaciones_resumen (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    admin_id uuid NOT NULL,
    tarjeta_id uuid,
    banco_detectado character varying(30) NOT NULL,
    nombre_archivo character varying(255) NOT NULL,
    estado public.estado_importacion_enum DEFAULT 'procesando'::public.estado_importacion_enum NOT NULL,
    capa_parser_usada character varying(20),
    confianza_extraccion numeric(3,2),
    periodo_desde date,
    periodo_hasta date,
    titulares_detectados jsonb,
    titulares_seleccionados jsonb,
    transacciones_parseadas jsonb,
    total_detectadas integer DEFAULT 0 NOT NULL,
    total_importadas integer DEFAULT 0 NOT NULL,
    total_duplicadas integer DEFAULT 0 NOT NULL,
    total_excluidas integer DEFAULT 0 NOT NULL,
    mensaje_error text,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.ipc_cache (
    id uuid NOT NULL,
    indice_acumulado double precision NOT NULL,
    fecha_dato character varying NOT NULL,
    fecha_actualizacion timestamp with time zone NOT NULL,
    fuente character varying NOT NULL,
    es_estimado boolean NOT NULL
);
CREATE TABLE public.memoria_comercios (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    clave character varying(120) NOT NULL,
    tipo character varying(10) NOT NULL,
    categoria_id uuid NOT NULL,
    subcategoria_id uuid,
    fecha_creacion timestamp with time zone NOT NULL,
    fecha_actualizacion timestamp with time zone NOT NULL
);
CREATE TABLE public.mensajes_salientes_wpp (
    id uuid NOT NULL,
    fecha timestamp with time zone DEFAULT now() NOT NULL,
    usuario_id uuid,
    telefono_hash character varying(64),
    tipo character varying(60) NOT NULL,
    es_plantilla boolean DEFAULT false NOT NULL,
    partes integer DEFAULT 1 NOT NULL,
    aviso_tipo character varying(40),
    wamid_entrante character varying(255),
    enviado boolean NOT NULL,
    motivo_no_enviado character varying(40),
    http_status integer,
    codigo_error_meta integer,
    wamid_meta character varying(255),
    estado_meta character varying(20),
    tipo_cobro character varying(40),
    categoria_cobro character varying(40),
    actualizado_en timestamp with time zone
);
CREATE TABLE public.mensajes_whatsapp_procesados (
    id uuid NOT NULL,
    wamid character varying(255) NOT NULL,
    telefono character varying(50),
    tipo_mensaje character varying(50),
    fecha_recepcion timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.metas (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    monto_objetivo numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    monto_actual numeric(15,2) NOT NULL,
    fecha_limite date,
    color character varying(7),
    nota text,
    estado public.estado_meta_enum NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.movimientos_meta (
    id uuid NOT NULL,
    meta_id uuid NOT NULL,
    tipo public.tipo_movimiento_meta_enum NOT NULL,
    monto numeric(15,2) NOT NULL,
    moneda_movimiento public.moneda_enum NOT NULL,
    cotizacion_usada numeric(15,4),
    tipo_dolar_usado character varying(30),
    billetera_id uuid NOT NULL,
    fecha date NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.notificaciones (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    tipo public.tipo_notificacion_sa_enum NOT NULL,
    mensaje text NOT NULL,
    leida boolean NOT NULL,
    nivel public.nivel_notificacion_sa_enum NOT NULL,
    archivada boolean NOT NULL,
    canal_web boolean NOT NULL,
    canal_whatsapp boolean NOT NULL,
    canal_email boolean NOT NULL,
    enviada_whatsapp boolean NOT NULL,
    enviada_email boolean NOT NULL,
    grupo_agrupacion character varying(200),
    entidad_tipo character varying(50),
    entidad_id uuid,
    silenciada_hasta timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    deep_link character varying(300),
    datos_template jsonb
);
CREATE TABLE public.pagos_saldo_arrastrado (
    id uuid NOT NULL,
    saldo_arrastrado_id uuid NOT NULL,
    transaccion_pago_id uuid NOT NULL,
    monto_aplicado numeric(15,2) NOT NULL,
    fecha_creacion timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.perfiles_financieros (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    consistencia_registro numeric(6,4),
    ultima_actualizacion timestamp with time zone,
    fecha_creacion timestamp with time zone NOT NULL,
    tasa_ahorro_ars numeric(6,4),
    tasa_ahorro_usd numeric(6,4),
    ratio_cuotas_ars numeric(6,4),
    ratio_cuotas_usd numeric(6,4)
);
CREATE TABLE public.periodos_presupuesto (
    id uuid NOT NULL,
    presupuesto_id uuid NOT NULL,
    fecha_inicio date NOT NULL,
    fecha_fin date NOT NULL,
    monto_limite numeric(15,2) NOT NULL,
    monto_usado numeric(15,2) NOT NULL,
    superado boolean NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.presupuestos (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    monto numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    periodo public.periodo_presupuesto_enum NOT NULL,
    renovacion public.renovacion_presupuesto_enum NOT NULL,
    estado public.estado_presupuesto_enum NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL,
    eliminado_en timestamp with time zone
);
CREATE TABLE public.presupuestos_categorias (
    id uuid NOT NULL,
    presupuesto_id uuid NOT NULL,
    categoria_id uuid,
    subcategoria_id uuid,
    CONSTRAINT ck_presupuesto_categoria_categoria_or_subcategoria CHECK (((categoria_id IS NOT NULL) OR (subcategoria_id IS NOT NULL)))
);
CREATE TABLE public.rate_limits (
    id uuid NOT NULL,
    accion character varying(100) NOT NULL,
    identificador character varying(255) NOT NULL,
    cantidad integer DEFAULT 1 NOT NULL,
    ventana_segundos integer NOT NULL,
    ventana_inicio timestamp with time zone NOT NULL,
    expira_en timestamp with time zone NOT NULL,
    detalles json,
    actualizado_en timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.rendimientos_billetera (
    id uuid NOT NULL,
    billetera_id uuid NOT NULL,
    monto numeric(15,2) NOT NULL,
    fecha timestamp with time zone NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL
);
CREATE TABLE public.saldos_arrastrados_tarjeta (
    id uuid NOT NULL,
    tarjeta_id uuid NOT NULL,
    fecha_vencimiento_resumen date NOT NULL,
    monto_inicial numeric(15,2) NOT NULL,
    monto_restante numeric(15,2) NOT NULL,
    moneda public.moneda_enum DEFAULT 'ARS'::public.moneda_enum NOT NULL,
    estado public.estado_saldo_arrastrado_enum DEFAULT 'activo'::public.estado_saldo_arrastrado_enum NOT NULL,
    transaccion_origen_id uuid NOT NULL,
    fecha_creacion timestamp with time zone DEFAULT now() NOT NULL,
    fecha_modificacion timestamp with time zone DEFAULT now() NOT NULL
);
CREATE TABLE public.subcategorias (
    id uuid NOT NULL,
    categoria_id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    estado public.estado_subcategoria_enum NOT NULL,
    orden integer DEFAULT 0 NOT NULL
);
CREATE TABLE public.suscripciones (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    categoria_id uuid,
    frecuencia public.frecuencia_suscripcion_enum NOT NULL,
    proximo_cobro date NOT NULL,
    estado public.estado_suscripcion_enum NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL,
    billetera_id uuid,
    tarjeta_id uuid,
    subcategoria_id uuid
);
CREATE TABLE public.tarjetas_credito (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    billetera_id uuid NOT NULL,
    nombre character varying(100) NOT NULL,
    red public.red_tarjeta_enum NOT NULL,
    dia_cierre integer NOT NULL,
    dia_vencimiento integer NOT NULL,
    limite_credito numeric(15,2),
    moneda public.moneda_enum NOT NULL,
    estado public.estado_tarjeta_enum NOT NULL,
    color character varying(7),
    fecha_creacion timestamp with time zone NOT NULL,
    percepcion_moneda_extranjera numeric(5,2) DEFAULT 30.00 NOT NULL,
    apodo character varying(50),
    regla_cierre character varying(20) DEFAULT 'dia_fijo'::character varying NOT NULL,
    semana_cierre integer,
    dia_semana_cierre integer,
    dias_hasta_vencimiento integer
);
CREATE TABLE public.tasas_entidades (
    id uuid NOT NULL,
    fuente character varying(30) NOT NULL,
    clave character varying(120) NOT NULL,
    tna numeric(8,4) NOT NULL,
    tope numeric(15,2),
    condiciones character varying(300),
    fecha_dato date NOT NULL,
    vcp numeric(20,6),
    vcp_anterior numeric(20,6),
    fecha_dato_anterior date,
    fecha_consulta timestamp with time zone NOT NULL
);
CREATE TABLE public.transacciones (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    tipo public.tipo_transaccion_enum NOT NULL,
    monto numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    fecha date NOT NULL,
    descripcion character varying(300) NOT NULL,
    categoria_id uuid,
    subcategoria_id uuid,
    metodo_pago public.metodo_pago_enum,
    billetera_id uuid NOT NULL,
    es_cuota_hija boolean NOT NULL,
    es_padre_cuotas boolean NOT NULL,
    grupo_cuotas_id uuid,
    origen public.origen_transaccion_enum NOT NULL,
    estado_verificacion public.estado_verificacion_transaccion_enum,
    fecha_creacion timestamp with time zone NOT NULL,
    tarjeta_id uuid,
    primer_vencimiento_manual date,
    import_hash character varying(64),
    importacion_id uuid,
    titular_pdf character varying(150),
    monto_original numeric(15,2),
    moneda_original public.moneda_enum,
    cotizacion_aplicada numeric(15,4),
    tipo_dolar_usado character varying(30),
    pago_resumen_vencimiento date,
    pago_origen_id uuid,
    suscripcion_id uuid,
    movimiento_meta_id uuid,
    saldo_aplicado boolean DEFAULT true NOT NULL,
    es_reintegro boolean DEFAULT false NOT NULL,
    reintegro_esperado numeric(15,2),
    reintegro_de_id uuid
);
CREATE TABLE public.transferencias_internas (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    billetera_origen_id uuid NOT NULL,
    billetera_destino_id uuid NOT NULL,
    monto numeric(15,2) NOT NULL,
    moneda public.moneda_enum NOT NULL,
    fecha date NOT NULL,
    notas character varying(300),
    fecha_creacion timestamp with time zone NOT NULL,
    monto_origen numeric(15,2),
    monto_destino numeric(15,2),
    moneda_origen public.moneda_enum,
    moneda_destino public.moneda_enum,
    cotizacion numeric(15,4),
    transaccion_comision_id uuid,
    monto_comision numeric(15,2),
    moneda_comision public.moneda_enum
);
CREATE TABLE public.user_refresh_tokens (
    id uuid NOT NULL,
    usuario_id uuid NOT NULL,
    token_id character varying(64) NOT NULL,
    token_hash character varying(64) NOT NULL,
    fecha_expiracion timestamp with time zone NOT NULL,
    fecha_creacion timestamp with time zone NOT NULL,
    revocado boolean DEFAULT false NOT NULL,
    device_info character varying(200)
);
CREATE TABLE public.uso_ia (
    id uuid NOT NULL,
    fecha timestamp with time zone DEFAULT now() NOT NULL,
    usuario_id uuid,
    funcion character varying(40) NOT NULL,
    modelo character varying(60) NOT NULL,
    tokens_entrada integer DEFAULT 0 NOT NULL,
    tokens_entrada_cache integer DEFAULT 0 NOT NULL,
    tokens_salida integer DEFAULT 0 NOT NULL,
    segundos_audio integer DEFAULT 0 NOT NULL,
    costo_usd numeric(12,6) DEFAULT 0 NOT NULL
);
CREATE TABLE public.usuarios (
    id uuid NOT NULL,
    nombre character varying(100),
    apellido character varying(100),
    email character varying(255),
    telefono character varying(20),
    fecha_nacimiento date,
    sexo public.sexo_enum,
    foto_url character varying(500),
    password_hash character varying(255),
    password_configurada boolean NOT NULL,
    auth_provider public.auth_provider_enum NOT NULL,
    rol public.rol_usuario_enum NOT NULL,
    estado public.estado_usuario_enum NOT NULL,
    moneda_principal public.moneda_enum,
    moneda_secundaria_activa boolean NOT NULL,
    tipo_dolar character varying(30) NOT NULL,
    ciclo_tipo public.ciclo_tipo_enum,
    ciclo_valor character varying(50),
    onboarding_completo boolean NOT NULL,
    email_verificado boolean NOT NULL,
    telefono_verificado boolean NOT NULL,
    fecha_registro timestamp with time zone NOT NULL,
    ultimo_acceso timestamp with time zone,
    is_admin boolean DEFAULT false NOT NULL,
    reset_token_hash character varying(255),
    reset_token_expira_at timestamp with time zone,
    tokens_revocados_at timestamp with time zone,
    telefono_normalizado character varying(20),
    ciclo_ajuste_direccion public.ciclo_ajuste_direccion_enum,
    intentos_fallidos_login integer DEFAULT 0 NOT NULL,
    ultimo_intento_fallido_at timestamp with time zone,
    modal_bienvenida_financiera_visto boolean DEFAULT false NOT NULL,
    permiso_lectura_mensajes_en timestamp with time zone
);
ALTER TABLE ONLY public.ajustes_saldo
    ADD CONSTRAINT ajustes_saldo_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);
ALTER TABLE ONLY public.analisis_ia
    ADD CONSTRAINT analisis_ia_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.billeteras
    ADD CONSTRAINT billeteras_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.calibraciones_usuario
    ADD CONSTRAINT calibraciones_usuario_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.categorias_excluidas
    ADD CONSTRAINT categorias_excluidas_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.categorias
    ADD CONSTRAINT categorias_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.codigos_verificacion
    ADD CONSTRAINT codigos_verificacion_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.configuracion_notificaciones
    ADD CONSTRAINT configuracion_notificaciones_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.configuracion_notificaciones
    ADD CONSTRAINT configuracion_notificaciones_usuario_id_key UNIQUE (usuario_id);
ALTER TABLE ONLY public.conversaciones_wpp
    ADD CONSTRAINT conversaciones_wpp_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.correcciones_importacion
    ADD CONSTRAINT correcciones_importacion_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cotizaciones_dolar
    ADD CONSTRAINT cotizaciones_dolar_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.cuotas
    ADD CONSTRAINT cuotas_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.decisiones_patrones
    ADD CONSTRAINT decisiones_patrones_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.ejecuciones_jobs
    ADD CONSTRAINT ejecuciones_jobs_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.eventos_actualizacion
    ADD CONSTRAINT eventos_actualizacion_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.facturas
    ADD CONSTRAINT facturas_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.fechas_resumen_tarjeta
    ADD CONSTRAINT fechas_resumen_tarjeta_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.feriados_ar
    ADD CONSTRAINT feriados_ar_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.grupos_cuotas
    ADD CONSTRAINT grupos_cuotas_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.historial_perfiles_financieros
    ADD CONSTRAINT historial_perfiles_financieros_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.historial_suscripciones
    ADD CONSTRAINT historial_suscripciones_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.importaciones_resumen
    ADD CONSTRAINT importaciones_resumen_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.ipc_cache
    ADD CONSTRAINT ipc_cache_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.memoria_comercios
    ADD CONSTRAINT memoria_comercios_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.mensajes_salientes_wpp
    ADD CONSTRAINT mensajes_salientes_wpp_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.mensajes_salientes_wpp
    ADD CONSTRAINT mensajes_salientes_wpp_wamid_meta_key UNIQUE (wamid_meta);
ALTER TABLE ONLY public.mensajes_whatsapp_procesados
    ADD CONSTRAINT mensajes_whatsapp_procesados_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.metas
    ADD CONSTRAINT metas_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.movimientos_meta
    ADD CONSTRAINT movimientos_meta_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.notificaciones
    ADD CONSTRAINT notificaciones_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.pagos_saldo_arrastrado
    ADD CONSTRAINT pagos_saldo_arrastrado_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.perfiles_financieros
    ADD CONSTRAINT perfiles_financieros_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.perfiles_financieros
    ADD CONSTRAINT perfiles_financieros_usuario_id_key UNIQUE (usuario_id);
ALTER TABLE ONLY public.periodos_presupuesto
    ADD CONSTRAINT periodos_presupuesto_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.presupuestos_categorias
    ADD CONSTRAINT presupuestos_categorias_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.presupuestos
    ADD CONSTRAINT presupuestos_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.rate_limits
    ADD CONSTRAINT rate_limits_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.rendimientos_billetera
    ADD CONSTRAINT rendimientos_billetera_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.saldos_arrastrados_tarjeta
    ADD CONSTRAINT saldos_arrastrados_tarjeta_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.subcategorias
    ADD CONSTRAINT subcategorias_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.suscripciones
    ADD CONSTRAINT suscripciones_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.tarjetas_credito
    ADD CONSTRAINT tarjetas_credito_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.tasas_entidades
    ADD CONSTRAINT tasas_entidades_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.transferencias_internas
    ADD CONSTRAINT transferencias_internas_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.calibraciones_usuario
    ADD CONSTRAINT uq_calibraciones_usuario_usuario_moneda UNIQUE (usuario_id, moneda);
ALTER TABLE ONLY public.cotizaciones_dolar
    ADD CONSTRAINT uq_cotizaciones_dolar_fecha_tipo UNIQUE (fecha, tipo);
ALTER TABLE ONLY public.decisiones_patrones
    ADD CONSTRAINT uq_decisiones_patrones_usuario_clave UNIQUE (usuario_id, clave_item);
ALTER TABLE ONLY public.fechas_resumen_tarjeta
    ADD CONSTRAINT uq_fechas_resumen_tarjeta_tarjeta_id_fecha_cierre UNIQUE (tarjeta_id, fecha_cierre);
ALTER TABLE ONLY public.feriados_ar
    ADD CONSTRAINT uq_feriados_ar_fecha UNIQUE (fecha);
ALTER TABLE ONLY public.ipc_cache
    ADD CONSTRAINT uq_ipc_cache_fecha_dato UNIQUE (fecha_dato);
ALTER TABLE ONLY public.memoria_comercios
    ADD CONSTRAINT uq_memoria_comercios_usuario_clave_tipo UNIQUE (usuario_id, clave, tipo);
ALTER TABLE ONLY public.mensajes_whatsapp_procesados
    ADD CONSTRAINT uq_mensajes_whatsapp_procesados_wamid UNIQUE (wamid);
ALTER TABLE ONLY public.presupuestos_categorias
    ADD CONSTRAINT uq_presupuestos_categorias_presupuesto_cat_subcat UNIQUE (presupuesto_id, categoria_id, subcategoria_id);
ALTER TABLE ONLY public.rate_limits
    ADD CONSTRAINT uq_rate_limits_accion_identificador UNIQUE (accion, identificador);
ALTER TABLE ONLY public.tasas_entidades
    ADD CONSTRAINT uq_tasas_entidades_fuente_clave_fecha UNIQUE (fuente, clave, fecha_dato);
ALTER TABLE ONLY public.user_refresh_tokens
    ADD CONSTRAINT user_refresh_tokens_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.uso_ia
    ADD CONSTRAINT uso_ia_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_email_key UNIQUE (email);
ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_telefono_key UNIQUE (telefono);
CREATE UNIQUE INDEX idx_transacciones_import_hash ON public.transacciones USING btree (usuario_id, import_hash) WHERE (import_hash IS NOT NULL);
CREATE INDEX ix_ajustes_saldo_billetera_fecha ON public.ajustes_saldo USING btree (billetera_id, fecha);
CREATE INDEX ix_analisis_ia_creado_en_desc ON public.analisis_ia USING btree (creado_en DESC);
CREATE INDEX ix_analisis_ia_tipo_analisis ON public.analisis_ia USING btree (tipo_analisis);
CREATE INDEX ix_analisis_ia_usuario_id ON public.analisis_ia USING btree (usuario_id);
CREATE INDEX ix_analisis_ia_usuario_periodo_tipo ON public.analisis_ia USING btree (usuario_id, periodo_fin, tipo_analisis);
CREATE INDEX ix_billeteras_estado ON public.billeteras USING btree (estado);
CREATE INDEX ix_billeteras_usuario_id ON public.billeteras USING btree (usuario_id);
CREATE INDEX ix_calibraciones_usuario_usuario_id ON public.calibraciones_usuario USING btree (usuario_id);
CREATE INDEX ix_codigos_verificacion_codigo ON public.codigos_verificacion USING btree (codigo);
CREATE INDEX ix_codigos_verificacion_codigo_tipo ON public.codigos_verificacion USING btree (codigo, tipo);
CREATE INDEX ix_codigos_verificacion_consumido ON public.codigos_verificacion USING btree (consumido);
CREATE INDEX ix_codigos_verificacion_expiracion ON public.codigos_verificacion USING btree (expiracion);
CREATE INDEX ix_codigos_verificacion_identificador ON public.codigos_verificacion USING btree (identificador);
CREATE INDEX ix_codigos_verificacion_tipo ON public.codigos_verificacion USING btree (tipo);
CREATE INDEX ix_codigos_verificacion_tipo_identificador ON public.codigos_verificacion USING btree (tipo, identificador);
CREATE INDEX ix_config_notif_whatsapp_hora ON public.configuracion_notificaciones USING btree (whatsapp_hora_envio);
CREATE INDEX ix_conversaciones_wpp_fecha ON public.conversaciones_wpp USING btree (fecha);
CREATE INDEX ix_conversaciones_wpp_usuario_fecha ON public.conversaciones_wpp USING btree (usuario_id, fecha DESC);
CREATE INDEX ix_conversaciones_wpp_wamid ON public.conversaciones_wpp USING btree (wamid);
CREATE INDEX ix_correcciones_importacion_banco_tipo_correccion ON public.correcciones_importacion USING btree (banco, tipo_correccion);
CREATE INDEX ix_cotizaciones_dolar_tipo_fecha ON public.cotizaciones_dolar USING btree (tipo, fecha);
CREATE INDEX ix_cuotas_grupo_id ON public.cuotas USING btree (grupo_id);
CREATE INDEX ix_cuotas_grupo_vencimiento ON public.cuotas USING btree (grupo_id, fecha_vencimiento) WHERE (pagada = false);
CREATE INDEX ix_cuotas_pagada_vencimiento ON public.cuotas USING btree (pagada, fecha_vencimiento);
CREATE INDEX ix_cuotas_transaccion_pago_id ON public.cuotas USING btree (transaccion_pago_id) WHERE (transaccion_pago_id IS NOT NULL);
CREATE INDEX ix_decisiones_patrones_usuario_id ON public.decisiones_patrones USING btree (usuario_id);
CREATE INDEX ix_ejecuciones_jobs_inicio ON public.ejecuciones_jobs USING btree (inicio);
CREATE INDEX ix_ejecuciones_jobs_job_id ON public.ejecuciones_jobs USING btree (job_id);
CREATE INDEX ix_eventos_actualizacion_created_at ON public.eventos_actualizacion USING btree (created_at);
CREATE INDEX ix_eventos_actualizacion_usuario_created ON public.eventos_actualizacion USING btree (usuario_id, created_at);
CREATE INDEX ix_eventos_actualizacion_usuario_id ON public.eventos_actualizacion USING btree (usuario_id);
CREATE INDEX ix_facturas_usuario_estado_vencimiento ON public.facturas USING btree (usuario_id, estado, fecha_vencimiento);
CREATE INDEX ix_fechas_resumen_tarjeta_tarjeta_id ON public.fechas_resumen_tarjeta USING btree (tarjeta_id);
CREATE INDEX ix_feriados_ar_anio ON public.feriados_ar USING btree (anio);
CREATE UNIQUE INDEX ix_feriados_ar_fecha ON public.feriados_ar USING btree (fecha);
CREATE INDEX ix_grupos_cuotas_tarjeta_usuario ON public.grupos_cuotas USING btree (tarjeta_id, usuario_id);
CREATE INDEX ix_historial_perfiles_periodo_inicio ON public.historial_perfiles_financieros USING btree (periodo_inicio);
CREATE INDEX ix_historial_perfiles_usuario_id ON public.historial_perfiles_financieros USING btree (usuario_id);
CREATE INDEX ix_historial_suscripcion_id_fecha ON public.historial_suscripciones USING btree (suscripcion_id, vigente_desde DESC);
CREATE INDEX ix_memoria_comercios_usuario_id ON public.memoria_comercios USING btree (usuario_id);
CREATE INDEX ix_mensajes_salientes_wpp_fecha ON public.mensajes_salientes_wpp USING btree (fecha);
CREATE INDEX ix_mensajes_salientes_wpp_telefono_hash ON public.mensajes_salientes_wpp USING btree (telefono_hash);
CREATE INDEX ix_mensajes_salientes_wpp_usuario_id ON public.mensajes_salientes_wpp USING btree (usuario_id);
CREATE INDEX ix_mensajes_salientes_wpp_wamid_entrante ON public.mensajes_salientes_wpp USING btree (wamid_entrante);
CREATE INDEX ix_mensajes_whatsapp_procesados_fecha_recepcion ON public.mensajes_whatsapp_procesados USING btree (fecha_recepcion);
CREATE UNIQUE INDEX ix_mensajes_whatsapp_procesados_wamid ON public.mensajes_whatsapp_procesados USING btree (wamid);
CREATE INDEX ix_metas_estado ON public.metas USING btree (estado);
CREATE INDEX ix_metas_usuario_id ON public.metas USING btree (usuario_id);
CREATE INDEX ix_movimientos_meta_billetera_id ON public.movimientos_meta USING btree (billetera_id);
CREATE INDEX ix_movimientos_meta_meta_id ON public.movimientos_meta USING btree (meta_id);
CREATE INDEX ix_notificaciones_usuario_activas ON public.notificaciones USING btree (usuario_id, leida, archivada);
CREATE INDEX ix_notificaciones_usuario_grupo ON public.notificaciones USING btree (usuario_id, grupo_agrupacion);
CREATE INDEX ix_notificaciones_usuario_no_leidas ON public.notificaciones USING btree (usuario_id, leida, archivada, silenciada_hasta);
CREATE INDEX ix_notificaciones_whatsapp_pendientes ON public.notificaciones USING btree (usuario_id, canal_whatsapp, enviada_whatsapp);
CREATE INDEX ix_pagos_saldo_arrastrado_saldo_id ON public.pagos_saldo_arrastrado USING btree (saldo_arrastrado_id);
CREATE INDEX ix_pagos_saldo_arrastrado_tx_id ON public.pagos_saldo_arrastrado USING btree (transaccion_pago_id);
CREATE INDEX ix_presupuestos_categorias_categoria_id ON public.presupuestos_categorias USING btree (categoria_id);
CREATE INDEX ix_presupuestos_categorias_presupuesto_id ON public.presupuestos_categorias USING btree (presupuesto_id);
CREATE INDEX ix_presupuestos_categorias_subcategoria_id ON public.presupuestos_categorias USING btree (subcategoria_id);
CREATE INDEX ix_presupuestos_estado ON public.presupuestos USING btree (estado);
CREATE INDEX ix_presupuestos_usuario_id ON public.presupuestos USING btree (usuario_id);
CREATE INDEX ix_rate_limits_accion ON public.rate_limits USING btree (accion);
CREATE INDEX ix_rate_limits_expira_en ON public.rate_limits USING btree (expira_en);
CREATE INDEX ix_rate_limits_identificador ON public.rate_limits USING btree (identificador);
CREATE INDEX ix_rendimientos_billetera_billetera_id ON public.rendimientos_billetera USING btree (billetera_id);
CREATE INDEX ix_rendimientos_billetera_fecha ON public.rendimientos_billetera USING btree (fecha);
CREATE INDEX ix_saldos_arrastrados_tarjeta_estado ON public.saldos_arrastrados_tarjeta USING btree (tarjeta_id, estado);
CREATE INDEX ix_saldos_arrastrados_tarjeta_id ON public.saldos_arrastrados_tarjeta USING btree (tarjeta_id);
CREATE INDEX ix_saldos_arrastrados_transaccion_origen ON public.saldos_arrastrados_tarjeta USING btree (transaccion_origen_id);
CREATE INDEX ix_saldos_arrastrados_vencimiento ON public.saldos_arrastrados_tarjeta USING btree (fecha_vencimiento_resumen);
CREATE INDEX ix_subcategorias_categoria_id ON public.subcategorias USING btree (categoria_id);
CREATE INDEX ix_suscripciones_usuario_estado ON public.suscripciones USING btree (usuario_id, estado);
CREATE INDEX ix_tarjetas_credito_billetera_id ON public.tarjetas_credito USING btree (billetera_id);
CREATE INDEX ix_tarjetas_credito_estado ON public.tarjetas_credito USING btree (estado);
CREATE INDEX ix_tarjetas_credito_usuario_id ON public.tarjetas_credito USING btree (usuario_id);
CREATE INDEX ix_tasas_entidades_clave_fecha ON public.tasas_entidades USING btree (clave, fecha_dato);
CREATE INDEX ix_transacciones_billetera_id ON public.transacciones USING btree (billetera_id);
CREATE INDEX ix_transacciones_categoria_id ON public.transacciones USING btree (categoria_id);
CREATE INDEX ix_transacciones_estado_verificacion ON public.transacciones USING btree (estado_verificacion);
CREATE INDEX ix_transacciones_importacion_id ON public.transacciones USING btree (importacion_id);
CREATE INDEX ix_transacciones_movimiento_meta_id ON public.transacciones USING btree (movimiento_meta_id) WHERE (movimiento_meta_id IS NOT NULL);
CREATE INDEX ix_transacciones_pago_origen_id ON public.transacciones USING btree (pago_origen_id) WHERE (pago_origen_id IS NOT NULL);
CREATE INDEX ix_transacciones_pago_resumen_vencimiento ON public.transacciones USING btree (tarjeta_id, pago_resumen_vencimiento) WHERE (pago_resumen_vencimiento IS NOT NULL);
CREATE INDEX ix_transacciones_reintegro_de_id ON public.transacciones USING btree (reintegro_de_id) WHERE (reintegro_de_id IS NOT NULL);
CREATE INDEX ix_transacciones_subcategoria_id ON public.transacciones USING btree (subcategoria_id);
CREATE INDEX ix_transacciones_suscripcion_id ON public.transacciones USING btree (suscripcion_id) WHERE (suscripcion_id IS NOT NULL);
CREATE INDEX ix_transacciones_tarjeta_id ON public.transacciones USING btree (tarjeta_id);
CREATE INDEX ix_transacciones_usuario_fecha ON public.transacciones USING btree (usuario_id, fecha);
CREATE INDEX ix_transacciones_usuario_fecha_creacion ON public.transacciones USING btree (usuario_id, fecha_creacion DESC);
CREATE INDEX ix_transacciones_usuario_id ON public.transacciones USING btree (usuario_id);
CREATE INDEX ix_transacciones_usuario_tipo_fecha ON public.transacciones USING btree (usuario_id, tipo, fecha);
CREATE INDEX ix_transferencias_internas_billetera_destino_id ON public.transferencias_internas USING btree (billetera_destino_id);
CREATE INDEX ix_transferencias_internas_billetera_origen_id ON public.transferencias_internas USING btree (billetera_origen_id);
CREATE INDEX ix_transferencias_internas_transaccion_comision_id ON public.transferencias_internas USING btree (transaccion_comision_id) WHERE (transaccion_comision_id IS NOT NULL);
CREATE INDEX ix_transferencias_internas_usuario_fecha ON public.transferencias_internas USING btree (usuario_id, fecha);
CREATE UNIQUE INDEX ix_user_refresh_tokens_token_id ON public.user_refresh_tokens USING btree (token_id);
CREATE INDEX ix_user_refresh_tokens_usuario_revocado_exp ON public.user_refresh_tokens USING btree (usuario_id, revocado, fecha_expiracion);
CREATE INDEX ix_uso_ia_fecha ON public.uso_ia USING btree (fecha);
CREATE INDEX ix_uso_ia_usuario_id ON public.uso_ia USING btree (usuario_id);
CREATE INDEX ix_usuarios_telefono_normalizado ON public.usuarios USING btree (telefono_normalizado);
CREATE UNIQUE INDEX uq_saldos_arrastrados_tarjeta_resumen_moneda_activo ON public.saldos_arrastrados_tarjeta USING btree (tarjeta_id, fecha_vencimiento_resumen, moneda) WHERE (estado = 'activo'::public.estado_saldo_arrastrado_enum);
ALTER TABLE ONLY public.ajustes_saldo
    ADD CONSTRAINT ajustes_saldo_billetera_id_fkey FOREIGN KEY (billetera_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.analisis_ia
    ADD CONSTRAINT analisis_ia_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.billeteras
    ADD CONSTRAINT billeteras_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.calibraciones_usuario
    ADD CONSTRAINT calibraciones_usuario_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.categorias_excluidas
    ADD CONSTRAINT categorias_excluidas_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.categorias_excluidas
    ADD CONSTRAINT categorias_excluidas_subcategoria_id_fkey FOREIGN KEY (subcategoria_id) REFERENCES public.subcategorias(id);
ALTER TABLE ONLY public.categorias_excluidas
    ADD CONSTRAINT categorias_excluidas_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.configuracion_notificaciones
    ADD CONSTRAINT configuracion_notificaciones_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.conversaciones_wpp
    ADD CONSTRAINT conversaciones_wpp_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.correcciones_importacion
    ADD CONSTRAINT correcciones_importacion_importacion_id_fkey FOREIGN KEY (importacion_id) REFERENCES public.importaciones_resumen(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.cuotas
    ADD CONSTRAINT cuotas_grupo_id_fkey FOREIGN KEY (grupo_id) REFERENCES public.grupos_cuotas(id);
ALTER TABLE ONLY public.cuotas
    ADD CONSTRAINT cuotas_transaccion_id_fkey FOREIGN KEY (transaccion_id) REFERENCES public.transacciones(id);
ALTER TABLE ONLY public.cuotas
    ADD CONSTRAINT cuotas_transaccion_pago_id_fkey FOREIGN KEY (transaccion_pago_id) REFERENCES public.transacciones(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.decisiones_patrones
    ADD CONSTRAINT decisiones_patrones_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.eventos_actualizacion
    ADD CONSTRAINT eventos_actualizacion_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.facturas
    ADD CONSTRAINT facturas_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.facturas
    ADD CONSTRAINT facturas_subcategoria_id_fkey FOREIGN KEY (subcategoria_id) REFERENCES public.subcategorias(id);
ALTER TABLE ONLY public.facturas
    ADD CONSTRAINT facturas_transaccion_id_fkey FOREIGN KEY (transaccion_id) REFERENCES public.transacciones(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.facturas
    ADD CONSTRAINT facturas_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.fechas_resumen_tarjeta
    ADD CONSTRAINT fechas_resumen_tarjeta_tarjeta_id_fkey FOREIGN KEY (tarjeta_id) REFERENCES public.tarjetas_credito(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.suscripciones
    ADD CONSTRAINT fk_suscripciones_subcategoria_id FOREIGN KEY (subcategoria_id) REFERENCES public.subcategorias(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT fk_transacciones_importacion_id FOREIGN KEY (importacion_id) REFERENCES public.importaciones_resumen(id);
ALTER TABLE ONLY public.grupos_cuotas
    ADD CONSTRAINT grupos_cuotas_tarjeta_id_fkey FOREIGN KEY (tarjeta_id) REFERENCES public.tarjetas_credito(id);
ALTER TABLE ONLY public.grupos_cuotas
    ADD CONSTRAINT grupos_cuotas_transaccion_padre_id_fkey FOREIGN KEY (transaccion_padre_id) REFERENCES public.transacciones(id);
ALTER TABLE ONLY public.grupos_cuotas
    ADD CONSTRAINT grupos_cuotas_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.historial_perfiles_financieros
    ADD CONSTRAINT historial_perfiles_financieros_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.historial_suscripciones
    ADD CONSTRAINT historial_suscripciones_suscripcion_id_fkey FOREIGN KEY (suscripcion_id) REFERENCES public.suscripciones(id);
ALTER TABLE ONLY public.importaciones_resumen
    ADD CONSTRAINT importaciones_resumen_admin_id_fkey FOREIGN KEY (admin_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.importaciones_resumen
    ADD CONSTRAINT importaciones_resumen_tarjeta_id_fkey FOREIGN KEY (tarjeta_id) REFERENCES public.tarjetas_credito(id);
ALTER TABLE ONLY public.importaciones_resumen
    ADD CONSTRAINT importaciones_resumen_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.memoria_comercios
    ADD CONSTRAINT memoria_comercios_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.memoria_comercios
    ADD CONSTRAINT memoria_comercios_subcategoria_id_fkey FOREIGN KEY (subcategoria_id) REFERENCES public.subcategorias(id);
ALTER TABLE ONLY public.memoria_comercios
    ADD CONSTRAINT memoria_comercios_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.mensajes_salientes_wpp
    ADD CONSTRAINT mensajes_salientes_wpp_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.metas
    ADD CONSTRAINT metas_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.movimientos_meta
    ADD CONSTRAINT movimientos_meta_billetera_id_fkey FOREIGN KEY (billetera_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.movimientos_meta
    ADD CONSTRAINT movimientos_meta_meta_id_fkey FOREIGN KEY (meta_id) REFERENCES public.metas(id);
ALTER TABLE ONLY public.notificaciones
    ADD CONSTRAINT notificaciones_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.pagos_saldo_arrastrado
    ADD CONSTRAINT pagos_saldo_arrastrado_saldo_arrastrado_id_fkey FOREIGN KEY (saldo_arrastrado_id) REFERENCES public.saldos_arrastrados_tarjeta(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.pagos_saldo_arrastrado
    ADD CONSTRAINT pagos_saldo_arrastrado_transaccion_pago_id_fkey FOREIGN KEY (transaccion_pago_id) REFERENCES public.transacciones(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.perfiles_financieros
    ADD CONSTRAINT perfiles_financieros_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.periodos_presupuesto
    ADD CONSTRAINT periodos_presupuesto_presupuesto_id_fkey FOREIGN KEY (presupuesto_id) REFERENCES public.presupuestos(id);
ALTER TABLE ONLY public.presupuestos_categorias
    ADD CONSTRAINT presupuestos_categorias_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.presupuestos_categorias
    ADD CONSTRAINT presupuestos_categorias_presupuesto_id_fkey FOREIGN KEY (presupuesto_id) REFERENCES public.presupuestos(id);
ALTER TABLE ONLY public.presupuestos_categorias
    ADD CONSTRAINT presupuestos_categorias_subcategoria_id_fkey FOREIGN KEY (subcategoria_id) REFERENCES public.subcategorias(id);
ALTER TABLE ONLY public.presupuestos
    ADD CONSTRAINT presupuestos_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.rendimientos_billetera
    ADD CONSTRAINT rendimientos_billetera_billetera_id_fkey FOREIGN KEY (billetera_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.saldos_arrastrados_tarjeta
    ADD CONSTRAINT saldos_arrastrados_tarjeta_tarjeta_id_fkey FOREIGN KEY (tarjeta_id) REFERENCES public.tarjetas_credito(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.saldos_arrastrados_tarjeta
    ADD CONSTRAINT saldos_arrastrados_tarjeta_transaccion_origen_id_fkey FOREIGN KEY (transaccion_origen_id) REFERENCES public.transacciones(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.subcategorias
    ADD CONSTRAINT subcategorias_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.suscripciones
    ADD CONSTRAINT suscripciones_billetera_id_fkey FOREIGN KEY (billetera_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.suscripciones
    ADD CONSTRAINT suscripciones_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.suscripciones
    ADD CONSTRAINT suscripciones_tarjeta_id_fkey FOREIGN KEY (tarjeta_id) REFERENCES public.tarjetas_credito(id);
ALTER TABLE ONLY public.suscripciones
    ADD CONSTRAINT suscripciones_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.tarjetas_credito
    ADD CONSTRAINT tarjetas_credito_billetera_id_fkey FOREIGN KEY (billetera_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.tarjetas_credito
    ADD CONSTRAINT tarjetas_credito_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_billetera_id_fkey FOREIGN KEY (billetera_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_categoria_id_fkey FOREIGN KEY (categoria_id) REFERENCES public.categorias(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_grupo_cuotas_id_fkey FOREIGN KEY (grupo_cuotas_id) REFERENCES public.grupos_cuotas(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_movimiento_meta_id_fkey FOREIGN KEY (movimiento_meta_id) REFERENCES public.movimientos_meta(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_pago_origen_id_fkey FOREIGN KEY (pago_origen_id) REFERENCES public.transacciones(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_reintegro_de_id_fkey FOREIGN KEY (reintegro_de_id) REFERENCES public.transacciones(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_subcategoria_id_fkey FOREIGN KEY (subcategoria_id) REFERENCES public.subcategorias(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_suscripcion_id_fkey FOREIGN KEY (suscripcion_id) REFERENCES public.suscripciones(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_tarjeta_id_fkey FOREIGN KEY (tarjeta_id) REFERENCES public.tarjetas_credito(id);
ALTER TABLE ONLY public.transacciones
    ADD CONSTRAINT transacciones_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.transferencias_internas
    ADD CONSTRAINT transferencias_internas_billetera_destino_id_fkey FOREIGN KEY (billetera_destino_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.transferencias_internas
    ADD CONSTRAINT transferencias_internas_billetera_origen_id_fkey FOREIGN KEY (billetera_origen_id) REFERENCES public.billeteras(id);
ALTER TABLE ONLY public.transferencias_internas
    ADD CONSTRAINT transferencias_internas_transaccion_comision_id_fkey FOREIGN KEY (transaccion_comision_id) REFERENCES public.transacciones(id) ON DELETE SET NULL;
ALTER TABLE ONLY public.transferencias_internas
    ADD CONSTRAINT transferencias_internas_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id);
ALTER TABLE ONLY public.user_refresh_tokens
    ADD CONSTRAINT user_refresh_tokens_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE CASCADE;
ALTER TABLE ONLY public.uso_ia
    ADD CONSTRAINT uso_ia_usuario_id_fkey FOREIGN KEY (usuario_id) REFERENCES public.usuarios(id) ON DELETE SET NULL;
CREATE POLICY "Allow backend full access" ON public.user_refresh_tokens USING (true) WITH CHECK (true);
ALTER TABLE public.ajustes_saldo ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analisis_ia ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.billeteras ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.calibraciones_usuario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.categorias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.categorias_excluidas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.codigos_verificacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.configuracion_notificaciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversaciones_wpp ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.correcciones_importacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cotizaciones_dolar ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cuotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decisiones_patrones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.eventos_actualizacion ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.facturas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feriados_ar ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.grupos_cuotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.historial_perfiles_financieros ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.historial_suscripciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.importaciones_resumen ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ipc_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memoria_comercios ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mensajes_whatsapp_procesados ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.metas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.movimientos_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notificaciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pagos_saldo_arrastrado ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.perfiles_financieros ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.periodos_presupuesto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.presupuestos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.presupuestos_categorias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_limits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.saldos_arrastrados_tarjeta ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subcategorias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.suscripciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tarjetas_credito ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tasas_entidades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transacciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transferencias_internas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_refresh_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.usuarios ENABLE ROW LEVEL SECURITY;
