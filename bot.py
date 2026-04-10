import logging
import asyncio
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup

import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google.oauth2.service_account import Credentials

import os
import json
TOKEN = os.environ["MY_TOKEN"]

ADMIN_ID = 7014188456

logging.basicConfig(level=logging.INFO)

# scope для Google Sheets
scope = ["https://spreadsheets.google.com/feeds","https://www.googleapis.com/auth/drive"]

# читаем JSON из переменной окружения GOOGLE_CREDS
import base64

google_creds_b64 = os.getenv("GOOGLE_CREDS_B64")

if not google_creds_b64:
    raise RuntimeError("GOOGLE_CREDS_B64 не задана")

creds_json = base64.b64decode(google_creds_b64).decode("utf-8")
creds_dict = json.loads(creds_json)

creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)

# подключение к Google Sheets
client = gspread.authorize(creds)
sheet = client.open("rent_blacklist_db").worksheet("blacklist")

records_cache = []
def load_records():
    global records_cache
    records_cache = sheet.get_all_records()
    return records_cache

def find_row_by_id(rid):

    records = load_records()   # если используешь кэш

    for i, r in enumerate(records, start=2):
        if str(r["id"]) == str(rid):
            return i, r

    return None, None
# ===== Бот =====
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# ===== Клавиатуры =====
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Проверить человека")],
        [KeyboardButton(text="Добавить в ЧС")],
        [KeyboardButton(text="Мои заявки")]
    ],
    resize_keyboard=True
)

cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отмена")]],
    resize_keyboard=True
)

# ===== FSM =====
class AddBlackList(StatesGroup):
    fio = State()
    company = State()
    phone = State()
    telegram = State()
    reason = State()
    description = State()

class RejectComment(StatesGroup):
    text = State()

class SearchPerson(StatesGroup):
    fio = State()

# ===== Inline кнопки =====
def mod_keyboard(record_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"a_{record_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"r_{record_id}")
        ]]
    )


# ===== /start =====
@dp.message(Command("start"))
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Бот проверки арендаторов", reply_markup=main_keyboard)


# ===== Отмена =====
@dp.message(F.text == "Отмена")
async def cancel(message: Message, state: FSMContext):

    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нечего отменять", reply_markup=main_keyboard)
        return

    await state.clear()
    await message.answer("Действие отменено", reply_markup=main_keyboard)

# ===== Проверка человека =====
@dp.message(F.text == "Проверить человека")
async def check_menu(message: Message, state: FSMContext):
    await state.set_state(SearchPerson.fio)
    await message.answer("Введите ФИО:", reply_markup=cancel_keyboard)


# ===== Добавить в ЧС =====
@dp.message(F.text == "Добавить в ЧС")
async def add_menu(message: Message, state: FSMContext):
    await state.set_state(AddBlackList.fio)
    await message.answer("Введите ФИО:", reply_markup=cancel_keyboard)


# ===== Мои заявки =====
@dp.message(F.text == "Мои заявки")
async def my_requests(message: Message):

    records = load_records()
    user_id = str(message.from_user.id)

    data = [
        r for r in records
        if str(r.get("telegram_id", "")) == user_id
        and r.get("status") == "pending"
    ]

    if not data:
        await message.answer("У вас нет заявок в обработке")
        return

    for r in data:
        text = f"""
ID: {r['id']}
ФИО: {r['fio']}
Компания: {r['company']}
Телефон: {r['phone']}
Telegram: {r['telegram']}
Причина: {r['reason']}
Описание: {r['description']}
Статус: {r['status']}
Дата: {r['date']}
"""
        await message.answer(text)

# ===== FSM добавление =====
@dp.message(AddBlackList.fio)
async def add_fio(message: Message, state: FSMContext):
    await state.update_data(fio=message.text)
    await state.set_state(AddBlackList.company)
    await message.answer("Введите название компании:", reply_markup=cancel_keyboard)

@dp.message(AddBlackList.company)
async def add_company(message: Message, state: FSMContext):
    await state.update_data(company=message.text)
    await state.set_state(AddBlackList.phone)
    await message.answer("Телефон:", reply_markup=cancel_keyboard)

@dp.message(AddBlackList.phone)
async def add_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.text)
    await state.set_state(AddBlackList.telegram)
    await message.answer("Telegram:")

@dp.message(AddBlackList.telegram)
async def add_tg(message: Message, state: FSMContext):
    await state.update_data(telegram=message.text)
    await state.set_state(AddBlackList.reason)
    await message.answer("Причина:")

@dp.message(AddBlackList.reason)
async def add_reason(message: Message, state: FSMContext):
    await state.update_data(reason=message.text)
    await state.set_state(AddBlackList.description)
    await message.answer("Описание:")


@dp.message(AddBlackList.description)
async def add_finish(message: Message, state: FSMContext):

    # Проверяем, есть ли уже данные FSM
    data = await state.get_data()
    if not data:
        # Если данные потерялись — ничего не делаем
        return

    # Сразу ставим state в None, чтобы не попасть повторно
    await state.clear()  

    records = load_records()
    new_id = str(len(records) + 1)

    sheet.append_row([
        new_id,
        data["fio"],
        data["company"],       
        data["phone"],
        data["telegram"],
        data["reason"],
        message.text,          
        "pending",
        datetime.now().strftime("%d.%m.%Y"),
        "",                    
        message.from_user.id
    ])
    load_records()

    await message.answer("Заявка отправлена на модерацию ✅", reply_markup=main_keyboard)

# ===== Поиск =====
@dp.message(SearchPerson.fio)
async def search_person(message: Message, state: FSMContext):

    fio = message.text.lower()
    records = load_records()

    for r in records:

        if r["status"] == "approved" and r["fio"].lower() == fio:

            await message.answer(f"""
⚠️ Найден в ЧС

ФИО: {r['fio']}
Телефон: {r['phone']}
Telegram: {r['telegram']}
Причина: {r['reason']}
Описание: {r['description']}
""", reply_markup=main_keyboard)

            await state.clear()
            return

    await message.answer("В базе не найден", reply_markup=main_keyboard)
    await state.clear()


# ===== Модерация =====
@dp.message(Command("moderate"))
async def moderate(message: Message, state: FSMContext):

    await state.clear()

    if message.from_user.id != ADMIN_ID:
        await message.answer("Нет доступа")
        return

    data = [
        r for r in sheet.get_all_records()
        if str(r["status"]).strip().lower() == "pending"
    ]

    if not data:
        await message.answer("Нет заявок")
        return

    for r in data:

        text = f"""
ID: {r['id']}
ФИО: {r['fio']}
Компания: {r['company']}
Телефон: {r['phone']}
Причина: {r['reason']}
Описание: {r['description']}
"""

        await message.answer(text, reply_markup=mod_keyboard(r["id"]))


# ===== Одобрить =====
@dp.callback_query(F.data.startswith("a_"))
async def approve(call: CallbackQuery):

    if call.from_user.id != ADMIN_ID:
        return

    rid = call.data.split("_")[1]

    row, record = find_row_by_id(rid)

    if not row:
        await call.answer("Заявка не найдена")
        return

    sheet.update_cell(row, 8, "approved")

    await call.message.edit_text(call.message.text + "\n\n✅ ОДОБРЕНО")
    await call.answer()


# ===== Отклонить =====
@dp.callback_query(F.data.startswith("r_"))
async def reject(call: CallbackQuery, state: FSMContext):

    await state.set_state(RejectComment.text)
    await state.update_data(id=call.data.split("_")[1])

    await call.message.answer("Введите комментарий:")
    await call.answer()

@dp.message(RejectComment.text)
async def reject_comment(message: Message, state: FSMContext):

    data = await state.get_data()
    rid = data["id"]

    # Берём все записи
    records = load_records()
    row_index = None
    user_id = None
    record = None

    # Находим нужную строку по id
    for i, r in enumerate(records, start=2):  # start=2, т.к. row 1 - заголовки
        if str(r['id']) == rid:
            row_index = i
            record = r
            user_id = r['telegram_id']
            break

    if row_index is None:
        await message.answer("Ошибка: запись не найдена")
        await state.clear()
        return

    # Обновляем статус и комментарий
    sheet.update_cell(row_index, 8, "rejected")  # status
    sheet.update_cell(row_index, 10, message.text)  # moderator_comment

    # Формируем сообщение пользователю
    text = f"""❌ Ваша заявка отклонена

ID: {record['id']}
ФИО: {record['fio']}
Компания: {record['company']}
Телефон: {record['phone']}
Telegram: {record['telegram']}
Причина: {record['reason']}
Описание: {record['description']}
Дата подачи: {record['date']}

Комментарий модератора:
{message.text}
"""

    try:
        await bot.send_message(int(user_id), text)
    except Exception as e:
        await message.answer(f"Не удалось отправить уведомление пользователю: {e}")

    await message.answer("Заявка отклонена и пользователь уведомлён ✅", reply_markup=main_keyboard)
    await state.clear()

# ===== Запуск =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
