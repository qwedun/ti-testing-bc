from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from .database import engine
from .models import Base
from fastapi.middleware.cors import CORSMiddleware
from app.routers import auth, test, user
from fastapi.staticfiles import StaticFiles
import os
BASE_DIR = Path(__file__).resolve().parent.parent
MEDIA_DIR = BASE_DIR / "media"

frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173")

origins = [
    frontend_url,
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

app = FastAPI()
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")
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
@app.get("/debug-image")
def debug_image():
    file_path = MEDIA_DIR / "tests" / "23" / "Q1P.bmp"
    return FileResponse(file_path)

@app.get("/")
def root():
    return {"status": "ok"}