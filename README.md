# Daily Web Digest (AI + SWE)

This project scrapes general web feeds (RSS/Atom) every day, filters for AI and software engineering developments, and generates a practical markdown digest.

## What it does

- Polls configured sources in `config/sources.json` (Medium, Reddit, Hacker News, etc).
- Tracks seen article IDs in SQLite so each run focuses on newly discovered items.
- Scores entries for AI/SWE relevance.
- Summarizes key developments with OpenAI (or a rule-based fallback if no OpenAI key is set).
- Writes digest reports to `reports/YYYY-MM-DD.md`.

## 1) Setup

```bash
cd /Users/danchizik/Desktop/feed_summary
./scripts/setup.sh
```

Then open `.env` and fill:

- `OPENAI_API_KEY` (optional but recommended)
- `OPENAI_MODEL` (optional, default: `gpt-4o-mini`)

Optional:

- `WEB_FETCH_TIMEOUT_SECONDS`
- `WEB_FETCH_USER_AGENT`

## 2) Configure sources

Edit `config/sources.json`:

```json
[
  {
    "name": "Medium AI",
    "url": "https://medium.com/feed/tag/artificial-intelligence",
    "categories": ["AI"]
  }
]
```

Categories supported: `AI`, `SWE`, `GENERAL`.

## 3) Run once manually

```bash
source .venv/bin/activate
python daily_digest.py --max-items-per-source 40
```

Useful flags:

- `--force`: ignore seen-item state and summarize currently fetched items.
- `--dry-run`: generate report without updating state.
- `--max-relevant 25`: cap number of items sent to summarizer.
- `--sources-file path/to/sources.json`: alternate source file.

## 4) Install daily cron

Make scripts executable:

```bash
chmod +x scripts/setup.sh cron/run_daily.sh
```

Add cron entry:

```bash
crontab -e
```

Use:

```cron
0 7 * * * cd /Users/danchizik/Desktop/feed_summary && /Users/danchizik/Desktop/feed_summary/cron/run_daily.sh
```

This runs every day at 7:00 AM local machine time.

## Output

- Digest markdown reports in `reports/`
- Log file in `logs/daily_digest.log`
- Seen-item/state DB in `.data/state.db`

## Notes

- Some sources can temporarily fail due to rate limits or anti-bot rules; warnings are included in the report.
- The first run usually has many new items; later runs are incremental.
