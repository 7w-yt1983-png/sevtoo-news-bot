import feedparser
import re
import logging

from . import config
from .db import Database

logger = logging.getLogger(__name__)

SOURCES = [
    {"name": "Habr AI", "url": "https://habr.com/ru/rss/hub/ai/all/", "lang": "ru"},
    {"name": "VC.ru", "url": "https://vc.ru/rss", "lang": "ru"},
    {"name": "CNews", "url": "https://www.cnews.ru/inc/rss/all.xml", "lang": "ru"},
    {"name": "TJournal", "url": "https://tjournal.ru/rss", "lang": "ru"},
    {"name": "RBC Tech", "url": "https://www.rbc.ru/rss/technology.rss", "lang": "ru"},
    {"name": "Interfax Digital", "url": "https://www.interfax.ru/digital/rss.asp", "lang": "ru"},
    {"name": "The Verge AI", "url": "https://www.theverge.com/ai-artificial-intelligence/rss", "lang": "en"},
    {"name": "VentureBeat", "url": "https://venturebeat.com/feed/", "lang": "en"},
]

KEYWORDS = [
    "искусственный интеллект", "нейросеть", "нейронн", "машинн обучен",
    "технологи", "стартап", "инноваци", "цифровиз",
    "политик", "власт", "закон", "запрет", "регулирован",
    "путин", "мишустин", "чернышенко", "шадаев", "госдум",
    "минцфиры", "минэк", "правител", "рф", "россия", "российск",
    "иск", "суд", "расследован", "блокировк",
    "заявил", "объявил", "анонсировал",
    "увольнени", "скандал", "утечк", "уволен", "санкци",
    "миллиард", "миллион", "инвестици", "сделк", "миллиардер",
    "яндекс", "yandex", "yandexgpt", "алиса", "шедеврум", "kandinsky",
    "сбер", "gigachat", "сбербанк", "т-банк", "тинькофф",
    "vkontakte", "вконтакте", "vk", "касперский",
    "илон маск", "musk", "альтман", "altman", "sam altman",
    "сатья", "nadella", "pichai", "цукерберг", "zuckerberg",
    "openai", "microsoft", "google", "meta", "apple", "tesla",
    "китай", "china", "deepseek", "alibaba", "baidu", "тенсент",
    "импортозамещен", "суверенитет", "госуслуг", "гостех",
    "нацпроект", "национальн проект",
    "artificial intelligence", "machine learning", "deep learning",
    "ai", "llm", "gpt", "gpt-5", "gpt5",
    "openai", "chatgpt", "claude", "gemini", "copilot",
    "regulation", "ban", "lawsuit", "investigation", "sanction",
    "billion", "funding", "investment", "acqui",
]


def _matches_keywords(text: str) -> bool:
    text = text.lower()
    return any(kw in text for kw in KEYWORDS)


def _extract_image(entry) -> str | None:
    if hasattr(entry, "media_content") and entry.media_content:
        for media in entry.media_content:
            if "image" in media.get("type", ""):
                return media.get("url")
    if hasattr(entry, "links"):
        for link in entry.links:
            if link.get("type", "").startswith("image"):
                return link.get("href")
    if hasattr(entry, "enclosures"):
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image"):
                return enc.get("href")
    if hasattr(entry, "summary"):
        m = re.search(r'<img[^>]+src="([^"]+)"', entry.summary)
        if m:
            return m.group(1)
    return None


class AIWriter:
    def __init__(self):
        self.client = None
        self.available = False
        self._init()

    def _init(self):
        if not config.GEMINI_API_KEY:
            logger.info("No Gemini API key set — LLM disabled")
            return
        try:
            from google import genai
            self.client = genai.Client(api_key=config.GEMINI_API_KEY)
            self.available = True
            logger.info("Gemini client ready")
        except ImportError:
            logger.warning("google-genai not installed — LLM disabled")
        except Exception as e:
            logger.warning(f"Gemini init failed: {e} — LLM disabled")

    def rewrite(self, title: str, summary: str) -> str:
        if not self.available:
            return f"*{title}*\n\n{summary[:400]}"

        prompt = (
            "Ты — автор Telegram-канала об IT, AI и технологиях в России.\n"
            "Аудитория: разработчики и гики, которые хотят понимать, что происходит в мире "
            "технологий и как это связано с Россией.\n\n"
            "Формат — аналитический разбор (350–600 символов):\n\n"
            "*Заголовок* — цепляющий, без кликбейта\n\n"
            "1. Что произошло (суть в 1 предложении)\n"
            "2. Почему это важно (анализ, контекст, скрытые последствия)\n"
            "3. Связь с Россией — это ОБЯЗАТЕЛЬНАЯ часть: как новость касается РФ, "
            "есть ли аналоги в России, что говорят/делают власти, как это повлияет "
            "на российский IT-рынок\n"
            "4. Вывод или прогноз\n\n"
            "Стиль: уверенный, факты + твоя оценка. Как будто опытный разработчик "
            "объясняет коллеге. Без воды, без «читайте далее», без копирования источника.\n\n"
            "Хештеги: #IT #AI #Россия + по ситуации\n\n"
            f"Новость:\n{title}\n{summary}\n\nНапиши пост:"
        )
        try:
            response = self.client.models.generate_content(
                model="gemini-2.0-flash", contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"LLM rewriting failed: {e}")
            return f"*{title}*\n\n{summary[:400]}"


class NewsManager:
    def __init__(self, db: Database):
        self.db = db
        self.writer = AIWriter()

    def fetch_all(self) -> list[dict]:
        articles = []
        for source in SOURCES:
            try:
                import httpx as _httpx
                resp = _httpx.get(source["url"], timeout=12, follow_redirects=True)
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:15]:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = entry.get("summary", "") or entry.get("description", "") or ""
                    summary_clean = re.sub(r"<[^>]+>", "", summary).strip()[:500]

                    if not _matches_keywords(f"{title} {summary_clean}"):
                        continue

                    articles.append({
                        "title": title,
                        "url": link,
                        "summary": summary_clean,
                        "image_url": _extract_image(entry) or "",
                        "source": source["name"],
                        "lang": source["lang"],
                    })
            except Exception as e:
                logger.error(f"Failed to fetch {source['name']}: {e}")

        return articles

    def deduplicate(self, articles: list[dict]) -> list[dict]:
        seen = set()
        unique = []
        for a in articles:
            if a["url"] in seen or self.db.is_published(a["url"]):
                continue
            seen.add(a["url"])
            unique.append(a)
        return unique

    def process_new(self) -> list[dict]:
        all_articles = self.fetch_all()
        unique = self.deduplicate(all_articles)
        results = []
        for article in unique:
            content = self.writer.rewrite(article["title"], article["summary"])
            pending_id = self.db.add_pending(
                url=article["url"],
                title=article["title"],
                content=content,
                image_url=article.get("image_url", ""),
                source_url=article["url"],
            )
            if pending_id:
                results.append({**article, "content": content, "pending_id": pending_id})
        return results
