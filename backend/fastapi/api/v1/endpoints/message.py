from typing import List
from uuid import UUID
from fastapi import status, APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from backend.fastapi.crud import MessageService
from backend.fastapi.dependencies.database import get_sync_db, get_async_db
from backend.fastapi.schemas import MessageBase, MessageCreate, MessageSchema

router = APIRouter()

@router.post("/messages/", response_model=MessageSchema, status_code=status.HTTP_201_CREATED)
def create_message(
    message_data: MessageCreate,
    db_sync: Session = Depends(get_sync_db)
):
    service = MessageService(db_sync=db_sync)
    return service.create_message(message_data)

@router.post("/messages/async", response_model=MessageSchema, status_code=status.HTTP_201_CREATED)
async def create_message_async(
    message_data: MessageCreate,
    db_async: AsyncSession = Depends(get_async_db)
):
    service = MessageService(db_async=db_async)
    return await service.create_message_async(message_data)

@router.get("/messages/", response_model=List[MessageSchema], status_code=status.HTTP_200_OK)
def get_messages(
    skip: int = 0,
    limit: int = 30,
    db_sync: Session = Depends(get_sync_db)
):
    service = MessageService(db_sync=db_sync)
    return service.get_messages(skip, limit)

@router.get("/messages/{message_id}", response_model=MessageSchema, status_code=status.HTTP_200_OK)
def get_message(
    message_id: UUID,
    db_sync: Session = Depends(get_sync_db)
):
    service = MessageService(db_sync=db_sync)
    return service.get_message(message_id)

@router.put("/messages/{message_id}", response_model=MessageSchema, status_code=status.HTTP_200_OK)
def update_message(
    message_id: UUID,
    message_data: MessageBase,
    db_sync: Session = Depends(get_sync_db)
):
    service = MessageService(db_sync=db_sync)
    return service.update_message(message_id, message_data)

@router.delete("/messages/{message_id}", response_model=MessageSchema, status_code=status.HTTP_200_OK)
def delete_message(
    message_id: UUID,
    db_sync: Session = Depends(get_sync_db)
):
    service = MessageService(db_sync=db_sync)
    return service.delete_message(message_id)