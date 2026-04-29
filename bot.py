#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import tempfile

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
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
PENDING_DIR = "/opt/printbot/pending"

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

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif"}

# nup → approximate text scale (2-on-1 landscape side-by-side ≈ 70%)
_NUP_SCALE = {1: 100, 2: 70, 4: 50}

# {job_key: {path, file_name, user_id, pages, total_pages, copies, nup}}
_pending_jobs: dict[str, dict] = {}

# ── Auth helpers ──────────────────────────────────────────────


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


# ── File / print helpers ─────────────────────────────────────


def _convert_to_pdf(src: str, tmp_dir: str) -> str:
    subprocess.run(
        ["libreoffice", "--headless", "--convert-to", "pdf",
         "--outdir", tmp_dir, src],
        check=True, timeout=120,
    )
    base = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(tmp_dir, base + ".pdf")


def _get_page_count(pdf_path: str) -> int:
    result = subprocess.run(
        ["pdfinfo", pdf_path],
        capture_output=True, text=True, timeout=10,
    )
    for line in result.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    return 1


def _count_selected_pages(job: dict) -> int:
    """Calculate how many pages are selected by the page range."""
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


def _calc_sheets(job: dict) -> int:
    """Calculate how many physical sheets will be printed."""
    selected = _count_selected_pages(job)
    nup = job.get("nup", 1)
    return -(-selected // nup)  # ceil division


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


def _print_file_with_options(path: str, job: dict) -> str:
    cmd = _build_lp_command(path, job)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def _extract_job_id(lp_output: str) -> str:
    m = re.search(r"request id is (\S+)", lp_output)
    if m:
        return m.group(1)
    return ""


# ── Job status tracking ──────────────────────────────────────


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


async def _poll_job(msg, job_id: str, file_name: str) -> None:
    for _ in range(30):
        await asyncio.sleep(2)
        st = _get_job_status(job_id)
        if st == "completed":
            await msg.edit_text(_format_status("completed", job_id, file_name))
            return
        if st.startswith("error:"):
            await msg.edit_text(_format_status(st, job_id, file_name))
            return
    await msg.edit_text(
        _format_status("queued", job_id, file_name) + "\n(таймаут ожидания)"
    )


async def _send_and_track(msg, path: str, file_name: str, user, job: dict) -> None:
    lp_out = _print_file_with_options(path, job)
    job_id = _extract_job_id(lp_out)
    logger.info(
        "User %s (%d) printed %s, job %s, opts: pages=%s copies=%d nup=%d",
        user.full_name, user.id, file_name, job_id,
        job.get("pages", "all"), job.get("copies", 1), job.get("nup", 1),
    )
    await msg.edit_text(_format_status("queued", job_id, file_name))
    await _poll_job(msg, job_id, file_name)


# ── Pending job management ───────────────────────────────────


def _create_pending_job(
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


def _cleanup_pending(key: str) -> None:
    job = _pending_jobs.pop(key, None)
    if job:
        job_dir = os.path.dirname(job["path"])
        shutil.rmtree(job_dir, ignore_errors=True)


# ── Options UI ────────────────────────────────────────────────


def _options_text(job: dict) -> str:
    pages_str = "все" if job["pages"] == "all" else job["pages"]
    selected = _count_selected_pages(job)
    sheets = _calc_sheets(job)
    nup = job.get("nup", 1)
    scale = _NUP_SCALE.get(nup, 100)

    nup_str = f"{nup} на листе (~{scale}%)" if nup > 1 else "1 на листе"
    sheets_word = _sheets_word(sheets)
    copies_total = sheets * job["copies"]

    lines = [
        f"\U0001f4c4 {job['file_name']} ({job['total_pages']} стр.)",
        "",
        f"\u2699\ufe0f Настройки печати:",
        f"\u2022 Страницы: {pages_str} ({selected} стр.)",
        f"\u2022 Копии: {job['copies']}",
        f"\u2022 Размещение: {nup_str}",
        "",
        f"\U0001f4e4 Итого: {copies_total} {sheets_word}",
    ]
    return "\n".join(lines)


def _sheets_word(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return f"{n} листов"
    last = n % 10
    if last == 1:
        return f"{n} лист"
    if 2 <= last <= 4:
        return f"{n} листа"
    return f"{n} листов"


def _options_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4c4 Страницы", callback_data=f"pg:{key}"),
            InlineKeyboardButton("\U0001f4cb Копии", callback_data=f"cp:{key}"),
        ],
        [
            InlineKeyboardButton(
                "\U0001f4d0 Уместить на меньше листов",
                callback_data=f"ft:{key}",
            ),
        ],
        [InlineKeyboardButton("\U0001f5a8 Печатать", callback_data=f"go:{key}")],
    ])


def _pages_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Все", callback_data=f"ps:all:{key}"),
            InlineKeyboardButton("Первая", callback_data=f"ps:1:{key}"),
            InlineKeyboardButton("Последняя", callback_data=f"ps:last:{key}"),
        ],
        [
            InlineKeyboardButton(
                "\u270f\ufe0f Ввести диапазон", callback_data=f"ps:inp:{key}",
            ),
        ],
        [InlineKeyboardButton("\u2b05\ufe0f Назад", callback_data=f"bk:{key}")],
    ])


def _copies_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("1", callback_data=f"cn:1:{key}"),
            InlineKeyboardButton("2", callback_data=f"cn:2:{key}"),
            InlineKeyboardButton("3", callback_data=f"cn:3:{key}"),
            InlineKeyboardButton("5", callback_data=f"cn:5:{key}"),
            InlineKeyboardButton("10", callback_data=f"cn:10:{key}"),
        ],
        [InlineKeyboardButton("\u2b05\ufe0f Назад", callback_data=f"bk:{key}")],
    ])


def _fit_keyboard(key: str) -> InlineKeyboardMarkup:
    job = _pending_jobs.get(key)
    if not job:
        return InlineKeyboardMarkup([])
    selected = _count_selected_pages(job)
    buttons = []

    for nup in (1, 2, 4):
        sheets = -(-selected // nup)
        scale = _NUP_SCALE.get(nup, 100)
        if nup == 1:
            label = f"Без уменьшения \u2014 {_sheets_word(sheets)}"
        else:
            label = f"~{scale}% \u2014 {_sheets_word(sheets)} ({nup} на листе)"
        # Only show if it actually reduces sheets vs nup=1
        if nup == 1 or sheets < selected:
            buttons.append([
                InlineKeyboardButton(label, callback_data=f"np:{nup}:{key}"),
            ])

    buttons.append(
        [InlineKeyboardButton("\u2b05\ufe0f Назад", callback_data=f"bk:{key}")],
    )
    return InlineKeyboardMarkup(buttons)


# ── Callback query handler ────────────────────────────────────


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("pg:"):
        await _cb_pages_menu(query, data)
    elif data.startswith("ps:"):
        await _cb_pages_set(query, data, ctx, user_id)
    elif data.startswith("cp:"):
        await _cb_copies_menu(query, data)
    elif data.startswith("cn:"):
        await _cb_copies_set(query, data)
    elif data.startswith("ft:"):
        await _cb_fit_menu(query, data)
    elif data.startswith("np:"):
        await _cb_nup_set(query, data)
    elif data.startswith("bk:"):
        await _cb_back(query, data)
    elif data.startswith("go:"):
        await _cb_print(query, data, update.effective_user)


async def _cb_pages_menu(query, data: str) -> None:
    key = data.split(":")[1]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    await query.edit_message_text(
        f"\U0001f4c4 Выберите страницы (всего {job['total_pages']}):",
        reply_markup=_pages_keyboard(key),
    )


async def _cb_pages_set(query, data: str, ctx, user_id: int) -> None:
    parts = data.split(":")
    value, key = parts[1], parts[2]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return

    if value == "inp":
        ctx.user_data["awaiting_pages"] = key
        await query.edit_message_text(
            f"Введите диапазон страниц (1\u2013{job['total_pages']}).\n"
            "Примеры: 1-3  или  1,3,5  или  2-4,7"
        )
        return
    if value == "all":
        job["pages"] = "all"
    elif value == "last":
        job["pages"] = str(job["total_pages"])
    else:
        job["pages"] = value

    await query.edit_message_text(
        _options_text(job), reply_markup=_options_keyboard(key),
    )


async def _cb_copies_menu(query, data: str) -> None:
    key = data.split(":")[1]
    if key not in _pending_jobs:
        await query.edit_message_text("Задание не найдено.")
        return
    await query.edit_message_text(
        "\U0001f4cb Количество копий:",
        reply_markup=_copies_keyboard(key),
    )


async def _cb_copies_set(query, data: str) -> None:
    parts = data.split(":")
    value, key = int(parts[1]), parts[2]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    job["copies"] = value
    await query.edit_message_text(
        _options_text(job), reply_markup=_options_keyboard(key),
    )


async def _cb_fit_menu(query, data: str) -> None:
    key = data.split(":")[1]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    selected = _count_selected_pages(job)
    await query.edit_message_text(
        f"\U0001f4d0 Уместить {selected} стр. на меньше листов:",
        reply_markup=_fit_keyboard(key),
    )


async def _cb_nup_set(query, data: str) -> None:
    parts = data.split(":")
    value, key = int(parts[1]), parts[2]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    job["nup"] = value
    await query.edit_message_text(
        _options_text(job), reply_markup=_options_keyboard(key),
    )


async def _cb_back(query, data: str) -> None:
    key = data.split(":")[1]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    await query.edit_message_text(
        _options_text(job), reply_markup=_options_keyboard(key),
    )


async def _cb_print(query, data: str, user) -> None:
    key = data.split(":")[1]
    job = _pending_jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return

    await query.edit_message_text("\U0001f5a8 Отправляю на печать...")
    try:
        await _send_and_track(
            query.message, job["path"], job["file_name"], user, job,
        )
    except Exception as e:
        await query.edit_message_text(f"\u274c Ошибка печати: {e}")
        logger.error("Print failed for %s: %s", job["file_name"], e)
    finally:
        _cleanup_pending(key)


# ── Text input handler (page range) ──────────────────────────


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    key = ctx.user_data.pop("awaiting_pages", None)
    if not key:
        return
    job = _pending_jobs.get(key)
    if not job:
        await update.message.reply_text("Задание не найдено или истекло.")
        return

    text = update.message.text.strip()
    if not re.match(r"^[\d,\- ]+$", text):
        await update.message.reply_text(
            "\u274c Неверный формат. Примеры: 1-3  или  1,3,5  или  2-4,7"
        )
        ctx.user_data["awaiting_pages"] = key
        return

    job["pages"] = text.replace(" ", "")
    await update.message.reply_text(
        _options_text(job), reply_markup=_options_keyboard(key),
    )


# ── Download and prepare file ─────────────────────────────────


async def _download_and_prepare(
    ctx, doc_file_id: str, file_name: str, ext: str, msg,
) -> tuple[str, str, int]:
    """Download file, convert if needed, return (print_path, pdf_path, pages)."""
    tmp_dir = tempfile.mkdtemp()
    tg_file = await ctx.bot.get_file(doc_file_id)
    local_path = os.path.join(tmp_dir, file_name)
    await tg_file.download_to_drive(local_path)

    print_path = local_path
    if ext in OFFICE_EXTENSIONS:
        await msg.edit_text("\U0001f504 Конвертирую в PDF...")
        print_path = _convert_to_pdf(local_path, tmp_dir)

    pages = 1
    if ext == ".pdf" or ext in OFFICE_EXTENSIONS:
        pages = _get_page_count(print_path)

    return print_path, tmp_dir, pages


# ── Command handlers ──────────────────────────────────────────


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if _is_authorized(user_id):
        await update.message.reply_text(
            "\U0001f5a8 Принт-бот\n\n"
            "Отправь документ или фото \u2014 я напечатаю.\n"
            "Для многостраничных документов предложу настройки.\n\n"
            "Форматы: PDF, изображения, Word, Excel, PowerPoint, текст.\n\n"
            "/status \u2014 статус принтера"
        )
    else:
        await update.message.reply_text(
            "\U0001f512 Доступ ограничен.\nВведите код: /auth <код>"
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

    _set_invite_code(ctx.args[0])
    await update.message.reply_text(f"\u2705 Инвайт-код изменён: {ctx.args[0]}")


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


# ── Document / photo handlers ─────────────────────────────────


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

    try:
        print_path, tmp_dir, pages = await _download_and_prepare(
            ctx, doc.file_id, file_name, ext, msg,
        )
    except Exception as e:
        await msg.edit_text(f"\u274c Ошибка: {e}")
        return

    if pages > 1:
        key = _create_pending_job(print_path, file_name, user.id, pages)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        job = _pending_jobs[key]
        await msg.edit_text(
            _options_text(job), reply_markup=_options_keyboard(key),
        )
    else:
        await msg.edit_text("\U0001f5a8 Отправляю на печать...")
        try:
            job = {"pages": "all", "copies": 1, "nup": 1}
            await _send_and_track(msg, print_path, file_name, user, job)
        except Exception as e:
            await msg.edit_text(f"\u274c Ошибка печати: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not _is_authorized(user.id):
        await update.message.reply_text(
            "\U0001f512 Нет доступа. Используйте /auth <код>"
        )
        return

    photo = update.message.photo[-1]
    msg = await update.message.reply_text("\u2b07\ufe0f Скачиваю фото...")

    tmp_dir = tempfile.mkdtemp()
    tg_file = await ctx.bot.get_file(photo.file_id)
    local_path = os.path.join(tmp_dir, "photo.jpg")
    await tg_file.download_to_drive(local_path)

    await msg.edit_text("\U0001f5a8 Отправляю на печать...")
    try:
        job = {"pages": "all", "copies": 1, "nup": 1}
        await _send_and_track(msg, local_path, "photo.jpg", user, job)
    except Exception as e:
        await msg.edit_text(f"\u274c Ошибка печати: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────


def main() -> None:
    os.makedirs(PENDING_DIR, exist_ok=True)
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("auth", auth))
    app.add_handler(CommandHandler("code", cmd_code))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
    )

    logger.info("Bot started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
