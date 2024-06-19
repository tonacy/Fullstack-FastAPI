import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/")
def onboard_message():
    return {"message": "You've been onboarded!"}

@router.get("/test")
def print_username_password():
    return {"username": os.getenv("USER_NAME"), "password": os.getenv("PASSWORD")}

