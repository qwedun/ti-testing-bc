

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
from app.routers.auth import get_current_user


router = APIRouter(
    prefix="/user",
    tags=["user"]
)

@router.get("/students")
def get_students_by_group(
    group: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    students = (
        db.query(
            User.id,
            User.surname,
            User.name,
            User.middle_name
        )
        .filter(User.group == group)
        .filter(User.role == "student")
        .order_by(User.surname, User.name)
        .all()
    )

    return [
        {
            "id": s.id,
            "fio": f"{s.surname} {s.name} {s.middle_name or ''}".strip()
        }
        for s in students
    ]