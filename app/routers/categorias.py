from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.models.categoria import Categoria, EstadoCategoria
from app.models.subcategoria import Subcategoria, EstadoSubcategoria
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
    Lista las categorías globales (desde cache) y las personalizadas del usuario.
    """
    cats_globales, _ = categoria_service.obtener_categorias_globales(db)
    
    # Categorías personalizadas del usuario
    stmt = select(Categoria).where(
        Categoria.creador_id == current_user.id,
        Categoria.estado == EstadoCategoria.ACTIVA
    )
    cats_personales = db.execute(stmt).scalars().all()
    
    return [*cats_globales, *cats_personales]

@router.get("/{categoria_id}/subcategorias", response_model=List[SubcategoriaRead])
def list_subcategorias(
    categoria_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las subcategorías de una categoría específica (globales desde cache + personalizadas).
    """
    _, subs_globales = categoria_service.obtener_categorias_globales(db)
    subs_glob_filtradas = [s for s in subs_globales if str(s["categoria_id"]) == str(categoria_id)]
    
    stmt = select(Subcategoria).where(
        Subcategoria.categoria_id == categoria_id,
        Subcategoria.creador_id == current_user.id,
        Subcategoria.estado == EstadoSubcategoria.ACTIVA
    )
    subs_personales = db.execute(stmt).scalars().all()
    
    return [*subs_glob_filtradas, *subs_personales]

@router.get("/subcategorias", response_model=List[SubcategoriaRead])
def list_all_subcategorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista todas las subcategorías activas (globales desde cache + personales).
    """
    _, subs_globales = categoria_service.obtener_categorias_globales(db)
    
    stmt = select(Subcategoria).where(
        Subcategoria.creador_id == current_user.id,
        Subcategoria.estado == EstadoSubcategoria.ACTIVA
    )
    subs_personales = db.execute(stmt).scalars().all()
    
    return [*subs_globales, *subs_personales]


