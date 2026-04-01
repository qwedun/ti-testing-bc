from fastapi import FastAPI
from .database import engine
from .models import Base
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, test, user


origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173"
]

app = FastAPI()
app.include_router(auth.router)
app.include_router(test.router)
app.include_router(user.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {"status": "ok"}