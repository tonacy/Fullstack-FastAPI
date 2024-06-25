import os
import uvicorn
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from backend.app.core.init_settings import args, global_settings
from backend.app.api.v1.endpoints import message, doc, base
from backend.app.dependencies.database import init_db, AsyncSessionLocal
from backend.app.crud.message import create_message_dict_async
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

app = FastAPI(lifespan=lifespan)

# Frontend
templates = Jinja2Templates(directory="frontend/login/templates")
app.mount("/static", StaticFiles(directory="frontend/login/static"), name="static")

# Set Middleware
# Define the allowed origins
origins = [
    global_settings.API_BASE_URL,
    "http://localhost",
    "http://localhost:5000",
]

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Document protection middleware
@app.middleware("http")
async def add_doc_protect(request: Request, call_next):
    if request.url.path in ["/docs", "/redoc", "/openapi.json"]:
        if not request.session.get('authenticated'):
            return RedirectResponse(url="/login")
    response = await call_next(request)
    return response
# Add session middleware with a custom expiration time (e.g., 30 minutes)
app.add_middleware(SessionMiddleware, 
                   secret_key="your_secret_key", 
                   max_age=18000)  # 18000 seconds = 300 minutes

# Add the routers to the FastAPI app
app.include_router(base.router, prefix="", tags=["main"])
app.include_router(doc.router, prefix="", tags=["doc"])
app.include_router(message.router, prefix="/api/v1", tags=["message"])


if __name__ == "__main__":
    # mounting at the root path
    uvicorn.run(
        app="backend.app.main:app",
        host = args.host,
        port=int(os.getenv("PORT", 5000)),
        reload=args.mode == "dev"  # Enables auto-reloading in development mode
    )