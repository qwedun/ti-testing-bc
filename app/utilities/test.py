from sqlalchemy.orm import Session
from ..models import AttemptedTest, Question, Test

def can_start_test(db: Session, test_id: int, student_id: int):
    test = db.query(Test).filter(Test.id == test_id).first()
    if not test:
        return False, "Тест не найден"

    # если ограничений нет
    if test.attempts_count is None:
        return True, None

    used_attempts = db.query(AttemptedTest).filter(
        AttemptedTest.test_id == test_id,
        AttemptedTest.student_id == student_id,
        AttemptedTest.completed == True
    ).count()

    if used_attempts >= test.attempts_count:
        return False, f"Лимит попыток исчерпан. Максимум: {test.attempts_count}"

    return True, None


def normalize_answer(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def check_answer_correct(question: Question, student_answers):
    student_answers = normalize_answer(student_answers)
    correct_answers = normalize_answer(question.correct_answers)

    # порядок важен
    if question.type == "order":
        return student_answers == correct_answers

    # пары: порядок самих пар можно не учитывать
    if question.type == "pair":
        return sorted(student_answers) == sorted(correct_answers)

    # множественный выбор: порядок не важен
    if question.is_multiple:
        return sorted(student_answers) == sorted(correct_answers)

    # обычный одиночный / text
    return student_answers == correct_answers


def calculate_ball(question: Question, student_answers):
    student_answers = normalize_answer(student_answers)

    # Полностью правильный ответ
    if check_answer_correct(question, student_answers):
        return question.ball

    # Частичное оценивание только для multiple, если включено
    if question.is_multiple and question.is_half_ball:
        correct_answers = set(normalize_answer(question.correct_answers))
        student_set = set(student_answers)

        if not student_set:
            return 0

        # нет лишних вариантов, но выбран не весь набор
        if student_set.issubset(correct_answers) and student_set != correct_answers:
            return question.ball // 2

    return 0