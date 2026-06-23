import feedparser
import re
import logging

from . import config
from .db import Database

logger = logging.getLogger(__name__)

SOURCES = [
    {"name": "Habr AI", "url": "https://habr.com/ru/rss/hub/ai/all/", "lang": "ru"},
    {"name": "Habr Programming", "url": "https://habr.com/ru/rss/hubs/programming/articles/", "lang": "ru"},
    {"name": "VC.ru", "url": "https://vc.ru/rss", "lang": "ru"},
    {"name": "Hacker News", "url": "https://hnrss.org/frontpage", "lang": "en"},
    {"name": "Reddit Artificial", "url": "https://www.reddit.com/r/artificial/.rss", "lang": "en"},
    {"name": "Reddit ML", "url": "https://www.reddit.com/r/MachineLearning/.rss", "lang": "en"},
]

KEYWORDS = [
    "искусственный интеллект", "нейросеть", "нейронн", "машинн обучен",
    "программирован", "разработк", "код", "алгоритм", "библиотек",
    "фреймворк", "язык программ", "бэкенд", "фронтенд",
    "база данных", "сервер", "devops", "docker", "git", "linux",
    "стартап", "технологи", "инноваци", "деплой",
    "python", "javascript", "typescript", "rust", "golang", "java",
    "react", "django", "fastapi", "node", "nextjs",
    "artificial intelligence", "neural network", "machine learning",
    "deep learning", "programming", "developer", "algorithm",
    "framework", "library", "backend", "frontend", "api",
    "database", "kubernetes", "startup", "ai", "llm", "gpt",
    "openai", "chatgpt", "claude", "gemini", "copilot",
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
            "Ты — автор IT-канала в Telegram. Напиши пост на русском языке по новости.\n\n"
            "Требования:\n"
            "- Живой, понятный стиль (как объяснение другу)\n"
            "- 300–500 символов\n"
            "- Заголовок жирным шрифтом (через *), потом суть\n"
            "- В конце: почему это важно (1 предложение)\n"
            "- Хештеги: #IT #AI или конкретнее\n"
            "- Без markdown (кроме * для жирного), только обычный текст\n\n"
            f"Новость:\nЗаголовок: {title}\nОписание: {summary}\n\nНапиши пост:"
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
                feed = feedparser.parse(source["url"])
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
