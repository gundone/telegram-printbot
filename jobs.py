import os
import secrets
import shutil

from config import PENDING_DIR

_pending_jobs: dict[str, dict] = {}


def get(key: str) -> dict | None:
    return _pending_jobs.get(key)


def count_selected_pages(job: dict) -> int:
    pages = job.get("pages", "all")
    total = job["total_pages"]
    if pages == "all":
        return total
    count = 0
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo = max(1, int(lo))
            hi = min(total, int(hi))
            count += max(0, hi - lo + 1)
        elif part.isdigit():
            count += 1
    return max(count, 1)


def calc_sheets(job: dict) -> int:
    selected = count_selected_pages(job)
    nup = job.get("nup", 1)
    return -(-selected // nup)


def create(
    src_path: str, file_name: str, user_id: int, total_pages: int,
) -> str:
    key = secrets.token_hex(4)
    os.makedirs(PENDING_DIR, exist_ok=True)
    job_dir = os.path.join(PENDING_DIR, key)
    os.makedirs(job_dir)
    dest = os.path.join(job_dir, os.path.basename(src_path))
    shutil.copy2(src_path, dest)
    _pending_jobs[key] = {
        "path": dest,
        "file_name": file_name,
        "user_id": user_id,
        "total_pages": total_pages,
        "pages": "all",
        "copies": 1,
        "nup": 1,
    }
    return key


def cleanup(key: str) -> None:
    job = _pending_jobs.pop(key, None)
    if job:
        job_dir = os.path.dirname(job["path"])
        shutil.rmtree(job_dir, ignore_errors=True)
