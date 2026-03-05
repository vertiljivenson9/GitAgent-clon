#!/usr/bin/env bash

echo "=========================================="
echo "  OpenAgent Runtime - Production Startup"
echo "=========================================="

echo ""
echo "Installing backend dependencies..."
pip install -r requirements.txt

echo ""
echo "Building frontend..."
cd webapp
npm install
npm run build
cd ..

echo ""
echo "Starting FastAPI server..."

exec uvicorn backend.main:app --host 0.0.0.0 --port $PORT