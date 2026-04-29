from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import jobs
from config import NUP_SCALE


def sheets_word(n: int) -> str:
    if 11 <= n % 100 <= 19:
        return f"{n} листов"
    last = n % 10
    if last == 1:
        return f"{n} лист"
    if 2 <= last <= 4:
        return f"{n} листа"
    return f"{n} листов"


def options_text(job: dict) -> str:
    pages_str = "все" if job["pages"] == "all" else job["pages"]
    selected = jobs.count_selected_pages(job)
    sheet_count = jobs.calc_sheets(job)
    nup = job.get("nup", 1)
    scale = NUP_SCALE.get(nup, 100)

    nup_str = f"{nup} на листе (~{scale}%)" if nup > 1 else "1 на листе"
    copies_total = sheet_count * job["copies"]

    lines = [
        f"\U0001f4c4 {job['file_name']} ({job['total_pages']} стр.)",
        "",
        "\u2699\ufe0f Настройки печати:",
        f"\u2022 Страницы: {pages_str} ({selected} стр.)",
        f"\u2022 Копии: {job['copies']}",
        f"\u2022 Размещение: {nup_str}",
        "",
        f"\U0001f4e4 Итого: {sheets_word(copies_total)}",
    ]
    return "\n".join(lines)


def options_kb(key: str) -> InlineKeyboardMarkup:
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


def pages_kb(key: str) -> InlineKeyboardMarkup:
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


def copies_kb(key: str) -> InlineKeyboardMarkup:
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


def fit_kb(key: str) -> InlineKeyboardMarkup:
    job = jobs.get(key)
    if not job:
        return InlineKeyboardMarkup([])
    selected = jobs.count_selected_pages(job)
    buttons = []

    for nup in (1, 2, 4):
        sheet_count = -(-selected // nup)
        scale = NUP_SCALE.get(nup, 100)
        if nup == 1:
            label = f"Без уменьшения \u2014 {sheets_word(sheet_count)}"
        else:
            label = (
                f"~{scale}% \u2014 {sheets_word(sheet_count)} ({nup} на листе)"
            )
        if nup == 1 or sheet_count < selected:
            buttons.append([
                InlineKeyboardButton(label, callback_data=f"np:{nup}:{key}"),
            ])

    buttons.append(
        [InlineKeyboardButton("\u2b05\ufe0f Назад", callback_data=f"bk:{key}")],
    )
    return InlineKeyboardMarkup(buttons)
