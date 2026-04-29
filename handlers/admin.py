from telegram import Update
from telegram.ext import ContextTypes

from auth import get_invite_code, load_users, save_users, set_invite_code
from config import ADMIN_ID


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
