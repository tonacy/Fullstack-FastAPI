import os
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

@router.get("/")
def onboard_message():
    return {"message": "You've been onboarded!"}