# MBR Deliverability Dashboard

A web-based dashboard for viewing Monthly Business Review (MBR) deliverability metrics by ESP (SparkPost, SendGrid, Mailgun). Built with **FastAPI** (backend) and **Vue 3** (frontend), styled to match Blueshift's design system.

## Features

- 📊 **Live Data Fetching** from Druid (US & EU regions)
- 📅 **Date Range Selection** (from and to dates)
- 📈 **Executive Summary** - Overall metrics across all ESPs
- 🔍 **ESP-wise Breakdown** - Regional metrics (US, EU, Total) for each ESP
- 🏆 **Top 10 Domains** - By send volume per ESP and overall
- 📥 **Export Options**:
  - **Excel** (.xlsx) - All data in separate sheets
  - **PDF** - Formatted report with tables
- 🎨 **Blueshift Design** - Same colors, fonts, and styling as Blueshift

## Tech Stack

### Backend
- **FastAPI** (async Python framework)
- **Pandas** (data processing)
- **Requests** (Druid API calls)
- **ReportLab** (PDF generation)
- **OpenPyXL** (Excel generation)

### Frontend
- **Vue 3** (Options API)
- **Bootstrap 3.4.1** (CSS framework)
- **Blueshift Design System** (colors, typography, tables)

## Project Structure

```
mbr_dashboard/
├── backend/
│   ├── app.py              # FastAPI application
│   ├── config.py           # Configuration (Druid URLs, ESPs)
│   ├── druid_service.py    # Druid query and data processing
│   ├── export_service.py   # Excel and PDF export logic
│   └── requirements.txt    # Python dependencies
├── frontend/
│   ├── index.html          # Main HTML file
│   ├── css/
│   │   ├── blueshift-colors.css  # Blueshift color variables
│   │   └── main.css              # Main styles
│   └── js/
│       ├── api.js          # API service
│       └── app.js          # Vue application
└── README.md
```

## Setup Instructions

### Prerequisites

- **Python 3.8+**
- **pip** (Python package manager)

### 1. Install Backend Dependencies

```bash
cd backend
pip install -r requirements.txt
```

### 2. Start the Backend Server

```bash
# From the backend directory
python app.py
```

The API will be available at: `http://localhost:8000`

### 3. Start the Frontend

Open the frontend in a browser:

```bash
# From the frontend directory
open index.html
```

Or use a simple HTTP server:

```bash
cd frontend
python -m http.server 8080
```

Then visit: `http://localhost:8080`

## Usage

### 1. MBR Deliverability Page

1. **Select Date Range**:
   - Choose **From Date** and **To Date**
   - Default is current month (first day to today)

2. **Fetch Data**:
   - Click **"Fetch Data"** button
   - Data will be loaded from Druid (US & EU)

3. **View Results**:
   - **Executive Summary** - Overall metrics
   - **ESP Breakdowns** - US, EU, and Total rows for each ESP
   - **Top 10 Domains** - Per ESP and overall

### 2. Export Page

1. Navigate to **Export** in the sidebar
2. Choose export format:
   - **Export to Excel** - Downloads `.xlsx` file
   - **Export to PDF** - Downloads `.pdf` file

## API Endpoints

### `POST /api/fetch-data`
Fetch deliverability data from Druid

**Request Body:**
```json
{
  "from_date": "2025-01-01",
  "to_date": "2025-01-31"
}
```

**Response:**
```json
{
  "status": "success",
  "overall_summary": { ... },
  "esp_data": { ... },
  "top10_overall": [ ... ],
  "total_domains": 1234
}
```

### `POST /api/export/excel`
Export data to Excel format

### `POST /api/export/pdf`
Export data to PDF format

## Configuration

Edit `backend/config.py` to modify:

- **Druid Broker URLs** (US & EU)
- **ESP List** (default: SparkPost, SendGrid, Mailgun)
- **Query Template** (Druid SQL)

## Design System

The dashboard uses Blueshift's design system:

- **Colors**: Gray scale, blue, green, red, yellow palettes
- **Sidebar**: Blue gradient with rounded active states
- **Tables**: White background with hover states
- **Typography**: System fonts with consistent sizing
- **Buttons**: Primary (blue), Success (green), Secondary (gray)

## Development

### Running in Development Mode

**Backend:**
```bash
cd backend
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

**Frontend:**
Use any local dev server (Live Server, http-server, etc.)

### Adding New Features

1. **New API Endpoint**: Add to `backend/app.py`
2. **New Data Processing**: Modify `backend/druid_service.py`
3. **New UI Component**: Edit `frontend/index.html` and `frontend/js/app.js`
4. **New Styles**: Add to `frontend/css/main.css`

## Troubleshooting

### Backend Issues

**Port already in use:**
```bash
# Change port in app.py or use:
python app.py --port 8001
```

**Druid connection failed:**
- Check Druid broker URLs in `config.py`
- Verify network connectivity to Druid

### Frontend Issues

**CORS errors:**
- Make sure backend is running on `localhost:8000`
- Check CORS middleware in `backend/app.py`

**Data not loading:**
- Open browser DevTools (F12) → Console
- Check for API errors

## Future Enhancements

- [ ] Data caching (store historical reports)
- [ ] User authentication
- [ ] Scheduled reports (email delivery)
- [ ] More export formats (CSV, JSON)
- [ ] Custom date presets (Last 7 days, Last month, etc.)
- [ ] Chart visualizations
- [ ] Comparison view (compare two date ranges)

## License

Internal use only - Blueshift MBR Dashboard

---

**Need help?** Contact the data team or check the API docs at `http://localhost:8000/docs`
