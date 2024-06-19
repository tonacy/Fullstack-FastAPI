from typing import Optional
from pydantic import BaseModel
from uuid import UUID

class MessageBase(BaseModel):
    content: str

class MessageCreate(MessageBase):
    pass

class MessageSchema(MessageBase):
    id: UUID

    class Config:
        from_attributes = True