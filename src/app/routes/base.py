from fastapi import APIRouter

router = APIRouter()


@router.get("/")
def home():
    return {"message": "Hi there, this is NTHU LINE Bot API"}


@router.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    return {"message": "pong"}
