import logging
import os
import re
import shutil
import subprocess
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

import jobs
import keyboards as kb
from auth import (
    get_invite_code,
    is_authorized,
    load_users,
    save_users,
    set_invite_code,
)
from config import ADMIN_ID, OFFICE_EXTENSIONS, PRINT_EXTENSIONS, PRINTER
from printing import convert_to_pdf, get_page_count, send_and_track

logger = logging.getLogger(__name__)


# ── Commands ──────────────────────────────────────────────────


async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if is_authorized(update.effective_user.id):
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
    if is_authorized(user.id):
        await update.message.reply_text("\u2705 Вы уже авторизованы.")
        return

    if not ctx.args:
        await update.message.reply_text("Использование: /auth <код>")
        return

    invite_code = get_invite_code()
    if not invite_code:
        await update.message.reply_text(
            "\u274c Инвайт-код не задан. Обратитесь к администратору."
        )
        return

    if ctx.args[0] != invite_code:
        await update.message.reply_text("\u274c Неверный код.")
        logger.warning("Failed auth attempt by %s (%d)", user.full_name, user.id)
        return

    users = load_users()
    users[str(user.id)] = {
        "name": user.full_name,
        "username": user.username or "",
    }
    save_users(users)
    logger.info("User %s (%d) authorized", user.full_name, user.id)
    await update.message.reply_text(
        "\u2705 Доступ получен! Отправляйте документы для печати."
    )


async def cmd_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return

    if not ctx.args:
        current = get_invite_code()
        text = f"Текущий код: {current}" if current else (
            "Код не задан. Использование: /code <новый_код>"
        )
        await update.message.reply_text(text)
        return

    set_invite_code(ctx.args[0])
    await update.message.reply_text(f"\u2705 Инвайт-код изменён: {ctx.args[0]}")


async def cmd_users(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return

    users = load_users()
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
    users = load_users()
    if target_id not in users:
        await update.message.reply_text(f"Пользователь {target_id} не найден.")
        return

    removed = users.pop(target_id)
    save_users(users)
    await update.message.reply_text(
        f"\u2705 Доступ отозван у {removed.get('name', target_id)}."
    )


async def whoami(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    authorized = (
        "\u2705 авторизован" if is_authorized(u.id) else "\u274c не авторизован"
    )
    role = "\U0001f451 админ" if u.id == ADMIN_ID else authorized
    await update.message.reply_text(
        f"ID: {u.id}\nИмя: {u.full_name}\nUsername: @{u.username}\nСтатус: {role}"
    )


async def status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update.effective_user.id):
        await update.message.reply_text("\U0001f512 Нет доступа.")
        return
    result = subprocess.run(
        ["lpstat", "-p", PRINTER, "-t"],
        capture_output=True, text=True, timeout=10,
    )
    await update.message.reply_text(result.stdout.strip() or "Нет информации.")


# ── Document / photo handlers ─────────────────────────────────


async def _download_and_prepare(ctx, file_id, file_name, ext, msg):
    tmp_dir = tempfile.mkdtemp()
    tg_file = await ctx.bot.get_file(file_id)
    local_path = os.path.join(tmp_dir, file_name)
    await tg_file.download_to_drive(local_path)

    print_path = local_path
    if ext in OFFICE_EXTENSIONS:
        await msg.edit_text("\U0001f504 Конвертирую в PDF...")
        print_path = convert_to_pdf(local_path, tmp_dir)

    pages = 1
    if ext == ".pdf" or ext in OFFICE_EXTENSIONS:
        pages = get_page_count(print_path)

    return print_path, tmp_dir, pages


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
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
        key = jobs.create(print_path, file_name, user.id, pages)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        job = jobs.get(key)
        await msg.edit_text(
            kb.options_text(job), reply_markup=kb.options_kb(key),
        )
    else:
        await msg.edit_text("\U0001f5a8 Отправляю на печать...")
        try:
            job = {"pages": "all", "copies": 1, "nup": 1}
            await send_and_track(msg, print_path, file_name, user, job)
        except Exception as e:
            await msg.edit_text(f"\u274c Ошибка печати: {e}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
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
        await send_and_track(msg, local_path, "photo.jpg", user, job)
    except Exception as e:
        await msg.edit_text(f"\u274c Ошибка печати: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Callback query handler ────────────────────────────────────


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("pg:"):
        await _cb_pages_menu(query, data)
    elif data.startswith("ps:"):
        await _cb_pages_set(query, data, ctx)
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
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    await query.edit_message_text(
        f"\U0001f4c4 Выберите страницы (всего {job['total_pages']}):",
        reply_markup=kb.pages_kb(key),
    )


async def _cb_pages_set(query, data: str, ctx) -> None:
    parts = data.split(":")
    value, key = parts[1], parts[2]
    job = jobs.get(key)
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
        kb.options_text(job), reply_markup=kb.options_kb(key),
    )


async def _cb_copies_menu(query, data: str) -> None:
    key = data.split(":")[1]
    if not jobs.get(key):
        await query.edit_message_text("Задание не найдено.")
        return
    await query.edit_message_text(
        "\U0001f4cb Количество копий:",
        reply_markup=kb.copies_kb(key),
    )


async def _cb_copies_set(query, data: str) -> None:
    parts = data.split(":")
    value, key = int(parts[1]), parts[2]
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    job["copies"] = value
    await query.edit_message_text(
        kb.options_text(job), reply_markup=kb.options_kb(key),
    )


async def _cb_fit_menu(query, data: str) -> None:
    key = data.split(":")[1]
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    selected = jobs.count_selected_pages(job)
    await query.edit_message_text(
        f"\U0001f4d0 Уместить {selected} стр. на меньше листов:",
        reply_markup=kb.fit_kb(key),
    )


async def _cb_nup_set(query, data: str) -> None:
    parts = data.split(":")
    value, key = int(parts[1]), parts[2]
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    job["nup"] = value
    await query.edit_message_text(
        kb.options_text(job), reply_markup=kb.options_kb(key),
    )


async def _cb_back(query, data: str) -> None:
    key = data.split(":")[1]
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    await query.edit_message_text(
        kb.options_text(job), reply_markup=kb.options_kb(key),
    )


async def _cb_print(query, data: str, user) -> None:
    key = data.split(":")[1]
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return

    await query.edit_message_text("\U0001f5a8 Отправляю на печать...")
    try:
        await send_and_track(
            query.message, job["path"], job["file_name"], user, job,
        )
    except Exception as e:
        await query.edit_message_text(f"\u274c Ошибка печати: {e}")
        logger.error("Print failed for %s: %s", job["file_name"], e)
    finally:
        jobs.cleanup(key)


# ── Text input (page range) ──────────────────────────────────


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    key = ctx.user_data.pop("awaiting_pages", None)
    if not key:
        return
    job = jobs.get(key)
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
        kb.options_text(job), reply_markup=kb.options_kb(key),
    )
