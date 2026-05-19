# Blueshift Observatory

Blueshift Observatory is an internal email deliverability observability and operations dashboard. It brings together domain, account, IP, DNS, bounce, reputation, and message-quality signals so the deliverability team can monitor health, investigate risk, export reports, and trigger follow-up actions from one place.

The application is built with a FastAPI backend and a Vue-powered static frontend. The backend collects and analyzes data from Druid, Microsoft SNDS, Google Postmaster Tools, ESP APIs, DNS lookups, bounce sources, local caches, and industry update feeds.

## What It Does

Blueshift Observatory supports the core deliverability workflow:

1. Collect metrics from internal event data and external reputation systems.
2. Classify domain, account, and IP health using delivery, bounce, spam, DNS, and reputation signals.
3. Drill into domains/accounts/IPs to identify causes and trends.
4. Save, export, and email Monthly Business Review reports.
5. Monitor DNS and reputation changes over time.
6. Analyze message quality and email content risk.
7. Surface industry updates relevant to deliverability operations.

## Feature Areas

### Pulsation Deliverability Monitor

- Domain and account health monitoring across configurable time windows.
- Delivery, bounce, spam, open, click, CTOR, and health-score views.
- Risk classifications for low delivery, spam complaints, bounces, and combined risk.
- Domain and account trend charts.
- Daily summary view.
- Spamhaus status and historical trends.
- Live domain bounce drilldowns with refresh jobs and CSV export.
- Jira ticket creation for remediation workflows.

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
- Read-only Rails-backed account mapping view.

### Account Info and ESP Integration

- ESP metadata collection for sending domains and accounts.
- Mailgun, SparkPost, and SendGrid integration points.
- Domain, account, IP address, subaccount, IP pool, region, status, verification, and created-at views.
- Cache clearing and forced refresh support.

### Microsoft SNDS Analytics

- Microsoft SNDS reputation and traffic analytics.
- Views by IP or account.
- Reputation trends, traffic trends, spam rates, trap hits, filter distribution, problem IPs, and top performers.
- Manual data collection trigger.
- CSV export from the frontend.

### Google Postmaster Tools

- OAuth-based Google Postmaster Tools connection.
- Domain list and authorization status.
- Domain reputation, IP reputation, spam, and authentication trends.
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

### Industry Updates

- Deliverability-related industry update feed.
- Source and severity filtering.
- Manual refresh and cleanup endpoints.
- Source metadata view.

### Scheduled Operations

The backend starts recurring jobs through APScheduler:

- Daily Spamhaus refresh.
- Daily bounce cleanup.
- Daily DNS check.
- Weekly ESP domain sync.

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
│   ├── gpt_service.py                 # Google Postmaster OAuth and collection
│   ├── gpt_analytics_service.py       # Google Postmaster analytics
│   ├── bounce_analytics_service.py    # ESP bounce collection and analysis
│   ├── eml_analysis_service.py        # EML/message analysis
│   ├── dns_*                          # DNS lookup, storage, change detection, alerts
│   ├── account_*                      # Account mapping and account aggregation services
│   ├── mbr_storage_service.py         # Saved MBR report storage
│   ├── email_service.py               # Report email recipients and SendGrid delivery
│   ├── industry_updates_service.py    # Industry update collection and storage
│   ├── jira_service.py                # Jira ticket creation
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

Important configuration locations:

- `backend/config.py` contains Druid broker URLs, ESP list, ESP API base URLs, and the Druid query template.
- `backend/data_paths.py` controls the local data directory, defaulting to `data/` unless `BS_DATA_DIR` is set.
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
- `/api/pulsation/*` - Pulsation collection, query, summary, trends, Spamhaus status, and live bounces.
- `/api/mbr/*` - saved report checks, saves, lists, detail, deletes, and trend data.
- `/api/email-recipients/*` and `/api/send-report-email` - report email recipient management and sending.
- `/api/account-mappings/*` - editable account mapping management.
- `/api/account-mappings-rails/*` - read-only Rails mapping access.
- `/api/account-info/*` - ESP integration account/domain metadata.
- `/api/snds/*` - Microsoft SNDS analytics and collection.
- `/api/gpt/*` - Google Postmaster Tools OAuth, collection, and analytics.
- `/api/bounces/*` - bounce collection, querying, domain lists, and CSV export.
- `/api/deliverability-analysis/*` - EML analysis and analysis report export.
- `/api/email-analysis/*` - test-address generation, inbound polling, HTML analysis, and URL analysis.
- `/api/dns/*` - DNS lookup, monitoring, ESP sync, selectors, alerts, and alert recipients.
- `/api/industry-updates/*` - industry update refresh, query, sources, and cleanup.
- `/api/jira/create-ticket` - Jira ticket creation from remediation workflows.

## Operational Notes

- Several modules use local SQLite databases and caches under the configured data directory.
- Druid-backed pages require internal network access to US and EU Druid brokers.
- SNDS, Google Postmaster Tools, SendGrid, Mailgun, and SparkPost features require valid external credentials.
- DNS monitoring and alerting rely on DNS storage, selectors, recipient configuration, and scheduled checks.
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
- Check backend logs for API status codes or pagination errors.

### DNS Monitoring Looks Incomplete

- Confirm the domain is present in the DNS monitor registry.
- Add or verify DKIM selectors where needed.
- Trigger a DNS check from the UI or API and inspect backend logs.

## Related Docs

- `QUICKSTART.md` - older quickstart notes.
- `PROJECT_SUMMARY.md` - original MBR dashboard project summary.
- `ACCOUNT_MAPPINGS_GUIDE.md` - account mapping workflow notes.
- `DAILY_SUMMARY_BRIEF.md` - daily summary context.
- `ROLLBACK_GUIDE.md` - rollback guidance.

## License

Internal use only.
