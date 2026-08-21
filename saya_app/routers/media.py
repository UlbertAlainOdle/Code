import asyncio
import base64
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from core_services import motion_ai, vision_service
from saya_app.config import KNOWLEDGE_BASE_DIR, MMD_DIRECTORY, STATIC_DIR, UPLOADED_FILES_DIR
from saya_app.model_runtime import model_runtime


router = APIRouter(tags=["media"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_AVATAR_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_AVATAR_HISTORY = 3
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}
TEXT_EXTENSIONS = {
    ".txt", ".py", ".md", ".js", ".html", ".css", ".json",
    ".c", ".cpp", ".h", ".java", ".go", ".rs",
}
USER_PROFILE_PATH = KNOWLEDGE_BASE_DIR / "user_profile.json"
USER_AVATAR_DIR = (STATIC_DIR / "uploads" / "avatars").resolve()
USER_AVATAR_URL_PREFIX = "/static/uploads/avatars/"


def sanitize_upload_filename(filename):
    raw_name = os.path.basename((filename or "upload").replace("\\", "/"))
    stem, ext = os.path.splitext(raw_name)
    safe_stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._")
    if not safe_stem:
        safe_stem = "upload"
    safe_stem = safe_stem[:80]
    return safe_stem + ext.lower()


async def save_upload_with_limit(file, destination, max_bytes=MAX_UPLOAD_BYTES):
    total = 0
    try:
        with open(destination, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    limit_mb = max(1, int(max_bytes / (1024 * 1024)))
                    raise HTTPException(status_code=413, detail=f"File exceeds the {limit_mb} MB limit")
                buffer.write(chunk)
    except Exception:
        if os.path.exists(destination):
            os.remove(destination)
        raise


def build_image_data_url(file_path, ext):
    mime_type = IMAGE_MIME_TYPES.get(ext, "application/octet-stream")
    with open(file_path, "rb") as source:
        encoded = base64.b64encode(source.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def detect_avatar_extension(file_path):
    with open(file_path, "rb") as source:
        header = source.read(16)
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"BM"):
        return ".bmp"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp"
    return ""


def avatar_path_from_url(url):
    value = str(url or "").strip()
    if not value.startswith(USER_AVATAR_URL_PREFIX):
        return None
    filename = value[len(USER_AVATAR_URL_PREFIX):]
    if not filename or filename != os.path.basename(filename):
        return None
    path = (USER_AVATAR_DIR / filename).resolve()
    try:
        if os.path.commonpath([str(USER_AVATAR_DIR), str(path)]) != str(USER_AVATAR_DIR):
            return None
    except ValueError:
        return None
    if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
        return None
    return path


def load_user_profile_state():
    state = {"name": "Doctor", "avatar": ""}
    if USER_PROFILE_PATH.is_file():
        try:
            with open(USER_PROFILE_PATH, "r", encoding="utf-8") as source:
                loaded = json.load(source)
            if isinstance(loaded, dict):
                state["name"] = str(loaded.get("name") or "Doctor").strip()[:40] or "Doctor"
                state["avatar"] = str(loaded.get("avatar") or "").strip()
        except Exception:
            pass
    if state["avatar"] and avatar_path_from_url(state["avatar"]) is None:
        state["avatar"] = ""
    return state


def save_user_profile_state(state):
    USER_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    clean = {
        "name": str(state.get("name") or "Doctor").strip()[:40] or "Doctor",
        "avatar": str(state.get("avatar") or "").strip(),
    }
    temp_path = USER_PROFILE_PATH.with_suffix(".tmp")
    with open(temp_path, "w", encoding="utf-8") as destination:
        json.dump(clean, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    os.replace(temp_path, USER_PROFILE_PATH)
    return clean


def list_user_avatar_files():
    USER_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    files = [
        path for path in USER_AVATAR_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return files


def prune_user_avatar_files(preferred_path=None):
    files = list_user_avatar_files()
    if preferred_path is not None:
        preferred_path = Path(preferred_path).resolve()
        files = [preferred_path] + [
            path for path in files
            if path.resolve() != preferred_path
        ]
    for path in files[MAX_AVATAR_HISTORY:]:
        try:
            path.unlink()
        except OSError:
            pass
    return list_user_avatar_files()


def build_user_profile_response(state=None):
    state = state or load_user_profile_state()
    files = prune_user_avatar_files()
    available_urls = {
        f"{USER_AVATAR_URL_PREFIX}{path.name}"
        for path in files
    }
    if state.get("avatar") and state["avatar"] not in available_urls:
        state["avatar"] = ""
        state = save_user_profile_state(state)
    avatars = [
        {
            "url": f"{USER_AVATAR_URL_PREFIX}{path.name}",
            "filename": path.name,
            "uploaded_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for path in files
    ]
    return {
        "name": state.get("name") or "Doctor",
        "avatar": state.get("avatar") or "",
        "avatars": avatars,
    }


@router.get("/api/user-profile")
def get_user_profile():
    return {"profile": build_user_profile_response()}


@router.post("/api/user-profile")
def update_user_profile(payload: dict):
    state = load_user_profile_state()
    state["name"] = str(payload.get("name") or "Doctor").strip()[:40] or "Doctor"
    if "avatar" in payload:
        avatar = str(payload.get("avatar") or "").strip()
        if avatar and avatar_path_from_url(avatar) is None:
            raise HTTPException(status_code=400, detail="Selected avatar is not available")
        state["avatar"] = avatar
    state = save_user_profile_state(state)
    return {"status": "success", "profile": build_user_profile_response(state)}


@router.post("/api/user-profile/avatar")
async def upload_user_avatar(file: UploadFile = File(...)):
    content_type = str(file.content_type or "").lower()
    if content_type and not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Avatar must be an image")

    USER_AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    temp_path = USER_AVATAR_DIR / f".{uuid.uuid4().hex}.upload"
    try:
        await save_upload_with_limit(file, temp_path, max_bytes=MAX_AVATAR_UPLOAD_BYTES)
        detected_ext = detect_avatar_extension(temp_path)
        if detected_ext not in IMAGE_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail="Avatar must be a JPG, PNG, BMP, or WebP image",
            )
        final_path = USER_AVATAR_DIR / f"avatar_{uuid.uuid4().hex}{detected_ext}"
        os.replace(temp_path, final_path)
        prune_user_avatar_files(preferred_path=final_path)

        state = load_user_profile_state()
        state["avatar"] = f"{USER_AVATAR_URL_PREFIX}{final_path.name}"
        state = save_user_profile_state(state)
        return {"status": "success", "profile": build_user_profile_response(state)}
    finally:
        try:
            await file.close()
        except Exception:
            pass
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


@router.post("/api/upload_file")
async def upload_file(file: UploadFile = File(...), model: str = Form("")):
    try:
        safe_name = sanitize_upload_filename(file.filename)
        ext = os.path.splitext(safe_name)[1].lower()
        if ext not in IMAGE_EXTENSIONS and ext not in TEXT_EXTENSIONS:
            return {"status": "error", "message": "Unsupported file type"}

        unique_name = f"{uuid.uuid4().hex}_{safe_name}"
        if ext in IMAGE_EXTENSIONS:
            upload_dir = STATIC_DIR / "uploads" / "images"
        else:
            upload_dir = UPLOADED_FILES_DIR
        os.makedirs(upload_dir, exist_ok=True)

        file_path = os.path.abspath(os.path.join(upload_dir, unique_name))
        if os.path.commonpath([file_path, os.path.abspath(upload_dir)]) != os.path.abspath(upload_dir):
            raise HTTPException(status_code=400, detail="Invalid filename")
        await save_upload_with_limit(file, file_path)

        if ext in IMAGE_EXTENSIONS:
            selected_model = (model or "").strip().lower()
            backend_model = (model_runtime.current_model_name or "").strip().lower()
            use_local_vision = selected_model != "api" and backend_model != "api"
            if use_local_vision:
                try:
                    description = await asyncio.to_thread(vision_service.describe_image, file_path)
                except Exception as exc:
                    description = f"Image analysis failed: {str(exc)}"
            else:
                description = f"Doctor uploaded an image named {safe_name}. The original image is attached for API vision models."

            return {
                "status": "success",
                "type": "image",
                "url": f"/static/uploads/images/{unique_name}",
                "data_url": build_image_data_url(file_path, ext),
                "filename": safe_name,
                "description": description,
            }

        try:
            try:
                with open(file_path, "r", encoding="utf-8") as source:
                    content = source.read()
            except UnicodeDecodeError:
                try:
                    with open(file_path, "r", encoding="gbk") as source:
                        content = source.read()
                except UnicodeDecodeError:
                    with open(file_path, "r", encoding="latin-1") as source:
                        content = source.read()

            content = content.replace("\x00", "")
            if len(content) > 2000:
                content = content[:2000] + "\n...(truncated due to length limit)"

            return {
                "status": "success",
                "type": "file",
                "filename": safe_name,
                "content": content,
                "content_preview": content[:100] + ("..." if len(content) > 100 else ""),
            }
        except Exception as exc:
            return {"status": "error", "message": f"Failed to read file: {str(exc)}"}

    except HTTPException:
        raise
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/upload_image")
async def upload_image_deprecated(file: UploadFile = File(...)):
    return await upload_file(file, model="")


@router.get("/get_vmd_files")
def get_vmd_files():
    try:
        vmd_path = (Path(MMD_DIRECTORY) / "motion").resolve()
        manifest_entries = motion_ai.get_motion_file_entries()
        motions_by_url = {}

        for entry in manifest_entries:
            url = entry.get("url")
            if url:
                motions_by_url[url] = dict(entry)

        if vmd_path.exists() and not motion_ai.manifest_enabled:
            for path in sorted(vmd_path.glob("*.vmd"), key=lambda item: item.name.lower()):
                url = mmd_asset_url(path)
                motions_by_url.setdefault(
                    url,
                    {
                        "id": path.stem,
                        "name": path.stem,
                        "file": path.name,
                        "url": url,
                        "description": "",
                        "tags": [],
                        "playback": "loop",
                        "enabled": True,
                    },
                )

        motions = list(motions_by_url.values())
        return JSONResponse(
            content={
                "motion_dir": str(vmd_path),
                "manifest_enabled": motion_ai.manifest_enabled,
                "motion_manifest_path": motion_ai.motion_manifest_path,
                "motions": motions,
                "vmd_files": [motion["url"] for motion in motions],
            }
        )
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)


def mmd_asset_url(path):
    rel = Path(path).resolve().relative_to(Path(MMD_DIRECTORY).resolve()).as_posix()
    return "/MMD/" + quote(rel, safe="/:@!$&'()*+,;=-._~")


@router.get("/get_try_vmd_files")
def get_try_vmd_files():
    try:
        vmd_path = (Path(MMD_DIRECTORY) / "motion" / "try").resolve()
        if not vmd_path.exists():
            raise FileNotFoundError(f"Try motion directory not found: {vmd_path}")
        files = sorted(
            vmd_path.rglob("*.vmd"),
            key=lambda item: item.relative_to(vmd_path).as_posix().lower(),
        )
        motions = [
            {
                "name": path.stem,
                "relative_path": path.relative_to(vmd_path).as_posix(),
                "url": mmd_asset_url(path),
            }
            for path in files
        ]
        return JSONResponse(
            content={
                "motion_dir": str(vmd_path),
                "count": len(motions),
                "motions": motions,
                "vmd_files": [motion["url"] for motion in motions],
            }
        )
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)
