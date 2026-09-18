from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def home():
    return {"message": "Hi there, this is NTHU LINE Bot API"}


@router.get("/ping")
def ping():
    return {"message": "pong"}
