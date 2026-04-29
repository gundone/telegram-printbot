import os
import shutil
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

import jobs
import keyboards as kb
from auth import is_authorized
from config import OFFICE_EXTENSIONS, PRINT_EXTENSIONS
from printing import convert_to_pdf, get_page_count, send_and_track


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
