import asyncio
import base64
import os
import re
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from core_services import vision_service
from saya_app.config import MMD_DIRECTORY, STATIC_DIR, UPLOADED_FILES_DIR
from saya_app.model_runtime import model_runtime


router = APIRouter(tags=["media"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
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


def sanitize_upload_filename(filename):
    raw_name = os.path.basename((filename or "upload").replace("\\", "/"))
    stem, ext = os.path.splitext(raw_name)
    safe_stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._")
    if not safe_stem:
        safe_stem = "upload"
    safe_stem = safe_stem[:80]
    return safe_stem + ext.lower()


async def save_upload_with_limit(file, destination):
    total = 0
    try:
        with open(destination, "wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 10 MB limit")
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
        vmd_path = os.path.join(str(MMD_DIRECTORY), "motion")
        files = [file for file in os.listdir(vmd_path) if file.endswith(".vmd")]
        file_paths = [f"http://127.0.0.1:8000/MMD/motion/{file}" for file in files]
        return JSONResponse(content={"vmd_files": file_paths})
    except Exception as exc:
        return JSONResponse(content={"error": str(exc)}, status_code=500)
