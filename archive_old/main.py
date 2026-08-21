from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEAR_ROOT = PROJECT_ROOT.parents[2]

app = FastAPI()

app.mount(
    "/MMD",
    StaticFiles(directory=str(DEAR_ROOT / "MMD")),
    name="MMD",
)

app.mount(
    "/static",
    StaticFiles(directory=str(PROJECT_ROOT / "static"), html=True),
    name="static",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    return {"msg": "Visit http://127.0.0.1:8000/static/Chat4.html"}
