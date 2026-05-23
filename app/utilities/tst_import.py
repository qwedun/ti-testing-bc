from pathlib import Path
import zipfile
from typing import Any


def safe_int(value: str, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def read_tst_file(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()

    # Для твоих .tst правильная кодировка ANSI = cp1251
    for encoding in ("cp1251", "cp866", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("cp1251", errors="replace")


class TstReader:
    def __init__(self, text: str):
        self.lines = [line.rstrip("\r\n") for line in text.splitlines()]
        self.pos = 0

    def read(self) -> str:
        if self.pos >= len(self.lines):
            raise ValueError("Неожиданный конец .tst файла")
        value = self.lines[self.pos]
        self.pos += 1
        return value

    def read_int(self, default: int = 0) -> int:
        return safe_int(self.read(), default=default)


def extract_archive(archive_path: str, target_dir: str) -> None:
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    lower = archive_path.lower()

    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path, mode="r") as zf:
            zf.extractall(path=target_dir)
        return

    if lower.endswith(".7z"):
        try:
            import py7zr
        except ImportError:
            raise ValueError("Для распаковки .7z установи py7zr или загружай архив в .zip")

        with py7zr.SevenZipFile(archive_path, mode="r") as zf:
            zf.extractall(path=target_dir)
        return

    raise ValueError("Поддерживаются только .zip и .7z архивы")


def normalize_question_text(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def parse_single_or_multiple(reader: TstReader, q_type: int) -> dict[str, Any]:
    answers_count = reader.read_int()
    answers: list[str] = []
    correct_answers: list[str] = []

    for _ in range(answers_count):
        is_correct = reader.read_int()
        answer_text = reader.read().strip()
        answers.append(answer_text)
        if is_correct == 1:
            correct_answers.append(answer_text)

    return {
        "type": "text",
        "is_multiple": q_type == 1,
        "answers": answers,
        "correct_answers": correct_answers,
    }


def parse_order(reader: TstReader) -> dict[str, Any]:
    seq_len = reader.read_int()
    correct_answers = [reader.read().strip() for _ in range(seq_len)]

    return {
        "type": "order",
        "is_multiple": False,
        "answers": correct_answers[:],
        "correct_answers": correct_answers,
    }


def parse_pair(reader: TstReader) -> dict[str, Any]:
    chain_len = reader.read_int()

    # Левая часть соответствия
    left_items = [
        reader.read().strip()
        for _ in range(chain_len)
    ]

    # Правая часть соответствия
    # Важно: это НЕ индексы left_items, а самостоятельные значения из .tst
    right_items = [
        reader.read().strip()
        for _ in range(chain_len)
    ]

    correct_answers = [
        f"{left}_{right}"
        for left, right in zip(left_items, right_items)
    ]

    return {
        "type": "pair",
        "is_multiple": False,
        "answers": None,
        "correct_answers": correct_answers,
    }


def parse_open(reader: TstReader) -> dict[str, Any]:
    answer_text = reader.read().strip()

    return {
        "type": "text",
        "is_multiple": False,
        "answers": [],
        "correct_answers": [answer_text] if answer_text else [],
    }


def parse_tst_content(raw_text: str) -> dict[str, Any]:
    reader = TstReader(raw_text)

    file_header = reader.read().strip()
    if not file_header.startswith("[.TST FILE"):
        raise ValueError(f"Неверный заголовок файла: {file_header!r}")

    test_name = reader.read().strip()
    author_name = reader.read().strip()

    description_lines_count = reader.read_int()
    description_lines = [reader.read() for _ in range(description_lines_count)]
    description = normalize_question_text(description_lines)

    min_ball = reader.read_int()
    avg_ball = reader.read_int()
    max_ball = reader.read_int()

    timer = reader.read_int()
    questions_to_answer = reader.read_int()

    results_path = reader.read().strip()
    total_questions = reader.read_int()

    questions: list[dict[str, Any]] = []

    for index in range(total_questions):
        question_lines_count = reader.read_int()
        question_lines = [reader.read() for _ in range(question_lines_count)]
        question_text = normalize_question_text(question_lines)

        image_name = reader.read().strip()
        if image_name.upper() == "NULL":
            image_name = None

        q_type = reader.read_int()
        if q_type not in (0, 1, 2, 3, 4):
            raise ValueError(
                f"Ошибка парсинга вопроса #{index + 1}: "
                f"ожидался тип вопроса 0..4, получено {q_type}"
            )

        if q_type in (0, 1):
            parsed_q = parse_single_or_multiple(reader, q_type)
        elif q_type == 2:
            parsed_q = parse_order(reader)
        elif q_type == 3:
            parsed_q = parse_pair(reader)
        else:
            parsed_q = parse_open(reader)

        questions.append({
            "index": index,
            "question": question_text,
            "description": None,
            "ball": 1,
            "is_half_ball": False,
            "image": image_name,
            **parsed_q,
        })

    return {
        "file_header": file_header,
        "name": test_name,
        "author_name": author_name,
        "description": description,
        "min_ball": min_ball,
        "avg_ball": avg_ball,
        "max_ball": max_ball,
        "timer": timer if timer > 0 else None,
        "questions_to_answer": questions_to_answer if questions_to_answer > 0 else None,
        "total_questions": total_questions,
        "results_path": results_path,
        "questions": questions,
    }


def build_question_response(question, media_base_url: str | None = None) -> dict[str, Any]:
    answers_out = None
    if question.answers is not None:
        answers_out = [
            {"answer": ans, "index": idx}
            for idx, ans in enumerate(question.answers)
        ]

    payload = {
        "id": question.id,
        "type": question.type,
        "isMultiple": question.is_multiple,
        "question": question.question,
        "description": question.description,
        "ball": question.ball,
        "isHalfBall": question.is_half_ball,
        "answers": answers_out,
        "index": question.index,
        "answered": [],
        "isCorrect": None,
    }

    if getattr(question, "image", None) and media_base_url:
        payload["imageUrl"] = f"{media_base_url}/{question.image}"

    return payload

def build_preview_question(question, media_base_url: str | None = None) -> dict[str, Any]:
    answers = []

    if question["type"] == "text":
        raw_answers = question["answers"] or []
        correct_answers = set(question["correct_answers"] or [])

        answers = [
            {
                "id": f"{question['id']}_answer_{idx}",
                "text": ans,
                "index": idx,
                "isCorrect": ans in correct_answers,
            }
            for idx, ans in enumerate(raw_answers)
        ]

    elif question["type"] == "order":
        raw_answers = question["answers"] or []

        answers = [
            {
                "id": f"{question['id']}_answer_{idx}",
                "text": ans,
                "index": idx,
                "isCorrect": False,
            }
            for idx, ans in enumerate(raw_answers)
        ]

    elif question["type"] == "pair":
        raw_answers = question["correct_answers"] or []

        for idx, pair in enumerate(raw_answers):
            left = ""
            right = ""

            if "_" in pair:
                left, right = pair.split("_", 1)

            answers.append({
                "id": f"{question['id']}_answer_{idx}",
                "left": left,
                "right": right,
                "index": idx,
                "isCorrect": False,
            })

    payload = {
        "id": question["id"],
        "type": question["type"],
        "isMultiple": question["is_multiple"],
        "question": question["question"],
        "description": question["description"],
        "answers": answers,
        "index": question["index"],
        "ball": str(question["ball"]),
        "isHalfBall": question["is_half_ball"],
    }

    if question.get("image") and media_base_url:
        payload["imageUrl"] = f"{media_base_url}/{question['image']}"

    return payload

import uuid

def build_preview_test(parsed: dict[str, Any], import_token: str, media_base_url: str | None = None) -> dict[str, Any]:
    questions = []

    for q in parsed["questions"]:
        q_copy = dict(q)
        q_copy["id"] = str(uuid.uuid4())
        questions.append(build_preview_question(q_copy, media_base_url))

    return {
        "name": parsed["name"],
        "description": parsed["description"],
        "timer": str(parsed["timer"]) if parsed["timer"] is not None else None,
        "activeQuestionId": questions[0]["id"] if questions else "",
        "questions": questions,
        "updatedAt": None,
        "hideBalls": False,
        "hideResults": False,
        "attemptsCount": None,
        "minBall": str(parsed["min_ball"]),
        "avgBall": str(parsed["avg_ball"]),
        "maxBall": str(parsed["max_ball"]),
        "shuffleQuestions": False,
        "noCopy": False,
        "errors": [],
        "group": None,
        "students": [],
        "toAll": True,
        "toAllInGroup": False,
        "questionsToAnswer": parsed["questions_to_answer"],
        "totalQuestions": parsed["total_questions"],
    }