#!/bin/bash
# Start script for MBR Dashboard Backend

echo "🚀 Starting MBR Dashboard Backend..."
echo ""

# Check if we're in the right directory
if [ ! -f "backend/app.py" ]; then
    echo "❌ Error: Please run this script from the mbr_dashboard root directory"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "backend/venv" ]; then
    echo "📦 Creating virtual environment..."
    cd backend
    python3 -m venv venv
    cd ..
fi

# Activate virtual environment
echo "🔧 Activating virtual environment..."
source backend/venv/bin/activate

# Install/update dependencies
echo "📥 Installing dependencies..."
pip install -q -r backend/requirements.txt

# Start the server
echo ""
echo "✅ Starting FastAPI server on http://localhost:8000"
echo "📚 API docs available at http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

cd backend
python app.py
