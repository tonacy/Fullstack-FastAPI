from typing import List
from uuid import UUID
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from backend.fastapi.dependencies.database import get_sync_db, get_async_db
from backend.fastapi.models import Message
from backend.fastapi.schemas import (
    MessageBase,
    MessageCreate,
)

class MessageService:
    def __init__(self, db_sync: Session = Depends(get_sync_db), db_async: AsyncSession = Depends(get_async_db)):
        self.db_sync = db_sync
        self.db_async = db_async

    def create_message(self, message_data: MessageCreate) -> Message:
        db_message = Message(**message_data.model_dump())
        self.db_sync.add(db_message)
        self.db_sync.commit()
        self.db_sync.refresh(db_message)
        return db_message
    
    async def create_message_async(self, message_data: MessageCreate) -> Message:
        db_message = Message(**message_data.model_dump())
        self.db_async.add(db_message)
        await self.db_async.commit()
        await self.db_async.refresh(db_message)
        return db_message

    def get_messages(self, skip: int = 0, limit: int = 30) -> List[Message]:
        return self.db_sync.query(Message).offset(skip).limit(limit).all()

    def get_message(self, message_id: UUID) -> Message:
        db_message = self.db_sync.query(Message).filter(Message.id == message_id).first()
        if db_message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        return db_message

    def update_message(self, message_id: UUID, message_data: MessageBase) -> Message:
        db_message = self.db_sync.query(Message).filter(Message.id == message_id).first()
        if db_message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        for key, value in message_data.model_dump(exclude_unset=True).items():
            setattr(db_message, key, value)
        self.db_sync.commit()
        self.db_sync.refresh(db_message)
        return db_message

    def delete_message(self, message_id: UUID) -> Message:
        db_message = self.db_sync.query(Message).filter(Message.id == message_id).first()
        if db_message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        self.db_sync.delete(db_message)
        self.db_sync.commit()
        return db_message

async def create_message_dict_async(db: AsyncSession, data: dict):
    # If not, insert the new model asynchronously
    db_data = Message(**data)
    db.add(db_data)
    await db.commit()  
    await db.refresh(db_data)  
    return db_data