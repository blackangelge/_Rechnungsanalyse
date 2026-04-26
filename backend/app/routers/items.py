from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas.item import ItemCreate, ItemUpdate, ItemRead
from app import crud

router = APIRouter(prefix="/api/items", tags=["items"])


@router.get("", response_model=list[ItemRead])
def list_items(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    return crud.item.get_items(db, skip=skip, limit=limit)


@router.post("", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate, db: Session = Depends(get_db)):
    return crud.item.create_item(db, payload)


@router.get("/{item_id}", response_model=ItemRead)
def read_item(item_id: int, db: Session = Depends(get_db)):
    obj = crud.item.get_item(db, item_id)
    if obj is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@router.put("/{item_id}", response_model=ItemRead)
def update_item(item_id: int, payload: ItemUpdate, db: Session = Depends(get_db)):
    obj = crud.item.update_item(db, item_id, payload)
    if obj is None:
        raise HTTPException(status_code=404, detail="Item not found")
    return obj


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    if not crud.item.delete_item(db, item_id):
        raise HTTPException(status_code=404, detail="Item not found")
