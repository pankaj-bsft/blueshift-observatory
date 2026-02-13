# Quick Start Guide

Get the MBR Dashboard running in 3 minutes!

## Step 1: Start the Backend (Terminal 1)

```bash
cd /Users/pankaj/pani/mbr_dashboard
./start_backend.sh
```

Or manually:

```bash
cd /Users/pankaj/pani/mbr_dashboard/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

✅ Backend will start at: `http://localhost:8000`

## Step 2: Open the Frontend

**Option A: Direct File Open**
```bash
open /Users/pankaj/pani/mbr_dashboard/frontend/index.html
```

**Option B: Simple HTTP Server (Terminal 2)**
```bash
cd /Users/pankaj/pani/mbr_dashboard/frontend
python3 -m http.server 8080
```

Then visit: `http://localhost:8080`

## Step 3: Use the Dashboard

1. **Select Date Range**
   - From Date: `2025-01-01`
   - To Date: `2025-01-31`

2. **Click "Fetch Data"**
   - Wait for data to load from Druid

3. **View Results**
   - Executive Summary
   - ESP-wise metrics
   - Top 10 domains

4. **Export (Optional)**
   - Click "Export" in sidebar
   - Download Excel or PDF

## Troubleshooting

### Backend won't start
```bash
# Check Python version
python3 --version  # Should be 3.8+

# Try installing dependencies manually
cd backend
pip3 install fastapi uvicorn pandas requests reportlab openpyxl xlsxwriter pydantic python-multipart
```

### Frontend shows CORS error
- Make sure backend is running on `http://localhost:8000`
- Check browser console (F12) for errors

### No data returned
- Verify Druid broker URLs in `backend/config.py`
- Check network connectivity to Druid
- Try a smaller date range

## Default URLs

- **Backend API**: `http://localhost:8000`
- **API Docs**: `http://localhost:8000/docs`
- **Frontend**: `http://localhost:8080` (or open `index.html` directly)

## Testing

1. Visit `http://localhost:8000/health` - Should return `{"status": "healthy"}`
2. Visit `http://localhost:8000/docs` - Interactive API documentation
3. Open frontend and check browser console for errors

## Need Help?

Check the full README.md for detailed documentation.
