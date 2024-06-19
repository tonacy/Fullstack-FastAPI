import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/")
def onboard_message():
    return {"message": "You've been onboarded!"}

@router.get("/env")
def print_env():
    return {"username": os.getenv("USER_NAME"),
            "password": os.getenv("PASSWORD"),
            "DB_ENGINE": os.getenv("DB_ENGINE"),
            "DB_USERNAME": os.getenv("DB_USERNAME"),
            "DB_PASS": os.getenv("DB_PASS"),
            "DB_HOST": os.getenv("DB_HOST"),
            "DB_PORT": os.getenv("DB_PORT"),
            "DB_NAME": os.getenv("DB_NAME")
            }