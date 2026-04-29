#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
PRINTER = os.environ.get("PRINTER", "KX-MB1500")
ADMIN_ID = int(os.environ["ADMIN_ID"])
INVITE_CODE_FILE = "/opt/printbot/invite_code.txt"
USERS_FILE = "/opt/printbot/users.json"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

PRINT_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif",
    ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp", ".txt", ".csv", ".rtf",
}

OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp", ".rtf", ".csv", ".txt",
}


def _load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_users(users: dict) -> None:
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _get_invite_code() -> str:
    if os.path.exists(INVITE_CODE_FILE):
        with open(INVITE_CODE_FILE, "r") as f:
            return f.read().strip()
    return ""


def _set_invite_code(code: str) -> None:
    with open(INVITE_CODE_FILE, "w") as f:
        f.write(code)


def _is_authorized(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    users = _load_users()
    return str(user_id) in users


def _convert_to_pdf(src: str, tmp_dir: str) -> str:
    subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmp_dir, src],
        check=True,
        timeout=120,
    )
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(tmp_dir, base + ".pdf")


def _print_file(path: str) -> str:
    result = subprocess.run(
        ["lp", "-d", PRINTER, "-o", "PageSize=A4", "-o", "fit-to-page", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _extract_job_id(lp_output: str) -> str:
    m = re.search(r"request id is (\S+)", lp_output)
    if m:
        return m.group(1)
    return ""


def _get_job_status(job_id: str) -> str:
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


def _format_status(status: str, job_id: str, file_name: str) -> str:
    if status.startswith("error:"):
        reason = status.split(":", 1)[1]
        return f"\u274c Ошибка печати: {file_name}\nЗадание: {job_id}\nПричина: {reason}"

    labels = {
        "queued": ("\u23f3", "В очереди"),
        "completed": ("\u2705", "Напечатано"),
    }
    emoji, label = labels.get(status, ("", status))
    return f"{emoji} {label}: {file_name}\nЗадание: {job_id}"


async def _poll_job(msg, job_id: str, file_name: str) -> None:
    for _ in range(30):
        await asyncio.sleep(2)
        status = _get_job_status(job_id)
        if status == "completed":
            await msg.edit_text(_format_status("completed", job_id, file_name))
            return
        if status.startswith("error:"):
            await msg.edit_text(_format_status(status, job_id, file_name))
            return
    await msg.edit_text(
        _format_status("queued", job_id, file_name) + "\n(таймаут ожидания)"
    )


async def _send_and_track(msg, print_path: str, file_name: str, user) -> None:
    lp_out = _print_file(print_path)
    job_id = _extract_job_id(lp_out)
    logger.info(
        "User %s (%d) printed %s, job %s",
        user.full_name, user.id, file_name, job_id,
    )
    await msg.edit_text(_format_status("queued", job_id, file_name))
    await _poll_job(msg, job_id, file_name)


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if _is_authorized(user_id):
        await update.message.reply_text(
            "\U0001f5a8 Принт-бот\n\n"
            "Отправь документ или фото — я напечатаю.\n"
            "Форматы: PDF, изображения, Word, Excel, PowerPoint, текст.\n\n"
            "/status — статус принтера"
        )
    else:
        await update.message.reply_text(
            "\U0001f512 Доступ ограничен.\n"
            "Введите код: /auth <код>"
        )


async def auth(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if _is_authorized(user.id):
        await update.message.reply_text("\u2705 Вы уже авторизованы.")
        return

    if not ctx.args:
        await update.message.reply_text("Использование: /auth <код>")
        return

    code = ctx.args[0]
    invite_code = _get_invite_code()
    if not invite_code:
        await update.message.reply_text(
            "\u274c Инвайт-код не задан. Обратитесь к администратору."
        )
        return

    if code != invite_code:
        await update.message.reply_text("\u274c Неверный код.")
        logger.warning("Failed auth attempt by %s (%d)", user.full_name, user.id)
        return

    users = _load_users()
    users[str(user.id)] = {
        "name": user.full_name,
        "username": user.username or "",
    }
    _save_users(users)
    logger.info("User %s (%d) authorized", user.full_name, user.id)
    await update.message.reply_text(
        "\u2705 Доступ получен! Отправляйте документы для печати."
    )


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return

    if not ctx.args:
        current = _get_invite_code()
        if current:
            await update.message.reply_text(f"Текущий код: {current}")
        else:
            await update.message.reply_text(
                "Код не задан. Использование: /code <новый_код>"
            )
        return

    new_code = ctx.args[0]
    _set_invite_code(new_code)
    await update.message.reply_text(f"\u2705 Инвайт-код изменён: {new_code}")


async def cmd_users(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return

    users = _load_users()
    if not users:
        await update.message.reply_text(
            "Нет авторизованных пользователей (кроме админа)."
        )
        return

    lines = []
    for uid, info in users.items():
        name = info.get("name", "")
        username = info.get("username", "")
        at = f" @{username}" if username else ""
        lines.append(f"\u2022 {name}{at} (ID: {uid})")

    await update.message.reply_text("Пользователи:\n" + "\n".join(lines))


async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return

    if not ctx.args:
        await update.message.reply_text("Использование: /revoke <user_id>")
        return

    target_id = ctx.args[0]
    users = _load_users()
    if target_id not in users:
        await update.message.reply_text(f"Пользователь {target_id} не найден.")
        return

    removed = users.pop(target_id)
    _save_users(users)
    await update.message.reply_text(
        f"\u2705 Доступ отозван у {removed.get('name', target_id)}."
    )


async def whoami(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    authorized = (
        "\u2705 авторизован" if _is_authorized(u.id) else "\u274c не авторизован"
    )
    role = "\U0001f451 админ" if u.id == ADMIN_ID else authorized
    await update.message.reply_text(
        f"ID: {u.id}\nИмя: {u.full_name}\nUsername: @{u.username}\nСтатус: {role}"
    )


async def status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update.effective_user.id):
        await update.message.reply_text("\U0001f512 Нет доступа.")
        return
    result = subprocess.run(
        ["lpstat", "-p", PRINTER, "-t"],
        capture_output=True, text=True, timeout=10,
    )
    await update.message.reply_text(result.stdout.strip() or "Нет информации.")


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text(
            "\U0001f512 Нет доступа. Используйте /auth <код>"
        )
        return

    doc = update.message.document
    file_name = doc.file_name or "file"
    ext = os.path.splitext(file_name)[1].lower()

    if ext not in PRINT_EXTENSIONS:
        await update.message.reply_text(f"Формат {ext} не поддерживается.")
        return

    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("Файл слишком большой (макс. 20 МБ).")
        return

    msg = await update.message.reply_text("\u2b07\ufe0f Скачиваю...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file = await ctx.bot.get_file(doc.file_id)
        local_path = os.path.join(tmp_dir, file_name)
        await file.download_to_drive(local_path)

        print_path = local_path
        if ext in OFFICE_EXTENSIONS:
            await msg.edit_text("\U0001f504 Конвертирую в PDF...")
            try:
                print_path = _convert_to_pdf(local_path, tmp_dir)
            except Exception as e:
                await msg.edit_text(f"\u274c Ошибка конвертации: {e}")
                return

        await msg.edit_text("\U0001f5a8 Отправляю на печать...")
        try:
            await _send_and_track(msg, print_path, file_name, user)
        except Exception as e:
            await msg.edit_text(f"\u274c Ошибка печати: {e}")
            logger.error("Print failed for %s: %s", file_name, e)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text(
            "\U0001f512 Нет доступа. Используйте /auth <код>"
        )
        return

    photo = update.message.photo[-1]
    msg = await update.message.reply_text("\u2b07\ufe0f Скачиваю фото...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        file = await ctx.bot.get_file(photo.file_id)
        local_path = os.path.join(tmp_dir, "photo.jpg")
        await file.download_to_drive(local_path)

        await msg.edit_text("\U0001f5a8 Отправляю на печать...")
        try:
            await _send_and_track(msg, local_path, "photo.jpg", user)
        except Exception as e:
            await msg.edit_text(f"\u274c Ошибка печати: {e}")
            logger.error("Print photo failed: %s", e)


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth", auth))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
