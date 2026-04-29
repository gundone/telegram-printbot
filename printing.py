import asyncio
import logging
import os
import re
import subprocess

from config import PRINTER

logger = logging.getLogger(__name__)


def convert_to_pdf(src: str, tmp_dir: str) -> str:
    subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf",
         "--outdir", tmp_dir, src],
        check=True, timeout=120,
    )
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(tmp_dir, base + ".pdf")


def get_page_count(pdf_path: str) -> int:
    result = subprocess.run(
        ["pdfinfo", pdf_path],
        capture_output=True, text=True, timeout=10,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    return 1


def _build_lp_command(path: str, job: dict) -> list[str]:
    cmd = ["lp", "-d", PRINTER, "-o", "PageSize=A4", "-o", "fit-to-page"]
    pages = job.get("pages", "all")
    if pages != "all":
        cmd.extend(["-P", pages])
    copies = job.get("copies", 1)
    if copies > 1:
        cmd.extend(["-n", str(copies)])
    nup = job.get("nup", 1)
    if nup > 1:
        cmd.extend(["-o", f"number-up={nup}",
                     "-o", "number-up-layout=lrtb"])
    cmd.append(path)
    return cmd


def print_file(path: str, job: dict) -> str:
    cmd = _build_lp_command(path, job)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def extract_job_id(lp_output: str) -> str:
    m = re.search(r"request id is (\S+)", lp_output)
    if m:
        return m.group(1)
    return ""


def get_job_status(job_id: str) -> str:
    active = subprocess.run(
        ["lpstat", "-o", PRINTER],
        capture_output=True, text=True, timeout=10,
    )
    if job_id in active.stdout:
        return "queued"

    completed = subprocess.run(
        ["lpstat", "-W", "completed", "-o", PRINTER],
        capture_output=True, text=True, timeout=10,
    )
    if job_id in completed.stdout:
        return "completed"

    errlog = subprocess.run(
        ["grep", "-i", job_id.split("-")[-1], "/var/log/cups/error_log"],
        capture_output=True, text=True, timeout=10,
    )
    for line in reversed(errlog.stdout.splitlines()):
        low = line.lower()
        if "error" in low or "fail" in low or "stop" in low:
            reason = line.split("]")[-1].strip() if "]" in line else line
            return f"error:{reason}"

    return "completed"


def format_status(status: str, job_id: str, file_name: str) -> str:
    if status.startswith("error:"):
        reason = status.split(":", 1)[1]
        return (
            f"\u274c Ошибка печати: {file_name}\n"
            f"Задание: {job_id}\nПричина: {reason}"
        )
    labels = {
        "queued": ("\u23f3", "В очереди"),
        "completed": ("\u2705", "Напечатано"),
    }
    emoji, label = labels.get(status, ("", status))
    return f"{emoji} {label}: {file_name}\nЗадание: {job_id}"


async def poll_job(msg, job_id: str, file_name: str) -> None:
    for _ in range(30):
        await asyncio.sleep(2)
        st = get_job_status(job_id)
        if st == "completed":
            await msg.edit_text(format_status("completed", job_id, file_name))
            return
        if st.startswith("error:"):
            await msg.edit_text(format_status(st, job_id, file_name))
            return
    await msg.edit_text(
        format_status("queued", job_id, file_name) + "\n(таймаут ожидания)"
    )


async def send_and_track(msg, path: str, file_name: str, user, job: dict) -> None:
    lp_out = print_file(path, job)
    job_id = extract_job_id(lp_out)
    logger.info(
        "User %s (%d) printed %s, job %s, opts: pages=%s copies=%d nup=%d",
        user.full_name, user.id, file_name, job_id,
        job.get("pages", "all"), job.get("copies", 1), job.get("nup", 1),
    )
    await msg.edit_text(format_status("queued", job_id, file_name))
    await poll_job(msg, job_id, file_name)
