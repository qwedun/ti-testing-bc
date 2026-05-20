import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from app.utilities.tst_import import (
    build_preview_test,
    extract_archive,
    parse_tst_content,
    build_question_response,
    read_tst_file,
)

from fastapi import APIRouter, Depends, Form, HTTPException, Query, File, UploadFile
from sqlalchemy.orm import Session
import uuid
from sqlalchemy import func, or_
from random import sample, shuffle
from app.database import get_db
from app.models import AttemptedTest, CompletedTest, Test, Question, TestAssignment, User
from app.schemas import TestCreate
from app.routers.auth import get_current_user
from typing import List
from datetime import datetime, timezone

from app.utilities.test import check_answer_correct, normalize_answer
router = APIRouter(prefix="/tests", tags=["tests"])

def get_question_image_url(question: Question | None) -> str | None:
    if not question or not question.image:
        return None
    return f"media/tests/{question.test_id}/{question.image}"

def get_fio(user: User | None) -> str:
    if not user:
        return ""

    parts = [user.surname, user.name, user.middle_name]
    return " ".join(part for part in parts if part and part.strip())

def copy_tmp_image_to_test(image_url: str, test_id: int) -> str | None:
    if not image_url or not image_url.startswith("/media/tmp_imports/"):
        return None

    relative_path = image_url.removeprefix("/media/")
    src_path = Path("media") / relative_path

    if not src_path.exists():
        return None

    target_dir = Path("media") / "tests" / str(test_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    file_name = src_path.name
    dst_path = target_dir / file_name
    shutil.copy2(src_path, dst_path)

    return file_name


async def save_uploaded_question_image(file: UploadFile, test_id: int) -> str:
    target_dir = Path("media") / "tests" / str(test_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    original_name = file.filename or "image.bin"
    file_name = original_name
    dst_path = target_dir / file_name

    # если имя уже занято — добавим uuid
    if dst_path.exists():
        ext = Path(original_name).suffix
        stem = Path(original_name).stem
        file_name = f"{stem}_{uuid.uuid4().hex}{ext}"
        dst_path = target_dir / file_name

    content = await file.read()
    with open(dst_path, "wb") as f:
        f.write(content)

    return file_name

@router.post("")
async def create_test(
    payload: str = Form(...),
    question_images: list[UploadFile] = File(default=[]),
    question_image_ids: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        data = json.loads(payload)
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный payload")

    test = Test(
        name=data.get("name"),
        description=data.get("description"),
        total_questions=int(data["totalQuestions"]) if data.get("totalQuestions") else None,
        questions_to_answer=int(data["questionsToAnswer"]) if data.get("questionsToAnswer") else None,

        shuffle_questions=data.get("shuffleQuestions", False),
        hide_balls=data.get("hideBalls", False),
        hide_results=data.get("hideResults", False),

        attempts_count=int(data["attemptsCount"]) if data.get("attemptsCount") else None,
        timer=int(data["timer"]) if data.get("timer") else None,

        min_ball=int(data.get("minBall", 0)),
        avg_ball=int(data.get("avgBall", 0)),
        max_ball=int(data.get("maxBall", 0)),

        no_copy=data.get("noCopy", False),
        author_id=current_user.id
    )

    db.add(test)
    db.flush()

    # -------------------------
    # НАЗНАЧЕНИЕ ТЕСТА
    # -------------------------
    students_ids = []

    if data.get("toAll"):
        test.is_public = True
    else:
        test.is_public = False

        if data.get("students"):
            students_ids = data["students"]

        elif data.get("group"):
            students_ids = [
                s.id for s in db.query(User.id)
                .filter(User.group == data["group"])
                .filter(User.role == "student")
                .all()
            ]

        if students_ids:
            assignments = [
                TestAssignment(
                    test_id=test.id,
                    student_id=student_id,
                    teacher_id=current_user.id
                )
                for student_id in students_ids
            ]
            db.add_all(assignments)

    # -------------------------
    # КАРТИНКИ ВОПРОСОВ
    # -------------------------
    uploaded_images_map: dict[str, UploadFile] = {}

    for idx, question_id in enumerate(question_image_ids):
        if idx < len(question_images):
            uploaded_images_map[question_id] = question_images[idx]

    # -------------------------
    # ВОПРОСЫ
    # -------------------------
    questions = []

    for index, q in enumerate(data.get("questions", [])):
        image_file_name = None

        # 1. приоритет у новой пользовательской картинки
        if q.get("id") in uploaded_images_map:
            image_file_name = await save_uploaded_question_image(
                uploaded_images_map[q["id"]],
                test.id
            )

        # 2. если новой нет, но есть временная картинка из import/preview
        elif q.get("imageUrl"):
            image_file_name = copy_tmp_image_to_test(q["imageUrl"], test.id)

        answers = []
        correct_answers = []

        if q["type"] == "text":
            answers = q.get("answers", []) or []
            correct_answers = q.get("correctAnswers", []) or []

        elif q["type"] == "order":
            correct_answers = q.get("correctAnswers", []) or []
            answers = correct_answers[:]

        elif q["type"] == "pair":
            answers = None
            correct_answers = q.get("correctAnswers", []) or []

        question = Question(
            id=str(uuid.uuid4()),
            test_id=test.id,

            type=q["type"],
            question=q.get("question", ""),
            description=q.get("description"),
            is_multiple=q.get("isMultiple", False),

            ball=int(q.get("ball", 1)),
            is_half_ball=q.get("isHalfBall") or False,
            index=index,

            answers=answers,
            correct_answers=correct_answers,
            image=image_file_name,
        )

        questions.append(question)

    db.add_all(questions)
    db.commit()

    return {"id": test.id}

def build_attempts_label(attempts_used: int, attempts_total: int | None) -> str:
    if attempts_total is None:
        return ""

    remaining_attempts = max(attempts_total - attempts_used, 0)

    if attempts_used == 0:
        return str(attempts_total)

    return f"{remaining_attempts}/{attempts_total}"


def serialize_test_with_attempts(base_row, completed_attempts):
    attempts = [
        {
            "id": a.id,
            "attempt_id": a.attempt_id,
            "completed_at": a.completed_at,
            "total_ball": a.total_ball,
            "answered_time": a.answered_time
        }
        for a in completed_attempts
    ]

    best_result = None
    if completed_attempts:
        best = max(
            completed_attempts,
            key=lambda x: x.total_ball if x.total_ball is not None else 0
        )
        best_result = {
            "id": best.id,
            "attempt_id": best.attempt_id,
            "completed_at": best.completed_at,
            "total_ball": best.total_ball,
        }

    attempts_used = len(completed_attempts)
    attempts_label = build_attempts_label(attempts_used, base_row.attempts_count)

    return {
        "id": base_row.id,
        "author_name": base_row.author_name,
        "name": base_row.name,
        "description": base_row.description,
        "created_at": base_row.created_at,
        "min_ball": base_row.min_ball,
        "timer": base_row.timer,
        "questions_count": base_row.questions_count,
        "questions_to_answer": base_row.questions_to_answer,
        "attempts_count": base_row.attempts_count,
        "attemptsCount": attempts_label,
        "max_possible_ball": base_row.max_possible_ball,
        "attempts": attempts,
        "best_result": best_result,
    }


@router.get("/", response_model=List[dict])
def get_public_tests(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    query = (
        db.query(
            Test.id,
            func.concat(User.surname, ' ', User.name).label("author_name"),
            Test.name,
            Test.description,
            Test.created_at,
            Test.min_ball,
            Test.timer,
            Test.questions_to_answer,
            func.count(Question.id).label("questions_count"),
            func.coalesce(func.sum(Question.ball), 0).label("max_possible_ball"),
            Test.attempts_count
        )
        .join(Test.author)
        .outerjoin(Test.questions)
    )

    if user.role != "teacher":
        query = query.filter(Test.is_public == True)

    tests_with_counts = (
        query.group_by(
            Test.id,
            User.surname,
            User.name,
            Test.name,
            Test.description,
            Test.created_at,
            Test.min_ball,
            Test.timer,
            Test.questions_to_answer,
            Test.attempts_count
        )
        .order_by(Test.created_at.desc())
        .all()
    )

    test_ids = [t.id for t in tests_with_counts]
    completed_by_test = {}

    if test_ids:
        completed_rows = (
            db.query(CompletedTest)
            .filter(
                CompletedTest.student_id == user.id,
                CompletedTest.test_id.in_(test_ids)
            )
            .order_by(CompletedTest.total_ball.desc(), CompletedTest.completed_at.desc())
            .all()
        )

        for row in completed_rows:
            completed_by_test.setdefault(row.test_id, []).append(row)

    return [
        serialize_test_with_attempts(
            t,
            completed_by_test.get(t.id, [])
        )
        for t in tests_with_counts
    ]


@router.get("/assigned", response_model=List[dict])
def get_assigned_tests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    tests_with_counts = (
        db.query(
            Test.id,
            func.concat(User.surname, ' ', User.name).label("author_name"),
            Test.name,
            Test.description,
            Test.created_at,
            Test.min_ball,
            Test.timer,
            Test.questions_to_answer,
            func.count(Question.id).label("questions_count"),
            func.coalesce(func.sum(Question.ball), 0).label("max_possible_ball"),
            Test.attempts_count
        )
        .join(Test.author)
        .join(TestAssignment, TestAssignment.test_id == Test.id)
        .outerjoin(Test.questions)
        .filter(TestAssignment.student_id == current_user.id)
        .group_by(
            Test.id,
            User.surname,
            User.name,
            Test.name,
            Test.description,
            Test.created_at,
            Test.min_ball,
            Test.timer,
            Test.questions_to_answer,
            Test.attempts_count
        )
        .order_by(Test.created_at.desc())
        .all()
    )

    test_ids = [t.id for t in tests_with_counts]
    completed_by_test = {}

    if test_ids:
        completed_rows = (
            db.query(CompletedTest)
            .filter(
                CompletedTest.student_id == current_user.id,
                CompletedTest.test_id.in_(test_ids)
            )
            .order_by(CompletedTest.total_ball.desc(), CompletedTest.completed_at.desc())
            .all()
        )

        for row in completed_rows:
            completed_by_test.setdefault(row.test_id, []).append(row)

    return [
        serialize_test_with_attempts(
            t,
            completed_by_test.get(t.id, [])
        )
        for t in tests_with_counts
    ]
@router.get("/completed")
def get_completed_tests(
    db: Session = Depends(get_db),
    student: User = Depends(get_current_user)
):
    results = (
        db.query(
            CompletedTest.id,
            CompletedTest.total_ball,
            CompletedTest.completed_at,
            CompletedTest.attempt_id,

            Test.id.label("test_id"),
            Test.name,
            Test.description,
            Test.min_ball,
            Test.avg_ball,
            Test.max_ball
        )
        .join(Test, Test.id == CompletedTest.test_id)
        .filter(CompletedTest.student_id == student.id)
        .order_by(CompletedTest.completed_at.desc())
        .all()
    )

    return [
        {
            "id": r.id,
            "attempt_id": r.attempt_id,
            "test_id": r.test_id,
            "name": r.name,
            "description": r.description,
            "completed_at": r.completed_at,
            "total_ball": r.total_ball,
            "min_ball": r.min_ball,
            "avg_ball": r.avg_ball,
            "max_ball": r.max_ball
        }
        for r in results
    ]

@router.get("/completed/{completed_test_id}")
def get_completed_test_detail(
    completed_test_id: int,
    db: Session = Depends(get_db),
    student: User = Depends(get_current_user)
):
    completed = db.query(CompletedTest).filter(
        CompletedTest.id == completed_test_id,
        CompletedTest.student_id == student.id
    ).first()

    if not completed:
        raise HTTPException(status_code=404, detail="Completed test not found")

    test = completed.test
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    teacher = test.author
    teacher_data = {
        "id": teacher.id if teacher else None,
        "fio": get_fio(teacher)
    }

    result_map = {
        q["id"]: q for q in (completed.questions or [])
    }

    attempt_question_ids = []
    if completed.attempt and completed.attempt.questions:
        attempt_question_ids = completed.attempt.questions

    questions = []
    if attempt_question_ids:
        db_questions = db.query(Question).filter(
            Question.id.in_(attempt_question_ids)
        ).all()

        questions_map = {q.id: q for q in db_questions}
        questions = [questions_map[q_id] for q_id in attempt_question_ids if q_id in questions_map]

    questions_out = []

    for q in questions:
        result = result_map.get(q.id, {})

        q_data = {
            "id": q.id,
            "type": q.type,
            "question": q.question,
            "description": q.description,
            "ball": q.ball,
            "isHalfBall": q.is_half_ball,
            "isMultiple": q.is_multiple,
            "answered": result.get("answered", []),
            "isCorrect": result.get("is_correct", False),
            "receivedBall": result.get("ball", 0)
        }

        if q.type == "text":
            q_data["answers"] = q.answers

        elif q.type == "order":
            q_data["answers"] = q.answers or []

        elif q.type == "pair":
            pairs = q.correct_answers or []
            left_items = []
            right_items = []

            for pair in pairs:
                if "_" in pair:
                    left, right = pair.split("_", 1)
                    left_items.append(left)
                    right_items.append(right)

            q_data["left_items"] = left_items
            q_data["right_items"] = right_items

        else:
            q_data["answers"] = q.answers

        questions_out.append(q_data)

    return {
        "id": completed.id,
        "attempt_id": completed.attempt_id,
        "completed_at": completed.completed_at,
        "total_ball": completed.total_ball,
        "test": {
            "id": test.id,
            "name": test.name,
            "description": test.description,
            "created_at": test.created_at,
            "min_ball": test.min_ball,
            "avg_ball": test.avg_ball,
            "max_ball": test.max_ball,
            "hideBalls": test.hide_balls,
            "hideResults": test.hide_results,
            "shuffleQuestions": test.shuffle_questions,
            "noCopy": test.no_copy,
            "teacher": teacher_data
        },
        "questions": questions_out
    }

@router.post("/attempt/save")
def save_attempt(
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    attempt = db.query(AttemptedTest).filter(
        AttemptedTest.id == payload.get("attempt_id"),
        AttemptedTest.student_id == user.id
    ).first()

    if not attempt:
        raise HTTPException(status_code=404, detail="Attempt not found")

    if attempt.completed:
        return {
            "status": "completed",
            "completed": True
        }

    # -------------------------
    # СОХРАНЯЕМ ОТВЕТЫ
    # -------------------------
    answers = [
        {
            "id": q["id"],
            "answered": q.get("answered", [])
        }
        for q in payload.get("questions", [])
    ]

    attempt.answers = answers

    # -------------------------
    # ПРОВЕРКА ТАЙМЕРА
    # -------------------------
    test = attempt.test

    if test.timer is not None:
        now = datetime.now(timezone.utc)

        started_at = attempt.started_at

        # если postgres вернул naive datetime
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        elapsed_seconds = int((now - started_at).total_seconds())

        limit_seconds = test.timer * 60

        if elapsed_seconds >= limit_seconds:

            questions = db.query(Question).filter(
                Question.id.in_(attempt.questions)
            ).all()

            questions_map = {q.id: q for q in questions}

            total_ball = 0
            completed_questions = []

            answers_map = {
                a["id"]: a.get("answered", [])
                for a in answers
            }

            for q_id in attempt.questions:
                q = questions_map.get(q_id)

                if not q:
                    continue

                answered = answers_map.get(q.id, [])

                is_correct = check_answer_correct(q, answered)

                earned_ball = 0

                if is_correct:
                    earned_ball = q.ball

                total_ball += earned_ball

                completed_questions.append({
                    "id": q.id,
                    "question": q.question,
                    "answered": answered,
                    "correct_answers": q.correct_answers,
                    "is_correct": is_correct,
                    "earned_ball": earned_ball,
                    "max_ball": q.ball,
                })

            completed = CompletedTest(
                test_id=test.id,
                student_id=user.id,
                total_ball=total_ball,
                answered_time=elapsed_seconds,
                attempt_id=attempt.id,
                questions=completed_questions
            )

            db.add(completed)

            attempt.completed = True

            db.commit()

            return {
                "status": "completed",
                "completed": True,
                "total_ball": total_ball,
                "attempt_id": attempt.id
            }

    db.commit()

    return {
        "status": "ok",
        "completed": False
    }

@router.get("/search", response_model=List[dict])
def search_tests(
    search: str | None = Query(None),
    author_id: int | None = Query(None),
    is_public: bool | None = Query(None),
    assigned_to_me: bool = Query(False),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    has_timer: bool | None = Query(None),
    has_attempt_limit: bool | None = Query(None),
    min_questions: int | None = Query(None),
    max_questions: int | None = Query(None),
    has_image_questions: bool | None = Query(None),
    question_type: str | None = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = (
        db.query(
            Test.id,
            func.concat(User.surname, " ", User.name).label("author_name"),
            Test.name,
            Test.description,
            Test.created_at,
            Test.min_ball,
            Test.timer,
            Test.questions_to_answer,
            Test.attempts_count,
            Test.is_public,
            func.count(Question.id).label("questions_count"),
            func.coalesce(func.sum(Question.ball), 0).label("max_possible_ball"),
        )
        .join(Test.author)
        .outerjoin(Test.questions)
    )

    if assigned_to_me:
        query = query.join(TestAssignment, TestAssignment.test_id == Test.id)
        query = query.filter(TestAssignment.student_id == current_user.id)

    if current_user.role == "teacher":
        query = query.filter(Test.author_id == current_user.id)        

    if current_user.role != "teacher" and not assigned_to_me:
        query = query.filter(
            or_(
                Test.is_public == True,
                Test.assignments.any(TestAssignment.student_id == current_user.id)
            )
        )

    if search:
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Test.name.ilike(pattern),
                Test.description.ilike(pattern),
            )
        )

    if author_id is not None:
        query = query.filter(Test.author_id == author_id)

    if is_public is not None:
        query = query.filter(Test.is_public == is_public)

    if created_from:
        query = query.filter(Test.created_at >= created_from)

    if created_to:
        query = query.filter(Test.created_at <= created_to)

    if has_timer is True:
        query = query.filter(Test.timer.isnot(None))
    elif has_timer is False:
        query = query.filter(Test.timer.is_(None))

    if has_attempt_limit is True:
        query = query.filter(Test.attempts_count.isnot(None))
    elif has_attempt_limit is False:
        query = query.filter(Test.attempts_count.is_(None))

    if question_type:
        query = query.filter(Question.type == question_type)

    if has_image_questions is True:
        query = query.filter(Question.image.isnot(None))
    elif has_image_questions is False:
        query = query.filter(
            or_(Question.id.is_(None), Question.image.is_(None))
        )

    query = query.group_by(
        Test.id,
        User.surname,
        User.name,
        Test.name,
        Test.description,
        Test.created_at,
        Test.min_ball,
        Test.timer,
        Test.questions_to_answer,
        Test.attempts_count,
        Test.is_public,
    )

    if min_questions is not None:
        query = query.having(func.count(Question.id) >= min_questions)

    if max_questions is not None:
        query = query.having(func.count(Question.id) <= max_questions)

    sort_columns = {
        "created_at": Test.created_at,
        "name": Test.name,
        "timer": Test.timer,
        "questions_count": func.count(Question.id),
        "attempts_count": Test.attempts_count,
        "max_possible_ball": func.coalesce(func.sum(Question.ball), 0),
    }

    sort_column = sort_columns.get(sort_by, Test.created_at)

    if sort_order.lower() == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    tests_with_counts = query.all()

    test_ids = [t.id for t in tests_with_counts]
    completed_by_test = {}

    if test_ids:
        completed_rows = (
            db.query(CompletedTest)
            .filter(
                CompletedTest.student_id == current_user.id,
                CompletedTest.test_id.in_(test_ids)
            )
            .order_by(CompletedTest.total_ball.desc(), CompletedTest.completed_at.desc())
            .all()
        )

        for row in completed_rows:
            completed_by_test.setdefault(row.test_id, []).append(row)

    return [
        serialize_test_with_attempts(
            t,
            completed_by_test.get(t.id, [])
        )
        for t in tests_with_counts
    ]

@router.get("/{test_id}")
def get_test(
    test_id: int,
    preview: bool = Query(False),
    attempt_id: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    teacher = test.author
    teacher_data = {
        "id": teacher.id if teacher else None,
        "fio": get_fio(teacher)
    }

    used_attempts = 0
    remaining_attempts = None
    attempts_label = ""

    if user.role != "teacher":
        used_attempts = db.query(AttemptedTest).filter(
            AttemptedTest.test_id == test.id,
            AttemptedTest.student_id == user.id,
            AttemptedTest.completed == True
        ).count()

        if test.attempts_count is None:
            attempts_label = ""
        else:
            remaining_attempts = max(test.attempts_count - used_attempts, 0)

            if used_attempts == 0:
                attempts_label = str(test.attempts_count)
            else:
                attempts_label = f"{remaining_attempts}/{test.attempts_count}"
    else:
        attempts_label = "" if test.attempts_count is None else str(test.attempts_count)

    if preview:
        return {
            "id": test.id,
            "name": test.name,
            "description": test.description,
            "created_at": test.created_at,
            "total_questions": test.total_questions,
            "attempts_count": test.attempts_count,
            "attemptsCount": attempts_label,
            "min_ball": test.min_ball,
            "avg_ball": test.avg_ball,
            "max_ball": test.max_ball,
            "timer": test.timer,
            "hideBalls": test.hide_balls,
            "hideResults": test.hide_results,
            "shuffleQuestions": test.shuffle_questions,
            "noCopy": test.no_copy,
            "teacher": teacher_data,
            "questions": []
        }

    # Учитель без attempt_id смотрит структуру своего теста
    if user.role == "teacher" and attempt_id is None:
        questions_out = []

        max_possible_ball = sum((q.ball or 0) for q in test.questions)

        for q in test.questions:
            questions_out.append({
                "id": q.id,
                "type": q.type,
                "question": q.question,
                "description": q.description,
                "ball": q.ball,
                "isHalfBall": q.is_half_ball,
                "isMultiple": q.is_multiple,
                "answers": q.answers,
                "correctAnswers": q.correct_answers,
                "imageUrl": get_question_image_url(q)
            })

        return {
            "id": test.id,
            "name": test.name,
            "description": test.description,
            "created_at": test.created_at,
            "total_questions": test.total_questions,
            "attempts_count": test.attempts_count,
            "attemptsCount": attempts_label,
            "min_ball": test.min_ball,
            "avg_ball": test.avg_ball,
            "max_ball": test.max_ball,
            "total_ball": None,
            "max_possible_ball": max_possible_ball,
            "hideBalls": test.hide_balls,
            "hideResults": test.hide_results,
            "shuffleQuestions": test.shuffle_questions,
            "noCopy": test.no_copy,
            "teacher": teacher_data,
            "questions": questions_out
        }

    # -------------------------
    # STUDENT / TEACHER RESULT VIEW
    # -------------------------
    attempt = None

    if attempt_id is not None:
        attempt_query = db.query(AttemptedTest).filter(
            AttemptedTest.id == attempt_id,
            AttemptedTest.test_id == test.id
        )

        if user.role == "teacher":
            if test.author_id != user.id:
                raise HTTPException(
                    status_code=403,
                    detail="Нет прав на просмотр этой попытки"
                )
        else:
            attempt_query = attempt_query.filter(
                AttemptedTest.student_id == user.id
            )

        attempt = attempt_query.first()

        if not attempt:
            raise HTTPException(status_code=404, detail="Attempt not found")

    else:
        attempt = db.query(AttemptedTest).filter(
            AttemptedTest.test_id == test.id,
            AttemptedTest.student_id == user.id,
            AttemptedTest.completed == False
        ).first()

        if not attempt:
            if test.attempts_count is not None and used_attempts >= test.attempts_count:
                raise HTTPException(
                    status_code=403,
                    detail=f"Лимит попыток исчерпан. Максимум попыток: {test.attempts_count}"
                )

            questions = test.questions

            total = test.total_questions or len(questions)
            to_answer = test.questions_to_answer or len(questions)

            total = min(total, len(questions))
            to_answer = min(to_answer, total)

            if to_answer < total:
                questions = sample(questions, to_answer)

            if test.shuffle_questions:
                shuffle(questions)

            attempt = AttemptedTest(
                test_id=test.id,
                student_id=user.id,
                completed=False,
                answers=[],
                questions=[q.id for q in questions]
            )

            db.add(attempt)
            db.commit()
            db.refresh(attempt)

    questions = db.query(Question).filter(
        Question.id.in_(attempt.questions)
    ).all()

    questions_map = {q.id: q for q in questions}
    questions = [
        questions_map[q_id]
        for q_id in attempt.questions
        if q_id in questions_map
    ]

    max_possible_ball = sum((q.ball or 0) for q in questions)

    answers_map = {}
    if attempt.answers:
        answers_map = {
            a["id"]: a.get("answered", [])
            for a in attempt.answers
        }

    completed_result = None
    result_map = {}

    if attempt.completed:
        completed_result = db.query(CompletedTest).filter(
            CompletedTest.attempt_id == attempt.id,
            CompletedTest.student_id == attempt.student_id,
            CompletedTest.test_id == test.id
        ).first()

        if completed_result and completed_result.questions:
            result_map = {
                q["id"]: q
                for q in completed_result.questions
            }

    completed_total_ball = None
    if attempt.completed and completed_result:
        completed_total_ball = completed_result.total_ball

    questions_out = []

    for q in questions:
        answered = answers_map.get(q.id, [])

        is_correct = None
        if attempt.completed:
            if q.id in result_map:
                is_correct = result_map[q.id].get("is_correct")
            else:
                is_correct = check_answer_correct(q, answered)

        q_data = {
            "id": q.id,
            "type": q.type,
            "question": q.question,
            "description": q.description,
            "ball": q.ball,
            "isHalfBall": q.is_half_ball,
            "isMultiple": q.is_multiple,
            "answered": answered,
            "isCorrect": is_correct,
            "imageUrl": get_question_image_url(q),
        }

        if q.type == "text":
            q_data["answers"] = q.answers

        elif q.type == "order":
            if attempt.completed or attempt_id is not None:
                q_data["answers"] = q.answers or []
            else:
                shuffled_answers = list(q.answers or [])
                original = shuffled_answers.copy()

                for _ in range(5):
                    shuffle(shuffled_answers)
                    if shuffled_answers != original:
                        break

                q_data["answers"] = shuffled_answers

        elif q.type == "pair":
            all_left = []
            all_right = []

            for pair in q.correct_answers or []:
                if "_" in pair:
                    left, right = pair.split("_", 1)
                    all_left.append(left)
                    all_right.append(right)

            used_left = set()
            used_right = set()

            for pair in answered or []:
                if "_" in pair:
                    left, right = pair.split("_", 1)

                    if left:
                        used_left.add(left)

                    if right:
                        used_right.add(right)

            left_items = [
                item for item in all_left
                if item not in used_left
            ]

            right_items = [
                item for item in all_right
                if item not in used_right
            ]

            if not attempt.completed and attempt_id is None:
                shuffle(left_items)
                shuffle(right_items)

            q_data["left_items"] = left_items
            q_data["right_items"] = right_items

        else:
            q_data["answers"] = q.answers

        questions_out.append(q_data)

    student_user = attempt.student

    student_data = {
        "id": student_user.id,
        "fio": get_fio(student_user),
        "group": student_user.group,
    }

    return {
        "attempt_id": attempt.id,
        "id": test.id,
        "name": test.name,
        "description": test.description,
        "created_at": test.created_at,
        "total_questions": test.total_questions,
        "attempts_count": test.attempts_count,
        "attemptsCount": attempts_label,
        "usedAttempts": used_attempts,
        "remainingAttempts": remaining_attempts,
        "min_ball": test.min_ball,
        "avg_ball": test.avg_ball,
        "max_ball": test.max_ball,
        "total_ball": completed_total_ball,
        "max_possible_ball": max_possible_ball,
        "hideBalls": test.hide_balls,
        "hideResults": test.hide_results,
        "shuffleQuestions": test.shuffle_questions,
        "noCopy": test.no_copy,
        "teacher": teacher_data,
        "student": student_data,
        "questions": questions_out,
        "started_at": attempt.started_at,
        "timer": test.timer,
        "completed": attempt.completed
    }

def calculate_ball(question: Question, student_answers: list) -> int:
    """
    Считает баллы за вопрос.
    - order: проверяется точный порядок.
    - text, pair, multiple choice: проверяется множество.
    """
    correct_answers = question.correct_answers or []

    if question.type == "order":
        return question.ball if student_answers == correct_answers else 0
    else:
        return question.ball if set(student_answers) == set(correct_answers) else 0


@router.post("/import/preview")
async def import_test_preview(
    tst_file: UploadFile = File(...),
    images_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not tst_file.filename or not tst_file.filename.lower().endswith(".tst"):
        raise HTTPException(status_code=400, detail="Нужен файл .tst")

    temp_dir = tempfile.mkdtemp(prefix="tst_preview_")

    try:
        tst_path = os.path.join(temp_dir, tst_file.filename)
        with open(tst_path, "wb") as f:
            f.write(await tst_file.read())

        import_token = str(uuid.uuid4())
        tmp_media_dir = os.path.join("media", "tmp_imports", import_token)
        os.makedirs(tmp_media_dir, exist_ok=True)

        if images_file and images_file.filename:
            archive_path = os.path.join(temp_dir, images_file.filename)
            with open(archive_path, "wb") as f:
                f.write(await images_file.read())

            # сначала распаковываем архив во временную папку
            images_extract_dir = os.path.join(temp_dir, "images_extracted")
            os.makedirs(images_extract_dir, exist_ok=True)

            extract_archive(archive_path, images_extract_dir)

            # потом копируем все файлы из любых вложенных папок
            # прямо в media/tmp_imports/<token>
            for root, _, files in os.walk(images_extract_dir):
                for file_name in files:
                    src = os.path.join(root, file_name)
                    dst = os.path.join(tmp_media_dir, file_name)
                    shutil.copy2(src, dst)

        raw_text = read_tst_file(tst_path)
        parsed = parse_tst_content(raw_text)

        media_base_url = f"/media/tmp_imports/{import_token}"

        preview_test = build_preview_test(
            parsed=parsed,
            import_token=import_token,
            media_base_url=media_base_url
        )

        return {
            "importToken": import_token,
            "test": preview_test
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка preview-импорта: {str(e)}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        
@router.post("/{test_id}/submit")
def submit_test(
    test_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    student: User = Depends(get_current_user)
):
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    attempt = db.query(AttemptedTest).filter(
        AttemptedTest.test_id == test.id,
        AttemptedTest.student_id == student.id,
        AttemptedTest.completed == False
    ).first()

    if not attempt:
        raise HTTPException(status_code=404, detail="Active attempt not found")

    allowed_question_ids = set(attempt.questions or [])
    submitted_answers = payload.get("questions", [])

    total_ball = 0
    questions_result = []

    for q_data in submitted_answers:
        question_id = q_data.get("id")
        if question_id not in allowed_question_ids:
            continue

        question = db.query(Question).filter(Question.id == question_id).first()
        if not question:
            continue

        student_answers = q_data.get("answered", [])
        is_correct = check_answer_correct(question, student_answers)
        ball = calculate_ball(question, student_answers)

        total_ball += ball

        questions_result.append({
            "id": question.id,
            "answered": normalize_answer(student_answers),
            "is_correct": is_correct,
            "ball": ball
        })

    # считаем затраченное время в секундах
    answered_time = None
    if attempt.started_at:
        now = datetime.now(timezone.utc)

        started_at = attempt.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        answered_time = max(int((now - started_at).total_seconds()), 0)

    attempt.completed = True
    attempt.answers = submitted_answers

    completed_test = CompletedTest(
        test_id=test.id,
        student_id=student.id,
        total_ball=total_ball,
        answered_time=answered_time,
        attempt_id=attempt.id,
        questions=questions_result
    )

    db.add(completed_test)
    db.commit()
    db.refresh(completed_test)

    return {
        "attempt_id": attempt.id,
        "total_ball": total_ball,
        "answered_time": answered_time,
        "questions": questions_result,        
    }

@router.get("/{test_id}/results")
def get_test_results(
    test_id: int,
    search: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    test = db.query(Test).filter(Test.id == test_id).first()

    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    if current_user.role not in ["teacher", "admin"]:
        raise HTTPException(status_code=403, detail="Нет прав для просмотра результатов")

    if current_user.role == "teacher" and test.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Можно смотреть результаты только своих тестов")

    query = (
        db.query(CompletedTest)
        .join(User, User.id == CompletedTest.student_id)
        .filter(CompletedTest.test_id == test_id)
    )

    if search:
        pattern = f"%{search.strip()}%"

        fio_expr = func.concat(
            User.surname,
            " ",
            User.name,
            " ",
            User.middle_name
        )

        query = query.filter(
            or_(
                User.name.ilike(pattern),
                User.surname.ilike(pattern),
                User.middle_name.ilike(pattern),
                User.group.ilike(pattern),
                User.login.ilike(pattern),
                fio_expr.ilike(pattern),
            )
        )

    completed_rows = (
        query
        .order_by(
            CompletedTest.student_id.asc(),
            CompletedTest.total_ball.desc(),
            CompletedTest.completed_at.desc()
        )
        .all()
    )

    grouped = {}

    for row in completed_rows:
        student = row.student

        if row.student_id not in grouped:
            grouped[row.student_id] = {
                "user": {
                    "id": student.id,
                    "login": student.login,
                    "name": student.name,
                    "surname": student.surname,
                    "middle_name": student.middle_name,
                    "fio": get_fio(student),
                    "group": student.group,
                },
                "attempts": []
            }

        grouped[row.student_id]["attempts"].append({
            "id": row.id,
            "attempt_id": row.attempt_id,
            "completed_at": row.completed_at,
            "total_ball": row.total_ball,
            "answered_time": row.answered_time,
        })

    for item in grouped.values():
        item["attempts"].sort(
            key=lambda x: (
                -(x["total_ball"] if x["total_ball"] is not None else 0),
                -(x["completed_at"].timestamp() if x["completed_at"] else 0)
            )
        )

    return list(grouped.values())

@router.delete("/{test_id}")
def delete_test(
    test_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    test = db.query(Test).filter(Test.id == test_id).first()

    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    if current_user.role != "admin" and test.author_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="Нет прав на удаление этого теста"
        )

    db.query(CompletedTest).filter(
        CompletedTest.test_id == test_id
    ).delete(synchronize_session=False)

    db.query(AttemptedTest).filter(
        AttemptedTest.test_id == test_id
    ).delete(synchronize_session=False)

    db.query(TestAssignment).filter(
        TestAssignment.test_id == test_id
    ).delete(synchronize_session=False)

    db.query(Question).filter(
        Question.test_id == test_id
    ).delete(synchronize_session=False)

    db.delete(test)
    db.commit()

    return {"status": "ok", "deleted_test_id": test_id}