from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.usuario import Usuario
from app.models.categoria import Categoria, EstadoCategoria
from app.models.subcategoria import Subcategoria
from app.schemas.categoria import CategoriaRead
from app.schemas.subcategoria import SubcategoriaRead

router = APIRouter(prefix="/categorias", tags=["categorias"])

@router.get("", response_model=List[CategoriaRead])
def list_categorias(
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las categorías globales y las personalizadas del usuario.
    """
    stmt = select(Categoria).where(
        or_(
            Categoria.es_global == True,
            Categoria.creador_id == current_user.id
        ),
        Categoria.estado == EstadoCategoria.ACTIVA
    )
    return db.execute(stmt).scalars().all()

@router.get("/{categoria_id}/subcategorias", response_model=List[SubcategoriaRead])
def list_subcategorias(
    categoria_id: str,
    db: Session = Depends(get_db),
    current_user: Usuario = Depends(get_current_user)
):
    """
    Lista las subcategorías de una categoría específica.
    """
    _ = current_user
    stmt = select(Subcategoria).where(Subcategoria.categoria_id == categoria_id)
    return db.execute(stmt).scalars().all()
