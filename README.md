# Blueshift Observatory

Blueshift Observatory is an internal email deliverability observability and operations dashboard. It brings together domain, account, IP, DNS, bounce, reputation, and message-quality signals so the deliverability team can monitor health, investigate risk, export reports, plan IP/domain warm-ups, and trigger follow-up actions from one place — including through a built-in conversational AI assistant.

The application is built with a FastAPI backend and a Vue-powered static frontend. The backend collects and analyzes data from Druid, Microsoft SNDS, Google Postmaster Tools, ESP APIs (Mailgun, SparkPost, SendGrid), AWS Athena, DNS lookups, bounce sources, local caches, and industry update feeds.

## What It Does

Blueshift Observatory supports the core deliverability workflow:

1. Collect metrics from internal event data and external reputation systems.
2. Classify domain, account, and IP health using delivery, bounce, spam, open/click, DNS, and reputation signals.
3. Drill into domains/accounts/IPs to identify causes and trends.
4. Save, export, and email Monthly Business Review reports.
5. Monitor DNS and reputation changes over time.
6. Analyze message quality and email content risk.
7. Plan, track, and adjust IP/domain warm-up ramps against real send volume.
8. Ask a conversational AI assistant deliverability questions and get the same underlying data back inline.
9. Surface industry updates relevant to deliverability operations.

## Feature Areas

### Pulsation Deliverability Monitor

- Domain and account health monitoring across configurable time windows.
- Delivery, bounce, spam, unique user opens, unique user clicks, and health-score views.
- Risk classifications for low delivery, spam complaints, bounces, and combined risk.
- Domain and account trend charts.
- Daily summary view.
- Spamhaus status and historical trends.
- Live domain bounce drilldowns with refresh jobs and CSV export.
- Athena-backed bounce-reason breakdown per domain.
- Jira ticket creation for remediation workflows.

Unique user opens/clicks are collected going forward only (no backfill) — dates before this was added show as "Not tracked" rather than a misleading 0%, and the existing 365-day retention cleanup covers the new columns automatically.

### MBR Deliverability Reporting

- Date-range reporting from Druid across US and EU regions.
- Domain-level and account-level MBR views.
- ESP breakdowns for SparkPost, SendGrid, and Mailgun.
- Executive summaries, top domains, top accounts, affiliate account views, and month-over-month comparison data.
- Saved report snapshots with filtering, reload, and delete support.
- Excel and PDF export.
- Email delivery of generated reports to managed recipient lists.

### Account Mappings

- Domain-to-account mapping management.
- Search, create, edit, delete, and bulk delete workflows.
- CSV import and export.
- Account statistics.
- Affiliate-account tagging.
- Read-only Rails-backed account mapping view (direct MySQL read access).

### Account Info and ESP Integration

- ESP metadata collection for sending domains and accounts, paginated to the full account size (not just the first page) for Mailgun and SendGrid.
- Mailgun, SparkPost, and SendGrid integration points.
- Domain, account, IP address, subaccount, IP pool, region, status, verification, and created-at views.
- Shared vs. Dedicated IP classification for Mailgun domains — derived from IP reuse across the account's own domains (an IP used by exactly one domain is Dedicated; no IP assigned, or an IP shared with other domains, is Shared). Not applicable to SparkPost/SendGrid, shown as N/A for those rows.
- Cache clearing and forced refresh support.

### Microsoft SNDS Analytics

- Microsoft SNDS reputation and traffic analytics.
- Views by IP or account.
- Reputation trends, traffic trends, spam rates, trap hits, filter distribution, problem IPs, and top performers.
- Manual data collection trigger.
- CSV export from the frontend.

### Google Postmaster Tools

- Google Postmaster Tools API v2 integration with OAuth-based connection.
- Domain list and authorization status.
- Domain reputation, IP reputation, spam, and authentication trends.
- Domain and subdomain Compliance Status (SPF/DKIM/DMARC, encryption-in-transit, one-click unsubscribe) alongside the existing reputation/spam/authentication views.
- Overview tables and enhanced reputation-change views.
- Domain-level detail pages.
- Manual collection for historical data within the Google API window.

### Bounce Analytics

- Bounce collection and analysis across ESPs.
- Date, ESP, and domain filtering.
- Sending-domain discovery.
- Bounce reason summaries and detailed rows.
- CSV export.

### Deliverability and Message Analysis

- EML upload analysis.
- Test email address generation and polling for inbound analysis.
- Raw HTML and URL-based email analysis.
- Message-quality scoring and deliverability risk signals.
- Authentication, header, content, link, unsubscribe, blocklist, rendering, and spam-pattern checks.
- PDF export for analysis reports.

### DNS Looker and DNS Monitoring

- DNS lookup for SPF, DKIM, DMARC, MX, BIMI, MTA-STS, and TLS-RPT.
- Domain monitoring and historical DNS status.
- Custom DKIM selector management.
- ESP domain registry sync.
- DNS alert retrieval, unread counts, and read-state management.
- DNS alert recipient controls.

### Deliverability Warm-up Planner

Plan, track, and adjust IP/domain warm-up ramps for new domains, new dedicated IPs, combined new IP + domain launches, ESP/IP migrations, and infrastructure changes.

- ISP mix collected as absolute daily send volumes per ISP (Gmail, Microsoft, Yahoo, Apple, Comcast, Other) — the same numbers used when building a warm-up plan with a client — with the plan's overall target volume derived from these rather than entered separately.
- Each ISP ramps independently on its own curve at the chosen strategy's (Conservative/Balanced/Aggressive) maximum safe daily growth rate toward its own target, holding flat once it arrives rather than being stretched to match a slower-growing ISP.
- Daily planned volumes are rounded to realistic, easy-to-communicate numbers (e.g. 500 → 600, not 612).
- Weekly grid timeline view (ISP rows × day columns, daily totals, weekly totals) alongside a day-by-day detail table.
- A decision engine evaluates delivery rate, hard/soft bounce rate, spam complaints, unsubscribes, volume adherence, Spamhaus listing status, authentication status, and — where a real signal exists (Gmail Postmaster, Microsoft SNDS) — reputation, to recommend Increase / Hold / Reduce / Pause with an explainable 0–100 health score and reasons. A single bad day is treated as a transient Reduce, not an automatic Pause; sustained critical signals escalate to Pause.
- Actuals entry (manual form or CSV import) with automatic re-evaluation of the day's recommendation.
- Progress is measured from real Pulsation-collected sent volume for the plan's domain, not elapsed calendar time — shows "No sends yet" instead of a misleading percentage when nothing has been tracked for that domain yet.
- Plan list, detail view, status management (Active/Paused/Completed/Aborted), and a versioned, tunable threshold configuration.

### Observatory Agent (AI Assistant)

A conversational deliverability assistant, built on LangGraph, that answers questions using the same data sources as the rest of the dashboard.

- Pluggable LLM backend selected via `LLM_PROVIDER` — Claude/Anthropic, OpenAI, or a local Ollama model — with no other code changes required to switch.
- Tools for: SPF/DKIM/DMARC authentication checks, Gmail Postmaster reputation, email delivery/bounce/spam metrics and trend charts, ranking sending domains by a chosen metric, MBR deliverability lookups, account-info lookups, ISP-level breakdowns of a domain's sending, Spamhaus listing checks (single domain or account-wide, including historical trend), and Jira ticket filing.
- Structured responses: findings, stat cards, trend charts, and tables render inline in the chat alongside the natural-language answer (tool results carry a text summary for the model and a separate UI-only artifact for rendering).
- Status endpoint to confirm the agent and its configured provider are reachable.

### Industry Updates

- Deliverability-related industry update feed.
- Source and severity filtering.
- Manual refresh and cleanup endpoints.
- Source metadata view.

### Scheduled Operations

The backend starts recurring jobs through APScheduler as soon as it starts:

- Daily Spamhaus refresh.
- Daily bounce cleanup.
- Daily DNS check.
- Weekly ESP domain sync.

Separately, a few standalone scripts in `backend/` are designed to run from the system crontab rather than in-process (see each script for its own schedule comment): `collect_snds_daily.py`, `collect_gpt_daily.py` / `collect_gpt_daily_offset.py`, and `collect_mbr_account_monthly.py`. These exist because they're long-running or follow a cadence (daily offset windows, monthly) that doesn't fit the in-process scheduler cleanly.

## Tech Stack

### Backend

- FastAPI and Uvicorn
- Pydantic
- Pandas
- Requests and urllib3
- APScheduler
- ReportLab, OpenPyXL, and XlsxWriter
- SendGrid client
- dnspython
- feedparser
- Playwright
- LangChain and LangGraph (Observatory Agent orchestration and tools)
- Pluggable LLM clients — `langchain-anthropic` (Claude), `langchain-openai`, `langchain-ollama` (local)
- PyMySQL (read-only Rails-backed account mapping source)
- boto3 (AWS Athena bounce-reason queries)
- pytest (backend test suite — dev/CI only, not required at runtime)
- SQLite-backed local storage through service modules

### Frontend

- Vue 3
- Static HTML/CSS/JavaScript
- Bootstrap-compatible styling
- Chart.js-driven analytics views
- Blueshift-styled dashboard layout

## Project Structure

```text
blueshift_observatory/
├── backend/
│   ├── app.py                         # FastAPI app and API routes
│   ├── config.py                      # Druid, ESP, and environment-backed config
│   ├── data_paths.py                  # Local data directory helpers
│   ├── druid_service.py               # Druid queries and metric aggregation
│   ├── pulsation_service.py           # Pulsation collection, storage, and query logic
│   ├── spamhaus_service.py            # Spamhaus status and trend support
│   ├── snds_service.py                # Microsoft SNDS collection
│   ├── snds_analytics_service.py      # SNDS analytics views
│   ├── gpt_service.py                 # Google Postmaster OAuth, v2 collection, and compliance status
│   ├── gpt_analytics_service.py       # Google Postmaster analytics
│   ├── athena_bounce_service.py       # AWS Athena-backed bounce-reason drilldown
│   ├── bounce_analytics_service.py    # ESP bounce collection and analysis
│   ├── eml_analysis_service.py        # EML/message analysis
│   ├── email_domain_stats_service.py  # Per-domain recipient-ISP sending breakdown
│   ├── health_score_service.py        # Deliverability health score calculation
│   ├── mom_service.py                 # Month-over-month volume comparison
│   ├── pdf_html_service.py            # HTML-to-PDF rendering for exports
│   ├── esp_domain_sync_service.py     # Scheduled ESP domain registry sync
│   ├── inbound_service.py             # Test-address generation and inbound polling
│   ├── warmup_service.py              # Warm-up Planner plan CRUD and curve generation
│   ├── warmup_decision_engine.py      # Warm-up Planner signal evaluation and recommendations
│   ├── warmup_thresholds.py           # Versioned, tunable Warm-up Planner thresholds
│   ├── deliverability_agent/          # Observatory Agent: LangGraph agent, tools, LLM provider selection
│   ├── dns_*                          # DNS lookup, storage, change detection, alerts
│   ├── account_*                      # Account mapping and account aggregation services
│   ├── rails_mapping_service.py       # Read-only Rails (MySQL) account mapping source
│   ├── mbr_storage_service.py         # Saved MBR report storage
│   ├── email_service.py               # Report email recipients and SendGrid delivery
│   ├── industry_updates_service.py    # Industry update collection and storage
│   ├── jira_service.py                # Jira ticket creation
│   ├── collect_*.py                   # Standalone cron-driven collectors (SNDS, GPT, MBR monthly)
│   ├── tests/                         # Pytest suite (Warm-up Planner)
│   └── requirements.txt               # Python dependencies
├── frontend/
│   ├── index.html                     # Main dashboard UI
│   ├── js/
│   │   ├── api.js                     # Shared frontend API base and MBR calls
│   │   └── app.js                     # Vue app logic
│   ├── css/                           # Frontend styles
│   └── assets/                        # Static assets
├── start_backend.sh                   # Backend startup helper
├── start_frontend.sh                  # Static frontend server helper
├── sync_ec2_dbs.sh                    # Optional local DB sync helper
└── README.md
```

## Setup

### Prerequisites

- Python 3.8+
- pip
- Network access to the internal services used by the enabled modules
- Required API credentials in local environment files

### Install Dependencies

```bash
cd /Users/pankaj/pani/blueshift_observatory/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Start the Backend

```bash
cd /Users/pankaj/pani/blueshift_observatory/backend
source venv/bin/activate
python app.py
```

The current `backend/app.py` default is `http://localhost:8001`.

You can also use the helper script from the repository root:

```bash
cd /Users/pankaj/pani/blueshift_observatory
./start_backend.sh
```

Note: the helper script may print `8000`, but the current Python app starts on port `8001`.

### Start the Frontend

```bash
cd /Users/pankaj/pani/blueshift_observatory/frontend
python3 -m http.server 8080
```

Then open:

```text
http://localhost:8080
```

You can also use:

```bash
cd /Users/pankaj/pani/blueshift_observatory
./start_frontend.sh
```

### Health Check

```bash
curl http://localhost:8001/health
```

Expected response:

```json
{"status":"healthy"}
```

### Run the Backend Test Suite

```bash
cd /Users/pankaj/pani/blueshift_observatory/backend
./venv/bin/pytest tests/ -v
```

## Configuration

Configuration is loaded primarily from `.env` files and `backend/config.py`.

Common environment variables:

- `MAILGUN_API_KEY`
- `SPARKPOST_API_KEY`
- `SENDGRID_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `JIRA_BASE_URL`
- `JIRA_USER_EMAIL`
- `JIRA_API_TOKEN`
- `JIRA_PROJECT_KEY`
- `JIRA_PRIORITY`
- `BLUESHIFT_SYNC_SCRIPT`
- `BS_DATA_DIR`
- `BS_ENABLE_SPAMHAUS_IP`
- `USE_HTML_PDF`

Observatory Agent (Note: read from `backend/deliverability_agent/.env` specifically, not the project-root `.env`):

- `LLM_PROVIDER` — `ollama` (default, local/no API cost), `claude`/`anthropic`, or `openai`.
- `LLM_MODEL` — e.g. `claude-sonnet-5`, `gpt-4o`, `qwen2.5:7b`.
- `LLM_TEMPERATURE`, `LLM_MAX_TOKENS` (Claude only), `LLM_CLAUDE_TEMPERATURE` (opt-in; current Claude 5 models reject `temperature`).
- `ANTHROPIC_API_KEY` (Claude provider) or `OPENAI_API_KEY` (OpenAI provider).

Athena-backed bounce-reason drilldown:

- `ATHENA_ENABLED`, `ATHENA_REGION`, `ATHENA_WORKGROUP`, `ATHENA_DATABASE`, `ATHENA_TABLE`, `ATHENA_OUTPUT`, `AWS_PROFILE`.

Rails-backed account mapping view:

- `BSFT_DB_HOST`, `BSFT_DB_PORT`, `BSFT_DB_NAME`, `BSFT_DB_USER`, `BSFT_DB_PASSWORD`, `BSFT_ADAPTER_IDS`.

Important configuration locations:

- `backend/config.py` contains Druid broker URLs, ESP list, ESP API base URLs, and the Druid query template.
- `backend/data_paths.py` controls the local data directory, defaulting to `data/` unless `BS_DATA_DIR` is set.
- `backend/warmup_thresholds.py` contains every tunable Warm-up Planner threshold (volume variance, bounce/spam/unsubscribe bands, health-score weights, nice-rounding steps, strategy growth caps) — change behavior here, not inline in the decision engine.
- `frontend/js/api.js` defines the frontend API base URL.
- Some frontend code calls `http://localhost:8001` directly, so keep the backend port aligned unless those references are updated.

Do not commit production secrets. Keep credentials in local environment files or the deployment environment.

## API Overview

Interactive API docs are available while the backend is running:

```text
http://localhost:8001/docs
```

The API surface is organized by functional area:

- `GET /health` - backend health check.
- `POST /api/fetch-data` - domain-level MBR data from Druid.
- `POST /api/fetch-data-by-account` - account-level MBR data.
- `POST /api/export/excel` and `POST /api/export/pdf` - MBR exports.
- `/api/pulsation/*` - Pulsation collection, query, summary, trends, Spamhaus status, bounce reasons, and live bounces.
- `/api/mbr/*` - saved report checks, saves, lists, detail, deletes, and trend data.
- `/api/account-summary/{account_name}` - account-level summary.
- `/api/email-recipients/*` and `/api/send-report-email` - report email recipient management and sending.
- `/api/account-mappings/*` - editable account mapping management.
- `/api/account-mappings-rails/*` - read-only Rails mapping access.
- `/api/account-info/*` - ESP integration account/domain metadata, including IP type.
- `/api/snds/*` - Microsoft SNDS analytics and collection.
- `/api/gpt/*` - Google Postmaster Tools OAuth, v2 collection, compliance status, and analytics.
- `/api/bounces/*` - bounce collection, querying, domain lists, and CSV export.
- `/api/deliverability-analysis/*` - EML analysis and analysis report export.
- `/api/email-analysis/*` - test-address generation, inbound polling, HTML analysis, and URL analysis.
- `/api/dns/*` - DNS lookup, monitoring, ESP sync, selectors, alerts, and alert recipients.
- `/api/warmup/*` - Warm-up Planner plan CRUD, schedule, actuals (including CSV import), recommendations, events, and threshold config.
- `/api/deliverability/agent` and `/api/deliverability/agent/status` - Observatory Agent chat and reachability check.
- `/api/industry-updates/*` - industry update refresh, query, sources, and cleanup.
- `/api/jira/create-ticket` - Jira ticket creation from remediation workflows.

## Operational Notes

- Several modules use local SQLite databases and caches under the configured data directory.
- Druid-backed pages require internal network access to US and EU Druid brokers.
- SNDS, Google Postmaster Tools, SendGrid, Mailgun, and SparkPost features require valid external credentials.
- DNS monitoring and alerting rely on DNS storage, selectors, recipient configuration, and scheduled checks.
- The Observatory Agent requires its own provider credentials (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) unless running fully local against Ollama; it reads `backend/deliverability_agent/.env` separately from the rest of the app.
- The Athena-backed bounce-reason drilldown and the Rails-backed account mapping view each require their own external credentials (AWS and MySQL respectively) and are inert without them.
- Warm-up Planner progress depends on Pulsation having collected real send data for a plan's domain — a plan shows no progress until Pulsation has data for that domain within the plan's date range.
- `sync_ec2_dbs.sh` can be used by `BLUESHIFT_SYNC_SCRIPT` to sync local data from the configured environment when available.

## Troubleshooting

### Backend Does Not Start

- Confirm the virtual environment is active.
- Reinstall dependencies from `backend/requirements.txt`.
- Check whether another process is already using port `8001`.
- Confirm required environment variables are present for the feature being used.

### Frontend Cannot Load Data

- Confirm the backend is running at `http://localhost:8001`.
- Check browser DevTools for failed API calls.
- Confirm `frontend/js/api.js` and any direct frontend API references use the same backend port.
- CORS is enabled in the backend, so most local failures are usually port, network, or service errors.

### Druid Queries Fail

- Confirm internal network access to the configured Druid brokers.
- Check `backend/config.py` for broker URLs and query configuration.
- Try a smaller date range.

### Google Postmaster Tools Fails

- Confirm `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` are set.
- Complete the OAuth authorization flow from the GPT page.
- Confirm the authenticated Google account has access to the requested domains.

### ESP or Bounce Data Is Missing

- Confirm the relevant ESP API key is configured.
- Confirm domains/accounts exist in the connected ESP.
- Check backend logs for API status codes or pagination errors — Mailgun and SendGrid both paginate their list endpoints well below a typical account's real size, so an unpaginated call will silently look complete while missing most of the data.

### DNS Monitoring Looks Incomplete

- Confirm the domain is present in the DNS monitor registry.
- Add or verify DKIM selectors where needed.
- Trigger a DNS check from the UI or API and inspect backend logs.

### Observatory Agent Is Unreachable or Errors

- Check `GET /api/deliverability/agent/status`.
- Confirm `LLM_PROVIDER` and the matching API key (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`) are set in `backend/deliverability_agent/.env`, or that a local Ollama server is running if using `LLM_PROVIDER=ollama`.
- Check backend logs for the underlying provider SDK error.

### Warm-up Plan Shows No Progress

- Confirm Pulsation has collected data for the plan's domain within its date range (`/api/pulsation/available-dates`).
- Progress is computed from real Sent volume, not elapsed calendar time, so a plan with no Pulsation data yet will correctly show no progress rather than a misleading percentage.

## Related Docs

- `QUICKSTART.md` - older quickstart notes.
- `PROJECT_SUMMARY.md` - original MBR dashboard project summary.
- `ACCOUNT_MAPPINGS_GUIDE.md` - account mapping workflow notes.
- `DAILY_SUMMARY_BRIEF.md` - daily summary context.
- `ROLLBACK_GUIDE.md` - rollback guidance.

## License

Internal use only.
