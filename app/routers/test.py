from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
import uuid
from sqlalchemy import func
from random import sample, shuffle
from app.database import get_db
from app.models import AttemptedTest, CompletedTest, Test, Question, TestAssignment, User
from app.schemas import TestCreate
from app.routers.auth import get_current_user
from typing import List

from app.utilities.test import check_answer_correct, normalize_answer
router = APIRouter(prefix="/tests", tags=["tests"])

def get_fio(user: User | None) -> str:
    if not user:
        return ""

    parts = [user.surname, user.name, user.middle_name]
    return " ".join(part for part in parts if part and part.strip())

@router.post("")
def create_test(
    payload: TestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    test = Test(
        name=payload.name,
        description=payload.description,
        total_questions=int(payload.totalQuestions) if payload.totalQuestions else None,
        questions_to_answer=int(payload.questionsToAnswer) if payload.questionsToAnswer else None,

        shuffle_questions=payload.shuffleQuestions,
        hide_balls=payload.hideBalls,
        hide_results=payload.hideResults,

        attempts_count=int(payload.attemptsCount) if payload.attemptsCount else None,
        timer=int(payload.timer) if payload.timer else None,

        min_ball=int(payload.minBall),
        avg_ball=int(payload.avgBall),
        max_ball=int(payload.maxBall),

        no_copy=payload.noCopy,
        author_id=current_user.id
    )

    db.add(test)
    db.flush()

    # -------------------------
    # НАЗНАЧЕНИЕ ТЕСТА
    # -------------------------
    students_ids = []

    if payload.toAll:
        test.is_public = True
    else:
        test.is_public = False

        # конкретные студенты
        if payload.students:
            students_ids = payload.students

        # вся группа
        elif payload.group:
            students_ids = [
                s.id for s in db.query(User.id)
                .filter(User.group == payload.group)
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
    # ВОПРОСЫ
    # -------------------------
    questions = []

    for index, q in enumerate(payload.questions):
        question = Question(
            id=str(uuid.uuid4()),
            test_id=test.id,

            type=q.type,
            question=q.question,
            description=q.description,
            is_multiple=q.isMultiple,

            ball=int(q.ball),
            is_half_ball=q.isHalfBall or False,
            index=index,

            answers=q.answers,
            correct_answers=q.correctAnswers,
        )

        questions.append(question)

    db.add_all(questions)

    db.commit()

    return {"id": test.id}

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

    return [
        {
            "id": t.id,
            "author_name": t.author_name,
            "name": t.name,
            "description": t.description,
            "created_at": t.created_at,
            "min_ball": t.min_ball,
            "timer": t.timer,
            "questions_count": t.questions_count,
            "questions_to_answer": t.questions_to_answer,
            "attempts_count": t.attempts_count
        }
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
            func.count(Question.id).label("questions_count"),
            Test.attempts_count
        )
        .join(Test.author)
        .join(TestAssignment, TestAssignment.test_id == Test.id)
        .outerjoin(Test.questions)
        .filter(TestAssignment.student_id == current_user.id)  # 👈 ключ
        .group_by(
            Test.id,
            User.surname,
            User.name,
            Test.name,
            Test.description,
            Test.created_at,
            Test.min_ball,
            Test.timer,
            Test.attempts_count
        )
        .order_by(Test.created_at.desc())
        .all()
    )

    return [
        {
            "id": t.id,
            "author_name": t.author_name,
            "name": t.name,
            "description": t.description,
            "created_at": t.created_at,
            "min_ball": t.min_ball,
            "timer": t.timer,
            "questions_count": t.questions_count,
            "attempts_count": t.attempts_count
        }
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

    # сохраняем только нужные данные
    answers = [
        {
            "id": q["id"],
            "answered": q.get("answered", [])
        }
        for q in payload.get("questions", [])
    ]

    attempt.answers = answers

    db.commit()

    return {"status": "ok"}


@router.get("/{test_id}")
def get_test(
    test_id: int,
    preview: bool = Query(False),
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
        if test.attempts_count is None:
            attempts_label = ""
        else:
            attempts_label = str(test.attempts_count)

    # -------------------------
    # PREVIEW → без вопросов
    # -------------------------
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
            "hideBalls": test.hide_balls,
            "hideResults": test.hide_results,
            "shuffleQuestions": test.shuffle_questions,
            "noCopy": test.no_copy,
            "teacher": teacher_data,
            "questions": []
        }

    # -------------------------
    # TEACHER
    # -------------------------
    if user.role == "teacher":
        questions_out = []

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
                "correctAnswers": q.correct_answers
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
            "hideBalls": test.hide_balls,
            "hideResults": test.hide_results,
            "shuffleQuestions": test.shuffle_questions,
            "noCopy": test.no_copy,
            "teacher": teacher_data,
            "questions": questions_out
        }

    # -------------------------
    # STUDENT
    # -------------------------
    attempt = db.query(AttemptedTest).filter(
        AttemptedTest.test_id == test.id,
        AttemptedTest.student_id == user.id,
        AttemptedTest.completed == False
    ).first()

    # если незавершённой попытки нет — проверяем лимит и создаём новую
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
    questions = [questions_map[q_id] for q_id in attempt.questions if q_id in questions_map]

    questions_out = []

    answers_map = {}
    if attempt.answers:
        answers_map = {
            a["id"]: a["answered"]
            for a in attempt.answers
        }

    for q in questions:
        q_data = {
            "id": q.id,
            "type": q.type,
            "question": q.question,
            "description": q.description,
            "ball": q.ball,
            "isHalfBall": q.is_half_ball,
            "isMultiple": q.is_multiple,
            "answered": answers_map.get(q.id, [])
        }

        if q.type == "text":
            q_data["answers"] = q.answers

        elif q.type == "order":
            shuffled = q.correct_answers.copy()
            shuffle(shuffled)
            q_data["answers"] = shuffled

        elif q.type == "pair":
            left_items = []
            right_items = []

            for pair in q.correct_answers:
                left, right = pair.split("_", 1)
                left_items.append(left)
                right_items.append(right)

            shuffle(left_items)
            shuffle(right_items)

            q_data["left_items"] = left_items
            q_data["right_items"] = right_items

        else:
            q_data["answers"] = q.answers

        questions_out.append(q_data)

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
        "hideBalls": test.hide_balls,
        "hideResults": test.hide_results,
        "shuffleQuestions": test.shuffle_questions,
        "noCopy": test.no_copy,
        "teacher": teacher_data,
        "questions": questions_out
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

    attempt.completed = True
    attempt.answers = submitted_answers

    completed_test = CompletedTest(
        test_id=test.id,
        student_id=student.id,
        total_ball=total_ball,
        attempt_id=attempt.id,
        questions=questions_result
    )

    db.add(completed_test)
    db.commit()
    db.refresh(completed_test)

    return {
        "completed_test_id": completed_test.id,
        "total_ball": total_ball,
        "questions": questions_result
    }