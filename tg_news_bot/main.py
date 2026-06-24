"""
Telegram-бот: автоматический сбор IT & AI новостей, отправка на утверждение, публикация.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

from . import config
from .db import Database
from .news import NewsManager
from . import handlers

logger = logging.getLogger(__name__)

db = Database(config.DATABASE_PATH)
news_mgr = NewsManager(db)


async def check_news(bot: Bot):
    logger.info("Checking news sources…")
    import asyncio
    try:
        articles = await asyncio.wait_for(
            asyncio.to_thread(news_mgr.process_new), timeout=120
        )
    except asyncio.TimeoutError:
        logger.warning("News check timed out")
        return
    if not articles:
        logger.info("No new articles found")
        return

    admin_id_str = db.get_config("admin_id", "")
    if not admin_id_str or int(admin_id_str) == 0:
        logger.warning("Admin not set — skipping approval")
        return

    admin_id = int(admin_id_str)
    for article in articles:
        preview = f"{article['content']}\n\n🔗 [Источник]({article['url']})"
        try:
            await bot.send_message(
                admin_id,
                preview,
                parse_mode="Markdown",
                reply_markup=handlers._approve_kb(article["pending_id"]),
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error("Failed to send for approval: %s", e)

    logger.info("Sent %d articles for approval", len(articles))


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if config.PROXY_URL:
        logger.info("Using proxy: %s", config.PROXY_URL)
        session = AiohttpSession(proxy=config.PROXY_URL)
        bot = Bot(
            token=config.BOT_TOKEN,
            session=session,
            default=DefaultBotProperties(parse_mode="Markdown"),
        )
    else:
        bot = Bot(
            token=config.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode="Markdown"),
        )
    dp = Dispatcher()

    handlers.setup(db, news_mgr)
    dp.include_router(handlers.router)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        check_news,
        trigger="interval",
        hours=config.CHECK_INTERVAL_HOURS,
        args=[bot],
        id="check_news",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (every %d h)", config.CHECK_INTERVAL_HOURS)

    # Healthcheck server (для Render/Railway — иначе они думают что сервис умер)
    port = int(os.getenv("PORT", "8080"))
    health_app = web.Application()
    health_app.router.add_get("/", lambda r: web.Response(text="ok"))
    runner = web.AppRunner(health_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Healthcheck server started on port %d", port)

    logger.info("Bot polling started")
    await dp.start_polling(bot)
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
