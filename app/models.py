from sqlalchemy import (
    JSON, Column, Integer, String, Boolean, Text, ForeignKey, DateTime, func
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True)
    hashed_password = Column(String)

    name = Column(String)
    surname = Column(String)
    middle_name = Column(String)

    group = Column(String)
    role = Column(String)  # student | teacher | admin

    created_tests = relationship("Test", back_populates="author")
    assigned_tests = relationship(
        "TestAssignment",
        back_populates="student",
        foreign_keys="TestAssignment.student_id"
    )
    assigned_by_teacher = relationship(
        "TestAssignment",
        back_populates="teacher",
        foreign_keys="TestAssignment.teacher_id"
    )


class Test(Base):
    __tablename__ = "tests"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(Text)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    shuffle_questions = Column(Boolean, default=False)

    hide_balls = Column(Boolean, default=False)
    hide_results = Column(Boolean, default=False)
    is_public = Column(Boolean, default=True)

    total_questions = Column(Integer, nullable=True)
    questions_to_answer = Column(Integer, nullable=True)

    attempts_count = Column(Integer, nullable=True)
    timer = Column(Integer, nullable=True)
    min_ball = Column(Integer)
    avg_ball = Column(Integer)
    max_ball = Column(Integer)
    no_copy = Column(Boolean, default=False)

    author_id = Column(Integer, ForeignKey("users.id"))
    author = relationship("User", back_populates="created_tests")

    questions = relationship(
        "Question",
        back_populates="test",
        cascade="all, delete",
        order_by="Question.index"
    )

    assignments = relationship(
        "TestAssignment",
        back_populates="test",
        cascade="all, delete"
    )

class Question(Base):
    __tablename__ = "questions"

    id = Column(String, primary_key=True)
    test_id = Column(Integer, ForeignKey("tests.id", ondelete="CASCADE"))

    type = Column(String) 
    question = Column(Text)
    description = Column(Text, nullable=True)

    ball = Column(Integer)
    is_half_ball = Column(Boolean, default=False)
    is_multiple = Column(Boolean, default=False)
    index = Column(Integer)

    answers = Column(ARRAY(String), nullable=True)
    correct_answers = Column(ARRAY(String), nullable=True)

    test = relationship("Test", back_populates="questions")



class TestAssignment(Base):
    __tablename__ = "test_assignments"

    id = Column(Integer, primary_key=True)
    test_id = Column(Integer, ForeignKey("tests.id"))
    student_id = Column(Integer, ForeignKey("users.id"))
    teacher_id = Column(Integer, ForeignKey("users.id"))

    test = relationship("Test", back_populates="assignments")
    student = relationship("User", back_populates="assigned_tests", foreign_keys=[student_id])
    teacher = relationship("User", back_populates="assigned_by_teacher", foreign_keys=[teacher_id])

class CompletedTest(Base):
    __tablename__ = "completed_tests"

    id = Column(Integer, primary_key=True)
    test_id = Column(Integer, ForeignKey("tests.id"))
    student_id = Column(Integer, ForeignKey("users.id"))
    completed_at = Column(DateTime(timezone=True), server_default=func.now())
    total_ball = Column(Integer)
    attempt_id = Column(Integer, ForeignKey("attempted_tests.id"), nullable=True)
    attempt = relationship("AttemptedTest")

    # JSON для хранения структуры вопросов и ответов студента
    questions = Column(JSON)

    student = relationship("User")
    test = relationship("Test")    

class AttemptedTest(Base):
    __tablename__ = "attempted_tests"

    id = Column(Integer, primary_key=True)

    test_id = Column(Integer, ForeignKey("tests.id"))
    student_id = Column(Integer, ForeignKey("users.id"))

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed = Column(Boolean, default=False)
    answers = Column(JSON, nullable=True)

    # зафиксированные вопросы
    questions = Column(JSON)

    student = relationship("User")
    test = relationship("Test")