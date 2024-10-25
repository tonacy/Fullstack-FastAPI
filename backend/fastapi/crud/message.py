from typing import List
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from backend.fastapi.models import Message
from backend.fastapi.schemas import MessageBase, MessageCreate
from backend.fastapi.crud.base import BaseService

class MessageService(BaseService):
    def create_message(self, message_data: MessageCreate) -> Message:
        db = self.get_sync_db()
        db_message = Message(**message_data.model_dump())
        db.add(db_message)
        db.commit()
        db.refresh(db_message)
        return db_message

    async def create_message_async(self, message_data: MessageCreate) -> Message:
        db = self.get_async_db()
        db_message = Message(**message_data.model_dump())
        db.add(db_message)
        await db.commit()
        await db.refresh(db_message)
        return db_message

    def get_messages(self, skip: int = 0, limit: int = 30) -> List[Message]:
        db = self.get_sync_db()
        return db.query(Message).offset(skip).limit(limit).all()

    def get_message(self, message_id: UUID) -> Message:
        db = self.get_sync_db()
        db_message = db.query(Message).filter(Message.id == message_id).first()
        if db_message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        return db_message

    def update_message(self, message_id: UUID, message_data: MessageBase) -> Message:
        db = self.get_sync_db()
        db_message = db.query(Message).filter(Message.id == message_id).first()
        if db_message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        for key, value in message_data.model_dump(exclude_unset=True).items():
            setattr(db_message, key, value)
        db.commit()
        db.refresh(db_message)
        return db_message

    def delete_message(self, message_id: UUID) -> Message:
        db = self.get_sync_db()
        db_message = db.query(Message).filter(Message.id == message_id).first()
        if db_message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        db.delete(db_message)
        db.commit()
        return db_message

async def create_message_dict_async(db: AsyncSession, data: dict):
    # If not, insert the new model asynchronously
    db_data = Message(**data)
    db.add(db_data)
    await db.commit()  
    await db.refresh(db_data)  
    return db_data