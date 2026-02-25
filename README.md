# Daily Web Digest (AI + SWE)

This project scrapes general web feeds (RSS/Atom) every day, filters for AI and software engineering developments, and generates a practical markdown digest.

## What it does

- Polls configured sources in `config/sources.json` (Medium, Reddit, Hacker News, etc).
- Tracks seen article IDs and canonical links in SQLite so each run focuses on truly new items.
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
- `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` (for email sending)
- `EMAIL_FROM` / `EMAIL_TO` (sender + recipients)

Optional:

- `WEB_FETCH_TIMEOUT_SECONDS`
- `WEB_FETCH_USER_AGENT`
- `SMTP_USE_SSL` / `SMTP_USE_STARTTLS` / `SMTP_TIMEOUT_SECONDS`

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

To persist logs during manual runs:

```bash
python daily_digest.py --force 2>&1 | tee -a logs/daily_digest.log
```

Useful flags:

- `--force`: ignore seen-item state and summarize currently fetched items.
- `--dry-run`: generate report without updating state.
- `--max-relevant 25`: cap number of items sent to summarizer.
- `--sources-file path/to/sources.json`: alternate source file.
- `--no-email`: skip email for this run even if SMTP is configured.
- `--email-empty-digest`: send email even if there are no new items (default is skip).

## 4) Email delivery

When SMTP + email env vars are configured, the script sends each generated digest as:

- Plain-text email body containing the digest
- Markdown attachment (`YYYY-MM-DD.md`)

Multiple recipients are supported in `EMAIL_TO` using comma separation.

## 5) Install daily cron

Make scripts executable:

```bash
chmod +x scripts/setup.sh scripts/install_cron.sh cron/run_daily.sh
```

Install cron entry automatically:

```bash
./scripts/install_cron.sh
```

Default schedule is daily at 7:00 AM local time. Override schedule with:

```bash
CRON_SCHEDULE="30 8 * * *" ./scripts/install_cron.sh
```

## Output

- Digest markdown reports in `reports/`
- Log file in `logs/daily_digest.log`
- Seen-item/state DB in `.data/state.db`

## Notes

- Some sources can temporarily fail due to rate limits or anti-bot rules; warnings are included in the report.
- The first run usually has many new items; later runs are incremental.
