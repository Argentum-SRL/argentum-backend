from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date
from io import BytesIO
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.billetera import Billetera
from app.models.tarjeta_credito import TarjetaCredito
from app.models.usuario import Usuario
from app.services import (
    analisis_comparativa_service,
    analisis_excel_service,
    analisis_informes_service,
    analisis_proyeccion_service,
    analisis_resumen_service,
)
from app.utils.fecha import hoy_argentina

router = APIRouter(prefix="/analisis", tags=["analisis"])


def _parse_optional_uuid(val: str | None, field_name: str) -> uuid.UUID | None:
    if not val or not val.strip() or val.strip() == "todos":
        return None
    try:
        return uuid.UUID(val.strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="No se encontró lo que elegiste. Volvé a elegirlo.",
        )


@router.get("/resumen")
def get_resumen_analisis(
    moneda_modo: str = Query("solo_ars", description="solo_ars | solo_usd | consolidado_ars | consolidado_usd"),
    ciclos: int = Query(6, ge=1, le=12, description="Cantidad de ciclos hacia atrás"),
    billetera_id: str | None = Query(None),
    tarjeta_id: str | None = Query(None),
    categoria_id: str | None = Query(None),
    naturaleza: str | None = Query("todos", description="todos | variables | fijos | cuotas"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    b_uuid = _parse_optional_uuid(billetera_id, "billetera_id")
    t_uuid = _parse_optional_uuid(tarjeta_id, "tarjeta_id")
    c_uuid = _parse_optional_uuid(categoria_id, "categoria_id")

    return analisis_resumen_service.obtener_resumen_analisis(
        db=db,
        usuario=current_user,
        moneda_modo=moneda_modo,
        cantidad_ciclos=ciclos,
        billetera_id=b_uuid,
        tarjeta_id=t_uuid,
        categoria_id=c_uuid,
        naturaleza=naturaleza,
    )


@router.get("/drilldown-movimientos")
def get_drilldown_movimientos(
    moneda_modo: str = Query("solo_ars"),
    ciclos: int = Query(6, ge=1, le=12),
    categoria_id: str | None = Query(None),
    subcategoria_id: str | None = Query(None),
    billetera_id: str | None = Query(None),
    tarjeta_id: str | None = Query(None),
    naturaleza: str | None = Query("todos"),
    limit: int = Query(40, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    b_uuid = _parse_optional_uuid(billetera_id, "billetera_id")
    t_uuid = _parse_optional_uuid(tarjeta_id, "tarjeta_id")

    items = analisis_resumen_service.obtener_movimientos_drilldown(
        db=db,
        usuario=current_user,
        moneda_modo=moneda_modo,
        cantidad_ciclos=ciclos,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id,
        billetera_id=b_uuid,
        tarjeta_id=t_uuid,
        naturaleza=naturaleza,
        limit=limit,
    )
    return {"items": items}


@router.get("/comparativa-ciclos")
def get_comparativa_ciclos(
    moneda_modo: str = Query("solo_ars"),
    ciclo_a: str | None = Query(None, description="Clave del Ciclo A (por defecto ciclo actual)"),
    ciclo_b: str | None = Query(None, description="Clave del Ciclo B (por defecto ciclo anterior)"),
    billetera_id: str | None = Query(None),
    tarjeta_id: str | None = Query(None),
    naturaleza: str | None = Query("todos"),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    b_uuid = _parse_optional_uuid(billetera_id, "billetera_id")
    t_uuid = _parse_optional_uuid(tarjeta_id, "tarjeta_id")

    return analisis_comparativa_service.obtener_comparativa_ciclos(
        db=db,
        usuario=current_user,
        moneda_modo=moneda_modo,
        ciclo_a_clave=ciclo_a,
        ciclo_b_clave=ciclo_b,
        billetera_id=b_uuid,
        tarjeta_id=t_uuid,
        naturaleza=naturaleza,
    )


@router.get("/proyeccion-compromisos")
def get_proyeccion_compromisos(
    moneda_modo: str = Query("solo_ars"),
    meses: int = Query(6, ge=3, le=12),
    billetera_id: str | None = Query(None),
    tarjeta_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    b_uuid = _parse_optional_uuid(billetera_id, "billetera_id")
    t_uuid = _parse_optional_uuid(tarjeta_id, "tarjeta_id")

    return analisis_proyeccion_service.obtener_proyeccion_compromisos(
        db=db,
        usuario=current_user,
        moneda_modo=moneda_modo,
        meses=meses,
        billetera_id=b_uuid,
        tarjeta_id=t_uuid,
    )


class InformeCreateSchema(BaseModel):
    nombre: str = Field(..., min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=300)
    icono: str = Field(default="pie-chart", max_length=40)
    es_favorito: bool = Field(default=False)
    configuracion: dict[str, Any] = Field(default_factory=dict)


class InformeUpdateSchema(BaseModel):
    nombre: str | None = Field(default=None, max_length=120)
    descripcion: str | None = Field(default=None, max_length=300)
    icono: str | None = Field(default=None, max_length=40)
    es_favorito: bool | None = None
    configuracion: dict[str, Any] | None = None


@router.get("/informes")
def list_informes(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    return {"items": analisis_informes_service.listar_informes_usuario(db, current_user)}


@router.post("/informes")
def create_informe(
    payload: InformeCreateSchema,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    return analisis_informes_service.crear_informe_usuario(
        db=db,
        usuario=current_user,
        nombre=payload.nombre,
        descripcion=payload.descripcion,
        icono=payload.icono,
        es_favorito=payload.es_favorito,
        configuracion=payload.configuracion,
    )


@router.get("/informes/{informe_id}")
def get_informe(
    informe_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    inf_uuid = _parse_optional_uuid(informe_id, "informe_id")
    if not inf_uuid:
        raise HTTPException(status_code=400, detail="Falta elegir el informe.")
    inf = analisis_informes_service.obtener_informe_usuario(db, current_user, inf_uuid)
    if not inf:
        raise HTTPException(status_code=404, detail="No se encontró ese informe.")
    return inf


@router.put("/informes/{informe_id}")
def update_informe(
    informe_id: str,
    payload: InformeUpdateSchema,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    inf_uuid = _parse_optional_uuid(informe_id, "informe_id")
    if not inf_uuid:
        raise HTTPException(status_code=400, detail="Falta elegir el informe.")
    actualizado = analisis_informes_service.actualizar_informe_usuario(
        db=db,
        usuario=current_user,
        informe_id=inf_uuid,
        datos=payload.model_dump(exclude_unset=True),
    )
    if not actualizado:
        raise HTTPException(status_code=404, detail="No se encontró ese informe.")
    return actualizado


@router.delete("/informes/{informe_id}")
def delete_informe(
    informe_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    inf_uuid = _parse_optional_uuid(informe_id, "informe_id")
    if not inf_uuid:
        raise HTTPException(status_code=400, detail="Falta elegir el informe.")
    ok = analisis_informes_service.eliminar_informe_usuario(db, current_user, inf_uuid)
    if not ok:
        raise HTTPException(status_code=404, detail="No se encontró ese informe.")
    return {"ok": True}


def _armar_nombre_archivo_excel(titulo: str | None, hoy: date) -> str:
    """Genera el nombre de archivo estandarizado con formato argentum_{slug}_{AAAAMMDD}.xlsx."""
    if not titulo or not titulo.strip():
        slug = "analisis"
    else:
        s = unicodedata.normalize("NFKD", titulo)
        s = "".join(c for c in s if not unicodedata.combining(c))
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", "_", s)
        slug = re.sub(r"_+", "_", s).strip("_")
        if not slug:
            slug = "analisis"
    fecha_str = hoy.strftime("%Y%m%d")
    return f"argentum_{slug}_{fecha_str}.xlsx"


@router.get("/exportar-excel")
def exportar_excel(
    moneda_modo: str = Query("solo_ars"),
    ciclos: int = Query(6, ge=1, le=12),
    billetera_id: str | None = Query(None),
    tarjeta_id: str | None = Query(None),
    categoria_id: str | None = Query(None),
    naturaleza: str | None = Query("todos"),
    titulo: str | None = Query(None),
    bloques: str | None = Query(None),
    titulos: str | None = Query(None),
    ciclo_a: str | None = Query(None),
    ciclo_b: str | None = Query(None),
    meses: int = Query(6),
    hoy: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> StreamingResponse:
    b_uuid = _parse_optional_uuid(billetera_id, "billetera_id")
    t_uuid = _parse_optional_uuid(tarjeta_id, "tarjeta_id")
    c_uuid = _parse_optional_uuid(categoria_id, "categoria_id")

    if b_uuid:
        b = db.query(Billetera).filter(Billetera.id == b_uuid, Billetera.usuario_id == current_user.id).first()
        if not b:
            raise HTTPException(status_code=422, detail="No se encontró esa billetera.")

    if t_uuid:
        tc = db.query(TarjetaCredito).filter(TarjetaCredito.id == t_uuid, TarjetaCredito.usuario_id == current_user.id).first()
        if not tc:
            raise HTTPException(status_code=422, detail="No se encontró esa tarjeta.")

    ref_hoy = hoy or hoy_argentina()

    # Validación de meses de proyección
    if meses not in (6, 12):
        raise HTTPException(
            status_code=422,
            detail="Elegí 6 o 12 meses.",
        )

    # Validación de tipos de bloques solicitados
    valid_bloques = {
        "kpis",
        "evolucion_ciclos",
        "desglose_categorias",
        "comparativa_ciclos",
        "proyeccion_cuotas",
        "tabla_movimientos",
    }
    if bloques:
        bloques_items = [b.strip() for b in bloques.split(",") if b.strip()]
        for b_item in bloques_items:
            if b_item not in valid_bloques:
                raise HTTPException(
                    status_code=422,
                    detail="Ese bloque no existe. Elegí otro.",
                )

    # Validación de ciclos para comparativa
    ciclo_a_clave = None
    ciclo_b_clave = None
    if ciclo_a or ciclo_b:
        from app.services.analisis_ciclos_service import generar_ventana_ciclos

        ciclos_disp = generar_ventana_ciclos(current_user, ref_hoy, 12)

        if ciclo_a:
            c_match_a = next(
                (
                    c
                    for c in ciclos_disp
                    if c["clave"] == ciclo_a
                    or c["clave"].startswith(ciclo_a)
                    or c["etiqueta"].lower() == ciclo_a.lower()
                ),
                None,
            )
            if not c_match_a:
                raise HTTPException(
                    status_code=422,
                    detail="Ese ciclo no tiene datos. Elegí otro.",
                )
            ciclo_a_clave = c_match_a["clave"]

        if ciclo_b:
            c_match_b = next(
                (
                    c
                    for c in ciclos_disp
                    if c["clave"] == ciclo_b
                    or c["clave"].startswith(ciclo_b)
                    or c["etiqueta"].lower() == ciclo_b.lower()
                ),
                None,
            )
            if not c_match_b:
                raise HTTPException(
                    status_code=422,
                    detail="Ese ciclo no tiene datos. Elegí otro.",
                )
            ciclo_b_clave = c_match_b["clave"]

    contenido = analisis_excel_service.generar_excel_analisis(
        db=db,
        usuario=current_user,
        moneda_modo=moneda_modo,
        cantidad_ciclos=ciclos,
        billetera_id=b_uuid,
        tarjeta_id=t_uuid,
        categoria_id=c_uuid,
        naturaleza=naturaleza,
        titulo_informe=titulo,
        bloques=bloques,
        titulos=titulos,
        ciclo_a=ciclo_a_clave or ciclo_a,
        ciclo_b=ciclo_b_clave or ciclo_b,
        meses=meses,
        hoy=ref_hoy,
    )

    nombre_archivo = _armar_nombre_archivo_excel(titulo, ref_hoy)
    headers = {
        "Content-Disposition": f'attachment; filename="{nombre_archivo}"',
    }
    return StreamingResponse(
        BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

