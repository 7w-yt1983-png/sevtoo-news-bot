import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Database
from .news import NewsManager

logger = logging.getLogger(__name__)
router = Router()

db: Database | None = None
news_manager: NewsManager | None = None


def setup(database: Database, news_mgr: NewsManager):
    global db, news_manager
    db = database
    news_manager = news_mgr


def _approve_kb(pending_id: int):
    b = InlineKeyboardBuilder()
    b.button(text="✅ Опубликовать", callback_data=f"pub_{pending_id}")
    b.button(text="❌ Пропустить", callback_data=f"skip_{pending_id}")
    return b.as_markup()


def _is_admin(user_id: int) -> bool:
    a = db.get_config("admin_id", "0")
    return a != "0" and int(a) == user_id


@router.message(Command("start"))
async def cmd_start(message: Message):
    if _is_admin(message.from_user.id):
        await message.answer("✅ Ты уже администратор. Используй /scan для поиска новостей.")
        return
    current = db.get_config("admin_id", "0")
    if current != "0":
        await message.answer("⛔ Бот доступен только администратору.")
        return
    db.set_config("admin_id", str(message.from_user.id))
    await message.answer(
        "👋 Привет! Я бот для IT & AI новостей.\n\n"
        "1️⃣ Добавь меня в канал как администратора\n"
        "2️⃣ Перешли сюда любое сообщение из канала\n"
        "3️⃣ Я буду присылать новости на утверждение\n\n"
        f"Твой ID: `{message.from_user.id}`",
        parse_mode="Markdown",
    )


@router.message(F.forward_from_chat)
async def handle_forward(message: Message):
    if not _is_admin(message.from_user.id):
        return
    chat = message.forward_from_chat
    if chat.type not in ("channel", "supergroup"):
        await message.answer("Это не канал. Перешли сообщение из канала.")
        return
    db.set_config("channel_id", str(chat.id))
    db.set_config("channel_title", chat.title or "")
    await message.answer(
        f"✅ Канал «{chat.title}» сохранён!\n\n"
        "Теперь я буду присылать тебе новости сюда.",
    )


@router.my_chat_member()
async def bot_added_to_channel(event: ChatMemberUpdated):
    if event.chat.type in ("channel", "supergroup"):
        new = event.new_chat_member.status
        if new in ("administrator", "member"):
            db.set_config("channel_id", str(event.chat.id))
            db.set_config("channel_title", event.chat.title or "")
            logger.info("Bot added to channel %s (%s)", event.chat.title, event.chat.id)


@router.callback_query(F.data.startswith("pub_"))
async def publish_post(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    pending_id = int(callback.data.split("_")[1])
    row = db.get_pending(pending_id)
    if not row:
        await callback.answer("Уже обработано", show_alert=True)
        await callback.message.delete()
        return

    _, url, title, content, image_url, source_url = row
    channel_id_str = db.get_config("channel_id", "")
    if not channel_id_str:
        await callback.answer("Канал не настроен! Перешли сообщение из канала.", show_alert=True)
        return

    channel_id = int(channel_id_str)
    post_text = f"{content}\n\n🔗 [Источник]({source_url})"

    try:
        if image_url:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=12) as client:
                    resp = await client.get(image_url)
                    if resp.status_code == 200:
                        from aiogram.types import BufferedInputFile
                        photo = BufferedInputFile(resp.content, filename="img.jpg")
                        await callback.bot.send_photo(
                            channel_id,
                            photo=photo,
                            caption=post_text,
                            parse_mode="Markdown",
                            disable_web_page_preview=True,
                        )
                    else:
                        raise ValueError
            except Exception:
                await callback.bot.send_message(
                    channel_id, post_text, parse_mode="Markdown", disable_web_page_preview=True,
                )
        else:
            await callback.bot.send_message(
                channel_id, post_text, parse_mode="Markdown", disable_web_page_preview=True,
            )

        db.mark_published(url, title)
        db.remove_pending(pending_id)
        await callback.answer("✅ Опубликовано!")
        await callback.message.edit_text(
            callback.message.text + "\n\n✅ Опубликовано", parse_mode="Markdown",
        )
    except Exception as e:
        logger.error("Publish error: %s", e)
        await callback.answer(f"Ошибка: {e}", show_alert=True)


@router.callback_query(F.data.startswith("skip_"))
async def skip_post(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    pending_id = int(callback.data.split("_")[1])
    row = db.get_pending(pending_id)
    if row:
        db.remove_pending(pending_id)
    await callback.answer("⏭ Пропущено")
    await callback.message.edit_text(
        callback.message.text + "\n\n❌ Пропущено", parse_mode="Markdown",
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not _is_admin(message.from_user.id):
        return
    channel_id = db.get_config("channel_id", "—")
    channel_title = db.get_config("channel_title", "—")
    admin_id = db.get_config("admin_id", "—")
    llm_ok = False
    try:
        import google.genai  # noqa: F401
        llm_ok = True
    except ImportError:
        pass
    await message.answer(
        f"📊 **Статус**\n\n"
        f"👤 Админ: `{admin_id}`\n"
        f"📢 Канал: {channel_title} (`{channel_id}`)\n"
        f"🤖 LLM: {'✅' if llm_ok else '❌ без LLM (работает)'}"
        f"\n🔄 Интервал: {__import__('tg_news_bot.config', fromlist=['']).CHECK_INTERVAL_HOURS} ч"
        f"\n/scan — поиск новостей сейчас",
        parse_mode="Markdown",
    )


@router.message(Command("scan"))
async def cmd_scan(message: Message):
    if not _is_admin(message.from_user.id):
        return
    if not news_manager:
        await message.answer("Ошибка: менеджер новостей не инициализирован.")
        return
    await message.answer("🔍 Ищу новые новости...")
    try:
        import asyncio
        articles = await asyncio.wait_for(
            asyncio.to_thread(news_manager.process_new), timeout=120
        )
    except asyncio.TimeoutError:
        await message.answer("⏱ Слишком долго. Попробуй ещё раз.")
        return
    except Exception as e:
        logger.error("Scan error: %s", e)
        await message.answer(f"❌ Ошибка: {e}")
        return
    if not articles:
        await message.answer("Новых новостей нет.")
        return
    for a in articles:
        preview = f"{a['content']}\n\n🔗 [Источник]({a['url']})"
        try:
            await message.answer(
                preview,
                parse_mode="Markdown",
                reply_markup=_approve_kb(a["pending_id"]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error("Scan send error: %s", e)
    await message.answer(f"✅ Нашёл {len(articles)} новостей.")
