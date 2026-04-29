import logging
import subprocess

from telegram import Update
from telegram.ext import ContextTypes

from auth import (
    get_invite_code,
    is_authorized,
    load_users,
    save_users,
)
from config import ADMIN_ID, PRINTER

logger = logging.getLogger(__name__)


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
