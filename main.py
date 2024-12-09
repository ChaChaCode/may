from typing import Union
import logging
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.utils.exceptions import MessageNotModified
from aiogram.dispatcher.filters.state import State, StatesGroup
import os
from dotenv import load_dotenv
import re

# Загрузка переменных окружения
load_dotenv()

API_TOKEN = os.getenv('API_TOKEN')
CHANNEL_USERNAME = os.getenv('CHANNEL_USERNAME', '@My_ProReels')
ADMIN_ID = int(os.getenv('ADMIN_ID'))
DB_NAME = os.getenv('DB_NAME', 'phrases.db')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

user_requests = {}


class AdminStates(StatesGroup):
    waiting_for_phrase = State()
    confirm_delete = State()
    waiting_for_edit = State()
    waiting_for_file = State()


async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS phrases
            (id INTEGER PRIMARY KEY, text TEXT)
        ''')
        await db.commit()


async def add_phrase(phrase):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('INSERT INTO phrases (text) VALUES (?)', (phrase,))
        await db.commit()


async def delete_phrase(phrase_id):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM phrases WHERE id = ?', (phrase_id,))
        await db.commit()


async def get_all_phrases():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT id, text FROM phrases') as cursor:
            return await cursor.fetchall()


async def get_random_phrase():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute('SELECT text FROM phrases ORDER BY RANDOM() LIMIT 1') as cursor:
            result = await cursor.fetchone()
            return result[0] if result else "Нет доступных фраз"


async def check_subscription(user_id):
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ['creator', 'administrator', 'member']
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {e}")
        return False


@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Получить фразу", callback_data="get_phrase"))
    if message.from_user.id == ADMIN_ID:
        keyboard.add(InlineKeyboardButton("Админ панель", callback_data="admin_panel"))
    sent_message = await message.answer("Привет! Я бот, который поможет тебе начать день с вдохновляющей фразы.",
                                        reply_markup=keyboard)
    await state.update_data(last_message_id=sent_message.message_id)


async def send_phrase(chat_id: int, message_id: int, state: FSMContext):
    user_id = chat_id
    now = datetime.now()

    if user_id not in user_requests:
        user_requests[user_id] = {'count': 0, 'reset_time': now + timedelta(days=1)}

    if now >= user_requests[user_id]['reset_time']:
        user_requests[user_id] = {'count': 0, 'reset_time': now + timedelta(days=1)}

    is_subscribed = await check_subscription(user_id)

    if user_requests[user_id]['count'] >= 3 and not is_subscribed:
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("Я подписался", callback_data="check_subscription"))
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                        text=f"Вы достигли лимита запросов. Подпишитесь на наш канал {CHANNEL_USERNAME} для неограниченного доступа.",
                                        reply_markup=keyboard)
        except MessageNotModified:
            pass
        return

    phrase = await get_random_phrase()
    user_requests[user_id]['count'] += 1

    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("Получить фразу", callback_data="get_phrase"))
    if user_id == ADMIN_ID:  # Кнопка админ-панели для админа
        keyboard.add(InlineKeyboardButton("Админ панель", callback_data="admin_panel"))
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=phrase, reply_markup=keyboard)
    except MessageNotModified:
        pass




@dp.callback_query_handler(lambda c: c.data == 'get_phrase')
async def process_callback_get_phrase(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    data = await state.get_data()
    last_message_id = data.get('last_message_id')
    if last_message_id:
        await send_phrase(callback_query.message.chat.id, last_message_id, state)
    else:
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("Получить фразу", callback_data="get_phrase"))
        if callback_query.from_user.id == ADMIN_ID:
            keyboard.add(InlineKeyboardButton("Админ панель", callback_data="admin_panel"))
        sent_message = await bot.send_message(callback_query.message.chat.id, "Нажмите кнопку, чтобы получить фразу.",
                                              reply_markup=keyboard)
        await state.update_data(last_message_id=sent_message.message_id)


@dp.callback_query_handler(lambda c: c.data == 'check_subscription')
async def process_callback_check_subscription(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    user_id = callback_query.from_user.id
    is_subscribed = await check_subscription(user_id)

    if is_subscribed:
        await process_callback_get_phrase(callback_query, state)
    else:
        keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("Я подписался", callback_data="check_subscription"))
        try:
            await bot.edit_message_text(chat_id=callback_query.message.chat.id,
                                        message_id=callback_query.message.message_id,
                                        text=f"Вы не подписались. Подпишитесь на канал {CHANNEL_USERNAME} и попробуйте снова.",
                                        reply_markup=keyboard)
        except MessageNotModified:
            pass




async def admin_panel(update: Union[types.Message, types.CallbackQuery], state: FSMContext):
    if isinstance(update, types.CallbackQuery):
        chat_id = update.message.chat.id
        message_id = update.message.message_id
    else:  # isinstance(update, types.Message)
        chat_id = update.chat.id
        message_id = update.message_id  # Changed this line

    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Добавить фразы", callback_data="add_phrases"),
        InlineKeyboardButton("Удалить фразы", callback_data="delete_phrases"),
        InlineKeyboardButton("Список фраз", callback_data="list_phrases"),
        InlineKeyboardButton("Загрузить фразы из файла", callback_data="upload_phrases"),
        InlineKeyboardButton("Назад", callback_data="back_to_main")
    )

    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text="Админ панель\nЗдесь вы можете просматривать фразы, которые используются, а также добавлять фразы или удалять их.",
        reply_markup=keyboard
    )
    await state.update_data(last_admin_message_id=message_id)


@dp.callback_query_handler(lambda c: c.data == 'add_phrases' and c.from_user.id == ADMIN_ID)
async def add_phrases(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    if await state.get_state() is not None:
        await state.finish()
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Напишите вашу фразу для добавления",
        reply_markup=InlineKeyboardMarkup().add(
            InlineKeyboardButton("Назад", callback_data="admin_panel")
        )
    )
    await AdminStates.waiting_for_phrase.set()


@dp.message_handler(state=AdminStates.waiting_for_phrase)
async def process_new_phrase(message: types.Message, state: FSMContext):
    new_phrase = message.text
    await state.update_data(new_phrase=new_phrase)
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Добавить", callback_data="confirm_add"),
        InlineKeyboardButton("Назад", callback_data="admin_panel"),
    )
    await message.answer(
        text=f'Ваша новая фраза: "{new_phrase}"',
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == 'confirm_add', state=AdminStates.waiting_for_phrase)
async def confirm_add_phrase(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    new_phrase = data.get('new_phrase')
    if new_phrase:
        await add_phrase(new_phrase)
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("Добавить еще фразу", callback_data="add_more"),
            InlineKeyboardButton("Назад", callback_data="admin_panel")
        )
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f"Ваша новая добавленная фраза - {new_phrase}",
            reply_markup=keyboard
        )
    else:
        await bot.answer_callback_query(callback_query.id, text="Ошибка: фраза не найдена.")
    await state.finish()


@dp.callback_query_handler(lambda c: c.data == 'add_more' and c.from_user.id == ADMIN_ID)
async def add_more_phrases(callback_query: types.CallbackQuery):
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Напишите вашу новую фразу для добавления",
        reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="admin_panel"))
    )
    await AdminStates.waiting_for_phrase.set()


@dp.callback_query_handler(lambda c: c.data == 'delete_phrases' and c.from_user.id == ADMIN_ID)
async def delete_phrases(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(InlineKeyboardButton("Удалить все фразы", callback_data="delete_all_phrases"))
    keyboard.add(InlineKeyboardButton("Выбрать фразы для удаления", callback_data="select_delete_phrases"))
    keyboard.add(InlineKeyboardButton("Назад", callback_data="admin_panel"))

    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Удаление фраз\nВыберите опцию:",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == 'delete_all_phrases' and c.from_user.id == ADMIN_ID)
async def confirm_delete_all_phrases(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Да", callback_data="confirm_delete_all"),
        InlineKeyboardButton("Нет", callback_data="delete_phrases")
    )
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Вы уверены, что хотите удалить все фразы? Это действие нельзя отменить.",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data == 'confirm_delete_all' and c.from_user.id == ADMIN_ID)
async def delete_all_phrases_confirmed(callback_query: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('DELETE FROM phrases')
        await db.commit()
    await bot.answer_callback_query(callback_query.id, text='Все фразы были удалены!')
    await delete_phrases(callback_query)


@dp.callback_query_handler(lambda c: c.data == 'select_delete_phrases' and c.from_user.id == ADMIN_ID)
async def select_delete_phrases(callback_query: types.CallbackQuery):
    await show_phrases_for_deletion(callback_query.message, 0)


async def show_phrases_for_deletion(message, start_index: int):
    phrases = await get_all_phrases()
    keyboard = InlineKeyboardMarkup(row_width=3)
    for i in range(start_index, min(start_index + 3, len(phrases))):
        phrase_id, phrase_text = phrases[i]
        keyboard.add(InlineKeyboardButton(phrase_text[:30] + "...", callback_data=f"delete:{phrase_id}"))

    nav_buttons = []
    if start_index > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"nav_delete:{start_index - 3}"))
    if start_index + 3 < len(phrases):
        nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"nav_delete:{start_index + 3}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)

    keyboard.add(InlineKeyboardButton("Назад", callback_data="admin_panel"))

    await bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=message.message_id,
        text="Удаление фраз\nВыберите фразу из кнопок, которую вы хотите удалить:",
        reply_markup=keyboard
    )


@dp.callback_query_handler(lambda c: c.data.startswith('nav_delete:') and c.from_user.id == ADMIN_ID)
async def navigate_delete_phrases(callback_query: types.CallbackQuery):
    start_index = int(callback_query.data.split(':')[1])
    await show_phrases_for_deletion(callback_query.message, start_index)


@dp.callback_query_handler(lambda c: c.data.startswith('delete:') and c.from_user.id == ADMIN_ID)
async def confirm_delete_phrase(callback_query: types.CallbackQuery):
    phrase_id = int(callback_query.data.split(':')[1])
    phrases = await get_all_phrases()
    phrase_to_delete = next((phrase for phrase in phrases if phrase[0] == phrase_id), None)
    if phrase_to_delete:
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton("Да", callback_data=f"confirm_delete:{phrase_id}"),
            InlineKeyboardButton("Нет", callback_data="delete_phrases"),
            InlineKeyboardButton("Назад", callback_data="admin_panel")
        )
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=f'Вы уверены, что хотите удалить фразу "{phrase_to_delete[1]}"?',
            reply_markup=keyboard
        )
    else:
        await bot.answer_callback_query(callback_query.id, text="Ошибка: фраза не найдена.")


@dp.callback_query_handler(lambda c: c.data.startswith('confirm_delete:') and c.from_user.id == ADMIN_ID)
async def delete_phrase_confirmed(callback_query: types.CallbackQuery):
    phrase_id = int(callback_query.data.split(':')[1])
    await delete_phrase(phrase_id)
    await bot.answer_callback_query(callback_query.id, text=f'Фраза удалена!')
    await delete_phrases(callback_query)


@dp.callback_query_handler(lambda c: c.data == 'list_phrases' and c.from_user.id == ADMIN_ID)
async def list_phrases(callback_query: types.CallbackQuery):
    phrases = await get_all_phrases()
    phrases_list = "\n".join([f"{i + 1}. {phrase[1]}" for i, phrase in enumerate(phrases)])
    text = f"Список ваших фраз:\n\n{phrases_list}"

    if len(text) > 4096:
        for x in range(0, len(text), 4096):
            await bot.send_message(callback_query.message.chat.id, text[x:x + 4096])
    else:
        await bot.edit_message_text(
            chat_id=callback_query.message.chat.id,
            message_id=callback_query.message.message_id,
            text=text,
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="admin_panel"))
        )


@dp.callback_query_handler(lambda c: c.data == 'back_to_main')
async def back_to_main(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Получить фразу", callback_data="get_phrase"))
    if callback_query.from_user.id == ADMIN_ID:
        keyboard.add(InlineKeyboardButton("Админ панель", callback_data="admin_panel"))

    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text="Привет! Я бот, который поможет тебе начать день с вдохновляющей фразы.",
        reply_markup=keyboard
    )
    await state.update_data(last_message_id=callback_query.message.message_id)


@dp.callback_query_handler(lambda c: c.data == 'admin_panel', state='*')
async def admin_panel_handler(callback_query: CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    try:
        if await state.get_state() is not None:
            await state.finish()
    except KeyError:
        logger.warning(f"No state found for chat_id: {callback_query.from_user.id}")

    await admin_panel(callback_query, state)


@dp.callback_query_handler(lambda c: c.data == 'admin_panel', state=AdminStates.waiting_for_phrase)
async def back_to_admin_from_add_phrase(callback_query: types.CallbackQuery, state: FSMContext):
    await state.finish()
    await admin_panel(callback_query, state)


@dp.callback_query_handler(lambda c: c.data == 'upload_phrases' and c.from_user.id == ADMIN_ID)
async def upload_phrases(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    await AdminStates.waiting_for_file.set()
    example_text = (
        "Пожалуйста, отправьте текстовый файл (.txt) с фразами.\n\n"
        "Пример как должно быть оформлено в txt:\n"
        "    \"Люди, побывавшие в «...», что вы думаете\",\n"
        "    \"Один из самых курьёзных случаев в моей практике:\","
    )
    keyboard = InlineKeyboardMarkup().add(InlineKeyboardButton("Назад", callback_data="admin_panel"))
    await bot.edit_message_text(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        text=example_text,
        reply_markup=keyboard
    )
    await state.update_data(last_admin_message_id=callback_query.message.message_id)


@dp.message_handler(content_types=['document'], state=AdminStates.waiting_for_file)
async def process_file(message: types.Message, state: FSMContext):
    if not message.document.file_name.lower().endswith('.txt'):
        await message.reply("Пожалуйста, отправьте файл с расширением .txt")
        return

    file = await bot.get_file(message.document.file_id)
    file_path = file.file_path
    downloaded_file = await bot.download_file(file_path)
    content = downloaded_file.read().decode('utf-8')

    phrases = re.findall(r'"([^"]*)"', content)
    added_count = 0

    for phrase in phrases:
        await add_phrase(phrase)
        added_count += 1

    await message.reply(f"Добавлено {added_count} фраз из файла.")
    await state.finish()
    await admin_panel(message, state)


@dp.errors_handler()
async def errors_handler(update, exception):
    """
    Exceptions handler. Catches all exceptions within task factory tasks.
    :param update:
    :param exception:
    :return: stdout logging
    """
    from aiogram.utils.exceptions import (Unauthorized, InvalidQueryID, TelegramAPIError,
                                          CantDemoteChatCreator, MessageNotModified, MessageToDeleteNotFound,
                                          MessageTextIsEmpty, RetryAfter,
                                          CantParseEntities, MessageCantBeDeleted)

    if isinstance(exception, CantDemoteChatCreator):
        logger.debug("Can't demote chat creator")
        return True

    if isinstance(exception, MessageNotModified):
        logger.debug('Message is not modified')
        return True
    if isinstance(exception, MessageCantBeDeleted):
        logger.debug('Message cant be deleted')
        return True

    if isinstance(exception, MessageToDeleteNotFound):
        logger.debug('Message to delete not found')
        return True

    if isinstance(exception, MessageTextIsEmpty):
        logger.debug('MessageTextIsEmpty')
        return True

    if isinstance(exception, Unauthorized):
        logger.info(f'Unauthorized: {exception}')
        return True

    if isinstance(exception, InvalidQueryID):
        logger.exception(f'InvalidQueryID: {exception} \nUpdate: {update}')
        return True

    if isinstance(exception, TelegramAPIError):
        logger.exception(f'TelegramAPIError: {exception} \nUpdate: {update}')
        return True
    if isinstance(exception, RetryAfter):
        logger.exception(f'RetryAfter: {exception} \nUpdate: {update}')
        return True
    if isinstance(exception, CantParseEntities):
        logger.exception(f'CantParseEntities: {exception} \nUpdate: {update}')
        return True

    logger.exception(f'Update: {update} \n{exception}')


async def on_startup(dp):
    await init_db()


if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)

