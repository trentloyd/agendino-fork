#!/bin/bash
# Agendino Auto-Commit Service Stop Script

echo "Stopping Agendino Auto-Commit Service..."

if [ -f auto_commit.pid ]; then
    PID=$(cat auto_commit.pid)
    if kill -0 "$PID" 2>/dev/null; then
        kill "$PID"
        echo "Service stopped (PID: $PID)"
        rm auto_commit.pid
    else
        echo "Service was not running (PID: $PID not found)"
        rm auto_commit.pid
    fi
else
    echo "No PID file found. Attempting to find and stop auto_commit.py processes..."
    pkill -f "python.*auto_commit.py"
    echo "Any auto_commit.py processes have been terminated."
fi