from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.schemas.categoria import CategoriaRead
from app.schemas.subcategoria import SubcategoriaRead
from app.services import categoria_service

router = APIRouter(prefix="/categorias", tags=["categorias"])

@router.get("", response_model=List[CategoriaRead])
def list_categorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las categorías globales (desde cache).
    """
    cats_globales, _ = categoria_service.obtener_categorias_globales(db)
    return cats_globales

@router.get("/{categoria_id}/subcategorias", response_model=List[SubcategoriaRead])
def list_subcategorias(
    categoria_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las subcategorías de una categoría específica (globales desde cache).
    """
    _, subs_globales = categoria_service.obtener_categorias_globales(db)
    return [s for s in subs_globales if str(s["categoria_id"]) == str(categoria_id)]

@router.get("/subcategorias", response_model=List[SubcategoriaRead])
def list_all_subcategorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista todas las subcategorías activas (globales desde cache).
    """
    _, subs_globales = categoria_service.obtener_categorias_globales(db)
    return subs_globales


