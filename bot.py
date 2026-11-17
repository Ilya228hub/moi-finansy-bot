
import os
import asyncio
import sqlite3
import html
from datetime import datetime
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode


# =========================
# НАСТРОЙКИ
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = os.getenv("OWNER_ID")

if not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("Не заданы BOT_TOKEN и OWNER_ID")

OWNER_ID = int(OWNER_ID)
DB_NAME = "finance.db"


# =========================
# БАЗА ДАННЫХ
# =========================

def init_db():
    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS operations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                category TEXT NOT NULL,
                comment TEXT,
                created_at TEXT NOT NULL
            )
        """)


def db_execute(query, params=()):
    with sqlite3.connect(DB_NAME) as db:
        return db.execute(query, params)


def get_balance():
    with sqlite3.connect(DB_NAME) as db:
        row = db.execute("""
            SELECT COALESCE(SUM(
                CASE WHEN type = 'income'
                THEN amount ELSE -amount END
            ), 0)
            FROM operations
            WHERE owner_id = ?
        """, (OWNER_ID,)).fetchone()

    return row[0]


# =========================
# ФОРМАТИРОВАНИЕ
# =========================

def money(cents):
    return (
        f"{Decimal(cents) / 100:,.2f}"
        .replace(",", " ")
        .replace(".", ",")
        + " ₽"
    )


def parse_amount(text):
    value = Decimal(
        text.strip().replace(" ", "").replace(",", ".")
    )

    if not value.is_finite() or value <= 0:
        raise ValueError

    if value > Decimal("100000000"):
        raise ValueError

    cents = int(value * 100)

    if Decimal(cents) / 100 != value:
        raise ValueError

    return cents


def safe(text):
    return html.escape(str(text or ""))


def is_owner(message):
    return (
        message.from_user is not None
        and message.from_user.id == OWNER_ID
    )


# =========================
# МЕНЮ
# =========================

menu = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="➕ Доход"),
            KeyboardButton(text="➖ Расход")
        ],
        [
            KeyboardButton(text="💰 Баланс"),
            KeyboardButton(text="📜 История")
        ],
        [
            KeyboardButton(text="📊 Статистика")
        ],
        [
            KeyboardButton(text="✏️ Редактировать"),
            KeyboardButton(text="🗑 Удалить")
        ]
    ],
    resize_keyboard=True
)


# =========================
# СОСТОЯНИЯ
# =========================

class FinanceStates(StatesGroup):
    amount = State()
    category = State()
    comment = State()
    operation_date = State()

    delete_id = State()

    edit_id = State()
    edit_amount = State()
    edit_category = State()
    edit_comment = State()


dp = Dispatcher(storage=MemoryStorage())


# =========================
# СТАРТ И ОТМЕНА
# =========================

@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    await state.clear()

    await message.answer(
        "💰 <b>МОИ ФИНАНСЫ</b>\n\n"
        "Личный финансовый помощник запущен!\n\n"
        "Выбирай действие в меню.",
        reply_markup=menu
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    await state.clear()
    await message.answer(
        "Операция отменена.",
        reply_markup=menu
    )


# =========================
# НАЧАЛО ДОХОДА
# =========================

@dp.message(F.text == "➕ Доход")
async def income_start(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    await state.clear()
    await state.update_data(operation_type="income")
    await state.set_state(FinanceStates.amount)

    await message.answer("Введи сумму дохода в рублях:")


# =========================
# НАЧАЛО РАСХОДА
# =========================

@dp.message(F.text == "➖ Расход")
async def expense_start(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    await state.clear()
    await state.update_data(operation_type="expense")
    await state.set_state(FinanceStates.amount)

    await message.answer("Введи сумму расхода в рублях:")


# =========================
# ВВОД СУММЫ
# =========================

@dp.message(FinanceStates.amount)
async def process_amount(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    try:
        cents = parse_amount(message.text or "")
    except (InvalidOperation, ValueError, AttributeError):
        await message.answer(
            "Некорректная сумма.\n"
            "Пример: 1500 или 1500,50"
        )
        return

    await state.update_data(amount=cents)
    await state.set_state(FinanceStates.category)

    await message.answer(
        "Укажи категорию операции:\n"
        "Например: зарплата, продукты, автомобиль."
    )


# =========================
# ВВОД КАТЕГОРИИ
# =========================

@dp.message(FinanceStates.category)
async def process_category(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    category = (message.text or "").strip()

    if not category or len(category) > 40:
        await message.answer(
            "Категория должна содержать от 1 до 40 символов."
        )
        return

    await state.update_data(category=category)
    await state.set_state(FinanceStates.comment)

    await message.answer(
        "Добавь комментарий или отправь точку (.) без комментария."
    )


# =========================
# ВВОД КОММЕНТАРИЯ
# =========================

@dp.message(FinanceStates.comment)
async def process_comment(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    comment = (message.text or "").strip()

    if len(comment) > 200:
        await message.answer(
            "Комментарий слишком длинный. Максимум 200 символов."
        )
        return

    if comment == ".":
        comment = ""

    await state.update_data(comment=comment)
    await state.set_state(FinanceStates.operation_date)

    await message.answer(
        "📅 Введи дату операции в формате ДД.ММ.ГГГГ.\n"
        "Например: 17.11.2026\n\n"
        "Или отправь точку (.), чтобы поставить сегодняшнюю дату."
    )


# =========================
# СОХРАНЕНИЕ ОПЕРАЦИИ
# =========================

@dp.message(FinanceStates.operation_date)
async def process_operation_date(
    message: Message,
    state: FSMContext
):
    if not is_owner(message):
        return

    date_text = (message.text or "").strip()

    if date_text == ".":
        created_at = datetime.now().strftime("%d.%m.%Y %H:%M")
    else:
        try:
            parsed_date = datetime.strptime(date_text, "%d.%m.%Y")
            created_at = parsed_date.strftime("%d.%m.%Y") + " 12:00"
        except ValueError:
            await message.answer(
                "Неверная дата. Введи дату в формате ДД.ММ.ГГГГ."
            )
            return

    data = await state.get_data()

    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            INSERT INTO operations
            (owner_id, type, amount, category, comment, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            OWNER_ID,
            data["operation_type"],
            data["amount"],
            data["category"],
            data["comment"],
            created_at
        ))

    operation = (
        "Доход"
        if data["operation_type"] == "income"
        else "Расход"
    )

    await state.clear()

    await message.answer(
        f"✅ <b>{operation} сохранён!</b>\n\n"
        f"Сумма: {money(data['amount'])}\n"
        f"Категория: {safe(data['category'])}\n"
        f"Дата: {created_at}\n"
        f"Баланс: {money(get_balance())}",
        reply_markup=menu
    )


# =========================
# БАЛАНС
# =========================

@dp.message(F.text == "💰 Баланс")
async def balance(message: Message):
    if not is_owner(message):
        return

    await message.answer(
        f"💰 <b>ТЕКУЩИЙ БАЛАНС</b>\n\n"
        f"{money(get_balance())}"
    )


# =========================
# ИСТОРИЯ
# =========================

@dp.message(F.text == "📜 История")
async def history(message: Message):
    if not is_owner(message):
        return

    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("""
            SELECT id, type, amount, category, comment, created_at
            FROM operations
            WHERE owner_id = ?
            ORDER BY id DESC
            LIMIT 20
        """, (OWNER_ID,)).fetchall()

    if not rows:
        await message.answer("Операций пока нет.")
        return

    result = "📜 <b>ПОСЛЕДНИЕ 20 ОПЕРАЦИЙ</b>\n\n"

    for op_id, kind, amount, category, comment, date in rows:
        sign = "+" if kind == "income" else "−"

        result += (
            f"🆔 <b>#{op_id}</b>\n"
            f"{sign}{money(amount)} — {safe(category)}\n"
            f"<i>{safe(date)}</i>\n"
        )

        if comment:
            result += f"{safe(comment)}\n"

        result += "\n"

    await message.answer(result)


# =========================
# СТАТИСТИКА
# =========================

@dp.message(F.text == "📊 Статистика")
async def statistics(message: Message):
    if not is_owner(message):
        return

    month = datetime.now().strftime("%m.%Y")

    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("""
            SELECT type, COALESCE(SUM(amount), 0)
            FROM operations
            WHERE owner_id = ?
            AND substr(created_at, 4, 7) = ?
            GROUP BY type
        """, (OWNER_ID, month)).fetchall()

    totals = dict(rows)

    income = totals.get("income", 0)
    expense = totals.get("expense", 0)

    await message.answer(
        f"📊 <b>СТАТИСТИКА ЗА {month}</b>\n\n"
        f"🟢 Доходы: {money(income)}\n"
        f"🔴 Расходы: {money(expense)}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 Разница: {money(income - expense)}"
    )


# =========================
# УДАЛЕНИЕ ОПЕРАЦИЙ
# =========================

@dp.message(F.text == "🗑 Удалить")
async def delete_start(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    await state.clear()

    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("""
            SELECT id, type, amount, category, created_at
            FROM operations
            WHERE owner_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (OWNER_ID,)).fetchall()

    if not rows:
        await message.answer("Нет операций для удаления.")
        return

    result = "🗑 <b>ВЫБЕРИ ОПЕРАЦИЮ ДЛЯ УДАЛЕНИЯ</b>\n\n"

    for op_id, kind, amount, category, date in rows:
        sign = "+" if kind == "income" else "−"

        result += (
            f"#{op_id} | {sign}{money(amount)}\n"
            f"{safe(category)} | {safe(date)}\n\n"
        )

    result += "Отправь номер операции (ID)."

    await state.set_state(FinanceStates.delete_id)
    await message.answer(result)


@dp.message(FinanceStates.delete_id)
async def process_delete(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    try:
        op_id = int((message.text or "").strip())
        if op_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный ID операции.")
        return

    with sqlite3.connect(DB_NAME) as db:
        cursor = db.execute("""
            DELETE FROM operations
            WHERE id = ? AND owner_id = ?
        """, (op_id, OWNER_ID))

        deleted = cursor.rowcount

    if not deleted:
        await message.answer(
            "Операция не найдена. Проверь ID."
        )
        return

    await state.clear()

    await message.answer(
        f"🗑 Операция #{op_id} удалена.\n"
        f"Новый баланс: {money(get_balance())}",
        reply_markup=menu
    )


# =========================
# РЕДАКТИРОВАНИЕ ОПЕРАЦИЙ
# =========================

@dp.message(F.text == "✏️ Редактировать")
async def edit_start(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    await state.clear()

    with sqlite3.connect(DB_NAME) as db:
        rows = db.execute("""
            SELECT id, type, amount, category, created_at
            FROM operations
            WHERE owner_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (OWNER_ID,)).fetchall()

    if not rows:
        await message.answer("Нет операций для редактирования.")
        return

    result = "✏️ <b>ВЫБЕРИ ОПЕРАЦИЮ</b>\n\n"

    for op_id, kind, amount, category, date in rows:
        sign = "+" if kind == "income" else "−"

        result += (
            f"#{op_id} | {sign}{money(amount)}\n"
            f"{safe(category)} | {safe(date)}\n\n"
        )

    result += "Отправь ID операции."

    await state.set_state(FinanceStates.edit_id)
    await message.answer(result)


@dp.message(FinanceStates.edit_id)
async def process_edit_id(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    try:
        op_id = int((message.text or "").strip())
        if op_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введи корректный ID.")
        return

    with sqlite3.connect(DB_NAME) as db:
        row = db.execute("""
            SELECT amount, category, comment
            FROM operations
            WHERE id = ? AND owner_id = ?
        """, (op_id, OWNER_ID)).fetchone()

    if not row:
        await message.answer("Операция не найдена.")
        return

    await state.update_data(
        edit_id=op_id,
        old_amount=row[0],
        old_category=row[1],
        old_comment=row[2] or ""
    )

    await state.set_state(FinanceStates.edit_amount)

    await message.answer(
        f"Текущая сумма: {money(row[0])}\n\n"
        "Введи новую сумму или точку (.), чтобы оставить прежнюю."
    )


@dp.message(FinanceStates.edit_amount)
async def process_edit_amount(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    data = await state.get_data()
    text = (message.text or "").strip()

    if text == ".":
        amount = data["old_amount"]
    else:
        try:
            amount = parse_amount(text)
        except (InvalidOperation, ValueError, AttributeError):
            await message.answer("Введи корректную сумму или точку.")
            return

    await state.update_data(new_amount=amount)
    await state.set_state(FinanceStates.edit_category)

    await message.answer(
        f"Текущая категория: {safe(data['old_category'])}\n\n"
        "Введи новую категорию или точку, чтобы оставить прежнюю."
    )


@dp.message(FinanceStates.edit_category)
async def process_edit_category(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    data = await state.get_data()
    category = (message.text or "").strip()

    if category == ".":
        category = data["old_category"]
    elif not category or len(category) > 40:
        await message.answer(
            "Категория должна содержать от 1 до 40 символов."
        )
        return

    await state.update_data(new_category=category)
    await state.set_state(FinanceStates.edit_comment)

    await message.answer(
        "Введи новый комментарий.\n\n"
        "Отправь точку (.), чтобы оставить прежний.\n"
        "Отправь минус (-), чтобы очистить комментарий."
    )


@dp.message(FinanceStates.edit_comment)
async def process_edit_comment(message: Message, state: FSMContext):
    if not is_owner(message):
        return

    data = await state.get_data()
    comment = (message.text or "").strip()

    if comment == ".":
        comment = data["old_comment"]
    elif comment == "-":
        comment = ""

    if len(comment) > 200:
        await message.answer("Максимум 200 символов.")
        return

    with sqlite3.connect(DB_NAME) as db:
        db.execute("""
            UPDATE operations
            SET amount = ?, category = ?, comment = ?
            WHERE id = ? AND owner_id = ?
        """, (
            data["new_amount"],
            data["new_category"],
            comment,
            data["edit_id"],
            OWNER_ID
        ))

    await state.clear()

    await message.answer(
        f"✅ <b>Операция #{data['edit_id']} обновлена!</b>\n\n"
        f"Сумма: {money(data['new_amount'])}\n"
        f"Категория: {safe(data['new_category'])}\n"
        f"Баланс: {money(get_balance())}",
        reply_markup=menu
    )


# =========================
# ЗАПУСК
# =========================

async def main():
    init_db()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        )
    )

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
