#!/bin/bash
# Start script for MBR Dashboard Frontend

echo "🌐 Starting MBR Dashboard Frontend..."
echo ""

# Check if we're in the right directory
if [ ! -f "frontend/index.html" ]; then
    echo "❌ Error: Please run this script from the mbr_dashboard root directory"
    exit 1
fi

# Check if backend is running
if ! curl -s http://localhost:8000/health > /dev/null; then
    echo "⚠️  Warning: Backend doesn't seem to be running on port 8000"
    echo "   Please start the backend first with: ./start_backend.sh"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# Start simple HTTP server
echo "✅ Starting frontend server on http://localhost:8080"
echo "📱 Open http://localhost:8080 in your browser"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

cd frontend
python3 -m http.server 8080
