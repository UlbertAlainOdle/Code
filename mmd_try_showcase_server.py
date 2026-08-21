import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from saya_app.config import MMD_DIRECTORY, STATIC_DIR


MMD_ROOT = Path(MMD_DIRECTORY).resolve()
TRY_MOTION_DIR = Path(
    os.environ.get("SAYA_TRY_MOTION_DIR", MMD_ROOT / "motion" / "try")
).resolve()
TRY_MOTION_EXPORT_DIR = Path(
    os.environ.get("SAYA_TRY_MOTION_EXPORT_DIR", MMD_ROOT / "motion" / "try_exports")
).resolve()


class ExportMotionsRequest(BaseModel):
    relative_paths: List[str]
    annotations: Dict[str, str] = Field(default_factory=dict)

app = FastAPI(title="MMD Try Showcase")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
app.mount("/MMD", StaticFiles(directory=str(MMD_ROOT)), name="MMD")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ],
    allow_origin_regex=r"https?://(127\.0\.0\.1|localhost)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def mmd_url(path):
    try:
        rel = path.resolve().relative_to(MMD_ROOT).as_posix()
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail="Try motion directory must be inside the configured MMD directory.",
        ) from exc
    return "/MMD/" + quote(rel, safe="/:@!$&'()*+,;=-._~")


def get_try_motion_path(relative_path):
    normalized = str(relative_path or "").replace("\\", "/").strip("/")
    if not normalized:
        raise HTTPException(status_code=400, detail="Empty motion path.")

    source = (TRY_MOTION_DIR / normalized).resolve()
    try:
        source.relative_to(TRY_MOTION_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid motion path: {relative_path}") from exc

    if source.suffix.lower() != ".vmd":
        raise HTTPException(status_code=400, detail=f"Only .vmd files can be exported: {relative_path}")

    return source


def make_export_dir():
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    export_dir = TRY_MOTION_EXPORT_DIR / f"checked_{stamp}"
    suffix = 2
    while export_dir.exists():
        export_dir = TRY_MOTION_EXPORT_DIR / f"checked_{stamp}_{suffix}"
        suffix += 1
    export_dir.mkdir(parents=True, exist_ok=False)
    return export_dir


def sanitize_file_stem(value):
    text = str(value or "").strip()
    if not text:
        return ""

    invalid_chars = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid_chars or ord(char) < 32 else char for char in text)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        return ""

    reserved_names = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if cleaned.upper() in reserved_names:
        cleaned = f"{cleaned}_"

    return cleaned[:120].rstrip(" .")


def get_unique_destination(destination):
    if not destination.exists():
        return destination

    suffix = 2
    stem = destination.stem
    suffix_text = f"_{suffix}"
    while True:
        trimmed_stem = stem[: max(1, 120 - len(suffix_text))]
        candidate = destination.with_name(f"{trimmed_stem}{suffix_text}{destination.suffix}")
        if not candidate.exists():
            return candidate
        suffix += 1
        suffix_text = f"_{suffix}"


@app.get("/")
def index():
    return RedirectResponse("/static/mmd_try_showcase.html")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "motion_dir": str(TRY_MOTION_DIR),
        "export_dir": str(TRY_MOTION_EXPORT_DIR),
    }


@app.get("/get_vmd_files")
def get_vmd_files():
    if not TRY_MOTION_DIR.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Try motion directory not found: {TRY_MOTION_DIR}",
        )
    if not TRY_MOTION_DIR.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Try motion path is not a directory: {TRY_MOTION_DIR}",
        )

    files = sorted(
        TRY_MOTION_DIR.rglob("*.vmd"),
        key=lambda item: item.relative_to(TRY_MOTION_DIR).as_posix().lower(),
    )
    motions = [
        {
            "name": path.stem,
            "relative_path": path.relative_to(TRY_MOTION_DIR).as_posix(),
            "url": mmd_url(path),
        }
        for path in files
    ]
    return JSONResponse(
        content={
            "motion_dir": str(TRY_MOTION_DIR),
            "count": len(motions),
            "motions": motions,
            "vmd_files": [motion["url"] for motion in motions],
        }
    )


@app.get("/get_try_vmd_files")
def get_try_vmd_files():
    return get_vmd_files()


@app.post("/export_checked_motions")
def export_checked_motions(request: ExportMotionsRequest):
    unique_paths = []
    seen = set()
    for relative_path in request.relative_paths:
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_paths.append(normalized)

    if not unique_paths:
        raise HTTPException(status_code=400, detail="No checked motion files were provided.")

    annotations = {}
    for relative_path, annotation in request.annotations.items():
        normalized = str(relative_path or "").replace("\\", "/").strip("/")
        if normalized:
            annotations[normalized] = str(annotation or "").strip()

    source_paths = [
        (relative_path, get_try_motion_path(relative_path))
        for relative_path in unique_paths
    ]
    export_dir = make_export_dir()
    copied = []
    skipped = []

    for relative_path, source in source_paths:
        if not source.exists() or not source.is_file():
            skipped.append({"relative_path": relative_path, "reason": "not found"})
            continue

        relative_source = source.relative_to(TRY_MOTION_DIR)
        annotation_stem = sanitize_file_stem(annotations.get(relative_path, ""))
        export_stem = annotation_stem or source.stem
        if annotation_stem and export_stem.lower().endswith(source.suffix.lower()):
            export_stem = export_stem[: -len(source.suffix)].rstrip(" .") or source.stem
        destination = export_dir / relative_source.parent / f"{export_stem}{source.suffix}"
        destination = get_unique_destination(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(
            {
                "relative_path": relative_source.as_posix(),
                "annotation": annotations.get(relative_path, ""),
                "exported_name": destination.name,
                "destination": str(destination),
            }
        )

    if not copied:
        try:
            export_dir.rmdir()
        except OSError:
            pass
        raise HTTPException(status_code=404, detail="No checked motion files could be copied.")

    return JSONResponse(
        content={
            "export_dir": str(export_dir),
            "copied_count": len(copied),
            "copied": copied,
            "skipped": skipped,
        }
    )
