#!/bin/bash

# OpenAgent Runtime - Startup Script
# This script starts both the backend and frontend servers

echo "=========================================="
echo "  OpenAgent Runtime - Starting Servers"
echo "=========================================="
echo ""

# Check if Python and required packages are installed
echo "Checking Python dependencies..."
python3 -c "import fastapi, uvicorn, git" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "Installing Python dependencies..."
    pip3 install fastapi uvicorn gitpython pygithub python-multipart aiofiles --quiet
fi

# Start Backend
echo ""
echo "Starting Backend Server on http://localhost:8000"
echo "----------------------------------------------"
cd backend
python3 main.py &
BACKEND_PID=$!
cd ..

# Wait for backend to start
sleep 3

# Check if backend is running
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "ERROR: Backend failed to start"
    exit 1
fi

echo "✓ Backend is running (PID: $BACKEND_PID)"
echo ""

# Check if Node and npm are available
if command -v npm &> /dev/null; then
    echo "Starting Frontend Development Server..."
    echo "----------------------------------------------"
    cd webapp
    
    # Check if node_modules exists
    if [ ! -d "node_modules" ]; then
        echo "Installing frontend dependencies (this may take a while)..."
        npm install --no-bin-links 2>&1 | tail -5
    fi
    
    echo "✓ Frontend dependencies ready"
    echo "Starting Vite dev server on http://localhost:5173"
    echo ""
    
    npm run dev &
    FRONTEND_PID=$!
    cd ..
    
    echo "✓ Frontend is running (PID: $FRONTEND_PID)"
else
    echo "WARNING: npm not found. Frontend will not start."
    echo "To use the frontend, install Node.js and run:"
    echo "  cd webapp && npm install && npm run dev"
fi

echo ""
echo "=========================================="
echo "  All servers are running!"
echo "=========================================="
echo ""
echo "Backend API:  http://localhost:8000"
echo "Frontend:     http://localhost:5173"
echo "API Docs:     http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop all servers"
echo ""

# Wait for interrupt
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT
wait
