import hashlib
import os
import uuid
from flask import current_app

MAGIC = {
    "pdf": [b"%PDF-"],
    "png": [b"\x89PNG\r\n\x1a\n"],
    "jpg": [b"\xff\xd8\xff"],
    "jpeg": [b"\xff\xd8\xff"],
    "xlsx": [b"PK\x03\x04"],
}
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "csv": "text/csv"}


class UploadError(ValueError):
    pass


def save_evidence(file_storage):
    """Validasi (ekstensi whitelist, magic bytes, ukuran), ganti nama UUID, simpan di luar webroot."""
    name = file_storage.filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in current_app.config["ALLOWED_UPLOAD_EXT"]:
        raise UploadError("Tipe file tidak diizinkan. Gunakan: pdf, png, jpg, xlsx, csv.")
    data = file_storage.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
    if not data:
        raise UploadError("File kosong.")
    if len(data) > current_app.config["MAX_CONTENT_LENGTH"]:
        raise UploadError("Ukuran file maksimal 5 MB.")
    if ext in MAGIC and not any(data.startswith(m) for m in MAGIC[ext]):
        raise UploadError("Isi file tidak sesuai dengan ekstensinya.")
    if ext == "csv":
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise UploadError("File CSV harus berenkode UTF-8.")
    folder = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(folder, exist_ok=True)
    stored = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(folder, stored), "wb") as f:
        f.write(data)
    return dict(original_name=os.path.basename(name)[:255], stored_path=stored, mime_type=MIME[ext],
                size_bytes=len(data), checksum=hashlib.sha256(data).hexdigest())
