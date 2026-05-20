# routers/auth.py
from fastapi import APIRouter, Depends, HTTPException, status, Response, Cookie
from sqlalchemy.orm import Session
from typing import Optional
from datetime import timedelta

from app.database import get_db
from app.models import User
from app.utilities import auth as auth_utils
from pydantic import BaseModel

SECRET_TEACHER_WORD = "ti2026"

router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)

# -----------------------
# Pydantic модели
# -----------------------
class RegisterRequest(BaseModel):
    login: str
    password: str
    name: str = ""
    surname: str = ""
    middle_name: str = ""
    group: str = ""
    role: str = "student"


class LoginRequest(BaseModel):
    login: str
    password: str

# -----------------------
# /register
# -----------------------
class RegisterRequest(BaseModel):
    login: str
    password: str
    name: str = ""
    surname: str = ""
    middle_name: str = ""
    group: str = ""
    role: str = "student"
    secretWord: str | None = None


@router.post("/register")
def register(payload: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    login = (payload.login or "").strip()
    password = (payload.password or "").strip()
    name = (payload.name or "").strip()
    surname = (payload.surname or "").strip()
    group = (payload.group or "").strip()
    middle_name = (payload.middle_name or "").strip() if payload.middle_name else None
    secret = (payload.secretWord or "").strip() if payload.secretWord else None

    required_fields = {
        "login": login,
        "password": password,
        "name": name,
        "surname": surname,
        "group": group,
    }

    for field_name, value in required_fields.items():
        if not value:
            raise HTTPException(
                status_code=400,
                detail=f"Поле {field_name} обязательно для заполнения"
            )

    # Проверка секретного слова для преподавателя
    if payload.role == "teacher":
        if not secret:
            raise HTTPException(
                status_code=400,
                detail="Секретное слово обязательно для преподавателя"
            )

        if secret != SECRET_TEACHER_WORD:
            raise HTTPException(
                status_code=400,
                detail="Неверное секретное слово"
            )

    existing_user = db.query(User).filter(User.login == login).first()

    if existing_user:
        raise HTTPException(
            status_code=400,
            detail="Пользователь с таким логином уже существует"
        )

    hashed_pw = auth_utils.hash_password(password)

    user = User(
        login=login,
        hashed_password=hashed_pw,
        name=name,
        surname=surname,
        middle_name=middle_name,
        group=group,
        role=payload.role
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user

# -----------------------
# /login
# -----------------------
@router.post("/login")
def login(
    response: Response,
    payload: LoginRequest,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.login == payload.login).first()
    if not user or not auth_utils.verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = auth_utils.create_access_token({
        "sub": user.login,
        "user_id": user.id,
        "role": user.role
    })
    
    response.set_cookie(
        key="access_token",
        value=token,
        samesite="none",
        secure=True,
        httponly=True,
        max_age=60 * 60 * 24 * 7
    )

    return {
        "id": user.id,
        "login": user.login,
        "role": user.role,
        "name": user.name,
        "surname": user.surname,
        "group": user.group
    }

# -----------------------
# /me
# -----------------------
def get_current_user(access_token: Optional[str] = Cookie(None), db: Session = Depends(get_db)) -> User:
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = auth_utils.decode_access_token(access_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")

    login = payload.get("sub")
    if not login:
        raise HTTPException(status_code=401, detail="Invalid token")

    user = db.query(User).filter(User.login == login).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user 


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "login": user.login, "role": user.role, "name": user.name, "surname": user.surname, "group": user.group}

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(
        key="access_token",
        samesite="none",
        secure=True,
        httponly=True
    )
    return {"message": "Logged out"}