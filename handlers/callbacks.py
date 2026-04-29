import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

import jobs
import keyboards as kb
from printing import send_and_track

logger = logging.getLogger(__name__)


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
        await _cb_value_set(query, data, "copies")
    elif data.startswith("ft:"):
        await _cb_fit_menu(query, data)
    elif data.startswith("np:"):
        await _cb_value_set(query, data, "nup")
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
        "\U0001f4cb Количество копий:", reply_markup=kb.copies_kb(key),
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


async def _cb_value_set(query, data: str, field: str) -> None:
    parts = data.split(":")
    value, key = int(parts[1]), parts[2]
    job = jobs.get(key)
    if not job:
        await query.edit_message_text("Задание не найдено.")
        return
    job[field] = value
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
