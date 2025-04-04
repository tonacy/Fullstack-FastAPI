import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from backend.fastapi.dependencies.database import init_db, AsyncSessionLocal
from backend.fastapi.crud.message import create_message_dict_async
from backend.data.init_data import models_data
from backend.fastapi.api.v1.endpoints.reddit import start_streaming

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
    
    if os.getenv("ENV_NAME") != "STAGING":
        # Start Reddit comment streaming
        await start_streaming()
    else:
        print("Skipping Reddit comment streaming on STAGING environment")
    yield
    
    # Cleanup code here if needed