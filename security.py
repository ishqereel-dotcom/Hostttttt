from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import Request, HTTPException
from . import config, db

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(config.SECRET_KEY, salt="session")

SESSION_COOKIE = "bp_session"
MAX_AGE = 60 * 60 * 24 * 7

def hash_pw(p: str) -> str:
    return pwd.hash(p)

def check_pw(p: str, h: str) -> bool:
    return pwd.verify(p, h)

def make_session(user_id: int, is_admin: bool) -> str:
    return serializer.dumps({"uid": user_id, "adm": is_admin})

def read_session(token: str) -> dict | None:
    try:
        return serializer.loads(token, max_age=MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None

async def current_user(request: Request) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "not logged in")
    data = read_session(token)
    if not data:
        raise HTTPException(401, "session expired")
    return data