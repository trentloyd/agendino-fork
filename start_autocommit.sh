#!/bin/bash
# Agendino Auto-Commit Service Startup Script

echo "Starting Agendino Auto-Commit Service..."

# Navigate to the project directory
cd /opt/agendino

# Activate virtual environment
source .venv/bin/activate

# Start the service in the background
nohup python auto_commit.py > auto_commit_service.log 2>&1 &

# Get the process ID
PID=$!
echo $PID > auto_commit.pid

echo "Auto-commit service started with PID: $PID"
echo "Logs: auto_commit.log and auto_commit_service.log"
echo ""
echo "To stop the service, run: ./stop_autocommit.sh"
echo "To check status, run: ./status_autocommit.sh"