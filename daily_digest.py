#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - import-time guard
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False

try:
    import feedparser
except ImportError:  # pragma: no cover - import-time guard
    feedparser = None

try:
    import requests
except ImportError:  # pragma: no cover - import-time guard
    requests = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - import-time guard
    OpenAI = None


DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "name": "Medium AI",
        "url": "https://medium.com/feed/tag/artificial-intelligence",
        "categories": ["AI"],
    },
    {
        "name": "Medium Programming",
        "url": "https://medium.com/feed/tag/programming",
        "categories": ["SWE"],
    },
    {
        "name": "Reddit MachineLearning",
        "url": "https://www.reddit.com/r/MachineLearning/.rss",
        "categories": ["AI"],
    },
    {
        "name": "Reddit LocalLLaMA",
        "url": "https://www.reddit.com/r/LocalLLaMA/.rss",
        "categories": ["AI"],
    },
    {
        "name": "Reddit Programming",
        "url": "https://www.reddit.com/r/programming/.rss",
        "categories": ["SWE"],
    },
    {
        "name": "Hacker News Frontpage",
        "url": "https://hnrss.org/frontpage",
        "categories": ["AI", "SWE"],
    },
    {
        "name": "InfoQ",
        "url": "https://feed.infoq.com/",
        "categories": ["SWE"],
    },
    {
        "name": "MIT Technology Review AI",
        "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "categories": ["AI"],
    },
]

AI_KEYWORDS: dict[str, int] = {
    "ai": 1,
    "llm": 3,
    "gpt": 3,
    "openai": 3,
    "anthropic": 2,
    "claude": 2,
    "gemini": 2,
    "mistral": 2,
    "deepseek": 2,
    "agent": 2,
    "agents": 2,
    "inference": 2,
    "fine tuning": 2,
    "fine-tuning": 2,
    "rag": 2,
    "embedding": 1,
    "benchmark": 1,
    "multimodal": 2,
    "reasoning model": 2,
    "model release": 2,
}

SWE_KEYWORDS: dict[str, int] = {
    "software engineering": 2,
    "devtools": 2,
    "typescript": 2,
    "python": 2,
    "rust": 2,
    "go": 1,
    "golang": 2,
    "java": 1,
    "node": 1,
    "docker": 2,
    "kubernetes": 2,
    "ci/cd": 2,
    "github actions": 2,
    "testing": 2,
    "performance": 2,
    "latency": 2,
    "security": 2,
    "vulnerability": 2,
    "sdk": 2,
    "api": 1,
    "database": 2,
    "postgres": 2,
    "distributed systems": 2,
    "release notes": 2,
}

TAG_PATTERN = re.compile(r"<[^>]+>")
WS_PATTERN = re.compile(r"\s+")
FENCE_PATTERN = re.compile(r"^```(?:json)?\s*|\s*```$", flags=re.DOTALL)


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    user_agent: str
    timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            user_agent=os.getenv(
                "WEB_FETCH_USER_AGENT",
                "feed-summarizer/1.0 (+https://github.com/danchizik18/feed-summarizer)",
            ),
            timeout_seconds=max(5, int(os.getenv("WEB_FETCH_TIMEOUT_SECONDS", "20"))),
        )


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    url: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class NewsItem:
    id: str
    source: str
    source_url: str
    title: str
    summary: str
    link: str
    published_at: datetime | None
    source_categories: tuple[str, ...]


@dataclass(frozen=True)
class ScoredItem:
    item: NewsItem
    score: float
    ai_hits: tuple[str, ...]
    swe_hits: tuple[str, ...]

    @property
    def categories(self) -> tuple[str, ...]:
        categories: set[str] = set(self.item.source_categories)
        if self.ai_hits:
            categories.add("AI")
        if self.swe_hits:
            categories.add("SWE")
        return tuple(sorted(categories))


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_items (
                    item_id TEXT PRIMARY KEY,
                    first_seen_utc TEXT NOT NULL
                );
                """
            )

    def is_seen(self, item_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM seen_items WHERE item_id = ? LIMIT 1",
                (item_id,),
            ).fetchone()
        return row is not None

    def mark_seen(self, item_ids: list[str], seen_at: datetime) -> None:
        if not item_ids:
            return
        stamp = seen_at.isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO seen_items(item_id, first_seen_utc)
                VALUES (?, ?)
                """,
                [(item_id, stamp) for item_id in item_ids],
            )

    def prune_seen(self, keep_days: int, now: datetime) -> None:
        threshold = now - timedelta(days=max(1, keep_days))
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM seen_items WHERE first_seen_utc < ?",
                (threshold.isoformat(),),
            )

    def set_value(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kv(key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )


class RssClient:
    def __init__(self, settings: Settings) -> None:
        if requests is None or feedparser is None:
            raise RuntimeError(
                "Missing dependencies: requests/feedparser. Run `pip install -r requirements.txt`."
            )
        self.timeout = settings.timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            }
        )

    def fetch_all(
        self,
        sources: list[SourceDefinition],
        max_items_per_source: int,
        logger: Callable[[str], None] | None = None,
    ) -> tuple[list[NewsItem], list[str]]:
        results: list[NewsItem] = []
        errors: list[str] = []
        for source in sources:
            if logger is not None:
                logger(f"[info] Fetching source: {source.name}")
            try:
                source_items = self.fetch_source(source, max_items_per_source)
                if logger is not None:
                    logger(f"[info] {source.name}: fetched {len(source_items)} items")
                results.extend(source_items)
            except Exception as exc:
                if logger is not None:
                    logger(f"[warn] {source.name}: {exc}")
                errors.append(f"{source.name}: {exc}")
        deduped: dict[str, NewsItem] = {}
        for item in results:
            deduped[item.id] = item
        items = list(deduped.values())
        items.sort(
            key=lambda item: (item.published_at or datetime(1970, 1, 1, tzinfo=timezone.utc)),
            reverse=True,
        )
        return items, errors

    def fetch_source(self, source: SourceDefinition, max_items: int) -> list[NewsItem]:
        response = self.session.get(source.url, timeout=self.timeout)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", 0):
            bozo_error = getattr(parsed, "bozo_exception", None)
            if bozo_error:
                raise RuntimeError(f"Feed parsing error: {bozo_error}")
        entries = getattr(parsed, "entries", [])[: max(1, max_items)]
        items: list[NewsItem] = []
        for entry in entries:
            item = to_news_item(entry, source)
            if item is not None:
                items.append(item)
        return items


class DigestSummarizer:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.model = model
        self.client = OpenAI(api_key=api_key) if api_key and OpenAI is not None else None

    def summarize(self, items: list[ScoredItem]) -> dict[str, Any]:
        if not items:
            return {
                "theme": "No major AI/SWE developments were detected in new items today.",
                "developments": [],
                "quick_wins": [],
                "watchlist": [],
            }
        if self.client is None:
            fallback = self._fallback_digest(items)
            fallback["note"] = "OPENAI_API_KEY not set. Generated with rule-based fallback."
            return fallback
        try:
            return self._summarize_with_openai(items)
        except Exception as exc:
            fallback = self._fallback_digest(items)
            fallback["note"] = f"OpenAI summarization failed ({exc}). Generated with rule-based fallback."
            return fallback

    def _summarize_with_openai(self, items: list[ScoredItem]) -> dict[str, Any]:
        payload: list[dict[str, Any]] = []
        for scored in items[:30]:
            payload.append(
                {
                    "id": scored.item.id,
                    "source": scored.item.source,
                    "title": scored.item.title,
                    "summary": scored.item.summary,
                    "link": scored.item.link,
                    "published_utc": scored.item.published_at.isoformat() if scored.item.published_at else None,
                    "score": round(scored.score, 2),
                    "categories": list(scored.categories),
                }
            )

        system_prompt = (
            "You are a practical analyst. Summarize AI and software-engineering developments from web articles. "
            "Prioritize concrete launches, tools, incidents, benchmarks, and practical workflow impact."
        )
        user_prompt = (
            "Return strict JSON using this schema:\n"
            "{\n"
            '  "theme": "string",\n'
            '  "developments": [\n'
            "    {\n"
            '      "title": "string",\n'
            '      "summary": "string",\n'
            '      "practical_use": "string",\n'
            '      "source_ids": ["item-id"]\n'
            "    }\n"
            "  ],\n"
            '  "quick_wins": ["string"],\n'
            '  "watchlist": ["string"]\n'
            "}\n"
            "Rules:\n"
            "- Maximum 8 developments.\n"
            "- source_ids must match provided ids.\n"
            "- practical_use must be specific and immediately useful.\n"
            "- Keep each string concise.\n"
            f"Items JSON:\n{json.dumps(payload, ensure_ascii=True)}"
        )

        completion = self.client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        raw_content = completion.choices[0].message.content or "{}"
        parsed = parse_json_object(raw_content)
        return normalize_digest(parsed, items)

    def _fallback_digest(self, items: list[ScoredItem]) -> dict[str, Any]:
        developments: list[dict[str, Any]] = []
        for scored in items[:6]:
            title = truncate(scored.item.title, 95)
            summary = truncate(scored.item.summary or scored.item.title, 230)
            practical_use = practical_action(scored.item.title, scored.item.summary)
            developments.append(
                {
                    "title": title,
                    "summary": summary,
                    "practical_use": practical_use,
                    "source_ids": [scored.item.id],
                }
            )
        quick_wins = list({entry["practical_use"] for entry in developments})[:4]
        watchlist = [entry["title"] for entry in developments[3:6]]
        return {
            "theme": "AI and software engineering updates from your tracked web sources.",
            "developments": developments,
            "quick_wins": quick_wins,
            "watchlist": watchlist,
        }


def load_sources(path: Path) -> list[SourceDefinition]:
    raw_sources: list[dict[str, Any]]
    if path.exists():
        content = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(content, dict):
            candidate = content.get("sources", [])
        else:
            candidate = content
        if not isinstance(candidate, list):
            raise ValueError("Sources file must be a JSON array or object with `sources` array.")
        raw_sources = [entry for entry in candidate if isinstance(entry, dict)]
    else:
        raw_sources = DEFAULT_SOURCES

    sources: list[SourceDefinition] = []
    for entry in raw_sources:
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip()
        raw_categories = entry.get("categories", [])
        if not name or not url:
            continue
        categories = tuple(
            normalize_category(str(value))
            for value in raw_categories
            if normalize_category(str(value))
        )
        if not categories:
            categories = ("GENERAL",)
        sources.append(SourceDefinition(name=name, url=url, categories=categories))
    return sources


def normalize_category(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"AI", "SWE", "GENERAL"}:
        return normalized
    return ""


def to_news_item(entry: Any, source: SourceDefinition) -> NewsItem | None:
    title = clean_text(strip_html(str(entry.get("title", ""))))
    summary = clean_text(strip_html(str(entry.get("summary", "") or entry.get("description", ""))))
    link = normalize_link(str(entry.get("link", "")).strip())
    if not title or not link:
        return None

    published_at = parse_entry_datetime(entry)
    raw_id = str(entry.get("id", "")).strip() or link or f"{source.name}:{title}"
    item_id = make_item_id(raw_id, link, title)

    if len(summary) < 40:
        summary = truncate((summary + " " + title).strip(), 240)
    return NewsItem(
        id=item_id,
        source=source.name,
        source_url=source.url,
        title=title,
        summary=summary,
        link=link,
        published_at=published_at,
        source_categories=source.categories,
    )


def parse_entry_datetime(entry: Any) -> datetime | None:
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_time is None:
        return None
    try:
        return datetime(
            parsed_time.tm_year,
            parsed_time.tm_mon,
            parsed_time.tm_mday,
            parsed_time.tm_hour,
            parsed_time.tm_min,
            parsed_time.tm_sec,
            tzinfo=timezone.utc,
        )
    except Exception:
        return None


def make_item_id(raw_id: str, link: str, title: str) -> str:
    value = "|".join([raw_id.strip().lower(), link.strip().lower(), title.strip().lower()])
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def strip_html(value: str) -> str:
    without_tags = TAG_PATTERN.sub(" ", value)
    return without_tags.replace("&nbsp;", " ").replace("&amp;", "&")


def clean_text(value: str) -> str:
    return WS_PATTERN.sub(" ", value).strip()


def normalize_link(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    keep_pairs = []
    for key, val in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in {"ref", "source", "fbclid", "gclid"}:
            continue
        keep_pairs.append((key, val))
    clean_query = urlencode(keep_pairs, doseq=True)
    cleaned = parsed._replace(query=clean_query, fragment="")
    normalized = urlunparse(cleaned).rstrip("/")
    return normalized


def keyword_present(text: str, keyword: str) -> bool:
    if " " in keyword or "/" in keyword or "-" in keyword:
        return keyword in text
    pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
    return re.search(pattern, text) is not None


def score_item(item: NewsItem) -> ScoredItem:
    corpus = f"{item.title}. {item.summary}".lower()
    ai_hits = tuple(term for term in AI_KEYWORDS if keyword_present(corpus, term))
    swe_hits = tuple(term for term in SWE_KEYWORDS if keyword_present(corpus, term))

    keyword_score = sum(AI_KEYWORDS[hit] for hit in ai_hits) + sum(SWE_KEYWORDS[hit] for hit in swe_hits)
    category_hint = 0.8 if ("AI" in item.source_categories and ai_hits) else 0.0
    category_hint += 0.8 if ("SWE" in item.source_categories and swe_hits) else 0.0
    text_bonus = min(1.2, math.log1p(len(item.summary) / 80))
    recency_bonus = 0.0
    if item.published_at is not None:
        age_hours = max(0.0, (datetime.now(timezone.utc) - item.published_at).total_seconds() / 3600)
        recency_bonus = max(0.0, 1.2 - (age_hours / 72))
    score = keyword_score + category_hint + text_bonus + recency_bonus
    return ScoredItem(item=item, score=score, ai_hits=ai_hits, swe_hits=swe_hits)


def fingerprint_item(item: NewsItem) -> str:
    content = f"{item.title} {item.summary}".lower()
    stripped = re.sub(r"[^a-z0-9\s]", "", content)
    return " ".join(stripped.split()[:20])


def select_relevant(items: list[NewsItem], max_items: int, min_score: float = 2.4) -> list[ScoredItem]:
    scored = [score_item(item) for item in items]
    candidates = [
        entry
        for entry in scored
        if entry.score >= min_score
        and (
            entry.ai_hits
            or entry.swe_hits
            or "AI" in entry.item.source_categories
            or "SWE" in entry.item.source_categories
        )
    ]
    candidates.sort(
        key=lambda entry: (
            entry.score,
            entry.item.published_at or datetime(1970, 1, 1, tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    selected: list[ScoredItem] = []
    seen_fingerprints: set[str] = set()
    for entry in candidates:
        fp = fingerprint_item(entry.item)
        if fp in seen_fingerprints:
            continue
        seen_fingerprints.add(fp)
        selected.append(entry)
        if len(selected) >= max_items:
            break
    return selected


def parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = FENCE_PATTERN.sub("", text).strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Model output was not a JSON object.")
    return parsed


def normalize_digest(raw: dict[str, Any], items: list[ScoredItem]) -> dict[str, Any]:
    valid_ids = {entry.item.id for entry in items}
    developments: list[dict[str, Any]] = []
    for raw_dev in raw.get("developments", []):
        if not isinstance(raw_dev, dict):
            continue
        source_ids: list[str] = []
        for value in raw_dev.get("source_ids", []):
            entry_id = str(value).strip()
            if entry_id and entry_id in valid_ids:
                source_ids.append(entry_id)
        developments.append(
            {
                "title": str(raw_dev.get("title", "")).strip() or "Untitled development",
                "summary": str(raw_dev.get("summary", "")).strip() or "No summary provided.",
                "practical_use": str(raw_dev.get("practical_use", "")).strip()
                or "Review the source links and test one idea in your current workflow.",
                "source_ids": source_ids,
            }
        )
        if len(developments) >= 8:
            break

    quick_wins = [str(value).strip() for value in raw.get("quick_wins", []) if str(value).strip()][:5]
    watchlist = [str(value).strip() for value in raw.get("watchlist", []) if str(value).strip()][:5]
    theme = str(raw.get("theme", "")).strip() or "Daily AI/SWE web digest"
    return {
        "theme": theme,
        "developments": developments,
        "quick_wins": quick_wins,
        "watchlist": watchlist,
    }


def practical_action(title: str, summary: str) -> str:
    text = f"{title} {summary}".lower()
    if "release" in text or "launch" in text or "open source" in text:
        return "Test one new capability against your current workflow and capture the outcome."
    if "benchmark" in text or "performance" in text or "latency" in text:
        return "Validate claims with a quick local benchmark before adopting."
    if "security" in text or "vulnerability" in text:
        return "Check your dependency list and patch policy for related exposure."
    if "agent" in text or "copilot" in text:
        return "Prototype a narrow assistant for one repetitive engineering task."
    return "Add this to your backlog and run a focused 30-minute experiment this week."


def truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "..."


def render_report(
    run_at: datetime,
    total_items: int,
    new_items: int,
    selected: list[ScoredItem],
    digest: dict[str, Any],
    source_count: int,
    source_errors: list[str],
) -> str:
    item_lookup = {entry.item.id: entry.item for entry in selected}
    lines: list[str] = []
    lines.append(f"# Daily Web Digest ({run_at.strftime('%Y-%m-%d')})")
    lines.append("")
    lines.append(f"- Generated at (UTC): {run_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Sources polled: {source_count}")
    lines.append(f"- Articles fetched: {total_items}")
    lines.append(f"- New articles since last run: {new_items}")
    lines.append(f"- Relevant items selected: {len(selected)}")
    note = str(digest.get("note", "")).strip()
    if note:
        lines.append(f"- Note: {note}")
    if source_errors:
        lines.append(f"- Source fetch warnings: {len(source_errors)}")
    lines.append("")

    lines.append("## Theme")
    lines.append(str(digest.get("theme", "Daily AI/SWE web digest")))
    lines.append("")

    lines.append("## Key Developments")
    developments = digest.get("developments", [])
    if developments:
        for idx, dev in enumerate(developments, start=1):
            title = str(dev.get("title", "Untitled"))
            summary = str(dev.get("summary", ""))
            practical_use = str(dev.get("practical_use", ""))
            lines.append(f"### {idx}. {title}")
            if summary:
                lines.append(summary)
            if practical_use:
                lines.append(f"- Practical use: {practical_use}")
            source_links = []
            for entry_id in dev.get("source_ids", []):
                source_item = item_lookup.get(str(entry_id))
                if source_item is not None:
                    source_links.append(f"[{source_item.source}]({source_item.link})")
            if source_links:
                lines.append(f"- Sources: {', '.join(source_links)}")
            lines.append("")
    else:
        lines.append("No high-confidence AI/SWE developments were detected from new items today.")
        lines.append("")

    quick_wins = digest.get("quick_wins", [])
    if quick_wins:
        lines.append("## Quick Wins")
        for win in quick_wins:
            lines.append(f"- {win}")
        lines.append("")

    watchlist = digest.get("watchlist", [])
    if watchlist:
        lines.append("## Watchlist")
        for item in watchlist:
            lines.append(f"- {item}")
        lines.append("")

    if selected:
        lines.append("## Top Source Articles")
        for entry in selected[:15]:
            published = entry.item.published_at.strftime("%Y-%m-%d") if entry.item.published_at else "unknown date"
            categories = "/".join(entry.categories) if entry.categories else "GENERAL"
            excerpt = truncate(entry.item.summary or entry.item.title, 170)
            lines.append(
                f"- {entry.score:.1f} [{categories}] ({published}) "
                f"[{entry.item.source} - {entry.item.title}]({entry.item.link}) - {excerpt}"
            )
        lines.append("")

    if source_errors:
        lines.append("## Source Warnings")
        for error in source_errors:
            lines.append(f"- {error}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report(report_dir: Path, report_text: str, run_at: datetime) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    base_name = run_at.strftime("%Y-%m-%d")
    path = report_dir / f"{base_name}.md"
    if path.exists():
        path = report_dir / f"{base_name}_{run_at.strftime('%H%M%S')}.md"
    path.write_text(report_text, encoding="utf-8")
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch web feeds (Medium/Reddit/etc.) and generate a daily AI/SWE digest."
    )
    parser.add_argument(
        "--sources-file",
        type=Path,
        default=Path("config/sources.json"),
        help="JSON file with source definitions.",
    )
    parser.add_argument(
        "--max-items-per-source",
        type=int,
        default=40,
        help="Maximum entries fetched per source feed.",
    )
    parser.add_argument(
        "--max-relevant",
        type=int,
        default=25,
        help="Max relevant items sent to summarizer.",
    )
    parser.add_argument("--report-dir", type=Path, default=Path("reports"), help="Directory for markdown output.")
    parser.add_argument("--state-file", type=Path, default=Path(".data/state.db"), help="SQLite state file.")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Environment file path.")
    parser.add_argument("--prune-days", type=int, default=60, help="Retention window for seen items.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore seen-item state and summarize all currently fetched entries.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate report without updating seen-item state.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress logs (only final result/errors).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    def logger(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    if args.env_file.exists():
        load_dotenv(args.env_file)
    else:
        load_dotenv()

    settings = Settings.from_env()
    try:
        sources = load_sources(args.sources_file)
    except Exception as exc:
        print(f"[error] Failed to load sources file: {exc}")
        return 1
    if not sources:
        print("[error] No sources configured. Add entries to config/sources.json.")
        return 1
    logger(f"[info] Loaded {len(sources)} sources from {args.sources_file}")

    state = StateStore(args.state_file)
    try:
        client = RssClient(settings)
    except Exception as exc:
        print(f"[error] Failed to initialize RSS client: {exc}")
        return 1

    try:
        logger("[info] Starting feed fetch")
        fetched_items, source_errors = client.fetch_all(
            sources=sources,
            max_items_per_source=max(1, args.max_items_per_source),
            logger=logger if not args.quiet else None,
        )
        logger(f"[info] Fetch complete. Total fetched items: {len(fetched_items)}")
    except Exception as exc:
        print(f"[error] Failed fetching sources: {exc}")
        return 1

    if not fetched_items:
        print("[info] No items fetched from configured sources.")
        return 0

    if args.force:
        new_items = fetched_items
    else:
        new_items = [item for item in fetched_items if not state.is_seen(item.id)]
    logger(f"[info] New items for this run: {len(new_items)}")

    selected = select_relevant(new_items, max_items=max(1, args.max_relevant))
    logger(f"[info] Relevant items selected: {len(selected)}")
    summarizer = DigestSummarizer(api_key=settings.openai_api_key, model=settings.openai_model)
    digest = summarizer.summarize(selected)
    if not new_items:
        digest["note"] = "No new items since previous run."

    run_at = datetime.now(timezone.utc)
    report_text = render_report(
        run_at=run_at,
        total_items=len(fetched_items),
        new_items=len(new_items),
        selected=selected,
        digest=digest,
        source_count=len(sources),
        source_errors=source_errors,
    )
    report_path = write_report(args.report_dir, report_text, run_at)
    logger(f"[info] Report generated at {report_path}")

    if not args.dry_run:
        state.mark_seen([item.id for item in fetched_items], run_at)
        state.prune_seen(args.prune_days, run_at)
        state.set_value("last_run_utc", run_at.isoformat())

    print(f"[ok] Wrote digest to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
