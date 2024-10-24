from contextlib import asynccontextmanager
from fastapi import FastAPI
from backend.fastapi.dependencies.database import init_db, AsyncSessionLocal
from backend.fastapi.crud.message import create_message_dict_async
from backend.data.init_data import models_data

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize the database connection
    init_db()

    # Insert the initial data
    async with AsyncSessionLocal() as db:
        try:
            for raw_data in models_data:
                await create_message_dict_async(db, raw_data)
        finally:
            await db.close()

    yield