from __future__ import annotations

import uuid
from io import BytesIO
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.services import analisis_service

router = APIRouter(prefix="/analisis", tags=["analisis"])


def _parse_optional_uuid(val: str | None, field_name: str) -> uuid.UUID | None:
    if not val or not val.strip() or val.strip() == "todos":
        return None
    try:
        return uuid.UUID(val.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"ID inválido para {field_name}")


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

    return analisis_service.obtener_resumen_analisis(
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

    items = analisis_service.obtener_movimientos_drilldown(
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

    return analisis_service.obtener_comparativa_ciclos(
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

    return analisis_service.obtener_proyeccion_compromisos(
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
    return {"items": analisis_service.listar_informes_usuario(db, current_user)}


@router.post("/informes")
def create_informe(
    payload: InformeCreateSchema,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    return analisis_service.crear_informe_usuario(
        db=db,
        usuario=current_user,
        nombre=payload.nombre,
        descripcion=payload.descripcion,
        icono=payload.icono,
        es_favorito=payload.es_favorito,
        configuracion=payload.configuracion,
    )


@router.put("/informes/{informe_id}")
def update_informe(
    informe_id: str,
    payload: InformeUpdateSchema,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    inf_uuid = _parse_optional_uuid(informe_id, "informe_id")
    if not inf_uuid:
        raise HTTPException(status_code=400, detail="ID de informe requerido")
    actualizado = analisis_service.actualizar_informe_usuario(
        db=db,
        usuario=current_user,
        informe_id=inf_uuid,
        datos=payload.model_dump(exclude_unset=True),
    )
    if not actualizado:
        raise HTTPException(status_code=404, detail="Informe no encontrado")
    return actualizado


@router.delete("/informes/{informe_id}")
def delete_informe(
    informe_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> Any:
    inf_uuid = _parse_optional_uuid(informe_id, "informe_id")
    if not inf_uuid:
        raise HTTPException(status_code=400, detail="ID de informe requerido")
    ok = analisis_service.eliminar_informe_usuario(db, current_user, inf_uuid)
    if not ok:
        raise HTTPException(status_code=404, detail="Informe no encontrado")
    return {"ok": True}


@router.get("/exportar-excel")
def exportar_excel(
    moneda_modo: str = Query("solo_ars"),
    ciclos: int = Query(6, ge=1, le=12),
    billetera_id: str | None = Query(None),
    tarjeta_id: str | None = Query(None),
    naturaleza: str | None = Query("todos"),
    titulo: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
) -> StreamingResponse:
    b_uuid = _parse_optional_uuid(billetera_id, "billetera_id")
    t_uuid = _parse_optional_uuid(tarjeta_id, "tarjeta_id")

    contenido = analisis_service.generar_excel_analisis(
        db=db,
        usuario=current_user,
        moneda_modo=moneda_modo,
        cantidad_ciclos=ciclos,
        billetera_id=b_uuid,
        tarjeta_id=t_uuid,
        naturaleza=naturaleza,
        titulo_informe=titulo,
    )
    headers = {
        "Content-Disposition": 'attachment; filename="argentum_analisis.xlsx"',
    }
    return StreamingResponse(
        BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers=headers,
    )

