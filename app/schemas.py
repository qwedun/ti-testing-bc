from pydantic import BaseModel
from typing import List, Optional, Literal

class UserCreate(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str

    class Config:
        orm_mode = True

QuestionType = Literal['text', 'pair', 'free', 'order']


class QuestionCreate(BaseModel):
    type: QuestionType
    question: str
    description: Optional[str] = None

    ball: str
    isMultiple: bool
    isHalfBall: Optional[bool] = False

    answers: Optional[List[str]] = None
    correctAnswers: List[str]


class TestCreate(BaseModel):
    name: str
    description: str
    timer: Optional[str] = None

    questions: List[QuestionCreate]

    minBall: str
    avgBall: str
    maxBall: str

    hideBalls: bool
    hideResults: bool
    attemptsCount: Optional[str] = None
    shuffleQuestions: bool
    noCopy: bool        
    totalQuestions: Optional[int] = None
    questionsToAnswer: Optional[int] = None
    toAll: Optional[bool] = False
    students: Optional[List[int]] = None
    group: Optional[str] = None