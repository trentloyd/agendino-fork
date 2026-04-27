#!/bin/bash
# Agendino Auto-Commit Service Status Script

echo "=== Agendino Auto-Commit Service Status ==="

if [ -f auto_commit.pid ]; then
    PID=$(cat auto_commit.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "✅ Service is running (PID: $PID)"
        echo "Started: $(ps -o lstart= -p "$PID" 2>/dev/null || echo 'Unknown')"
        echo ""
        echo "Recent activity from auto_commit.log:"
        if [ -f auto_commit.log ]; then
            tail -n 5 auto_commit.log
        else
            echo "No log file found"
        fi
    else
        echo "❌ Service is not running (stale PID file)"
        rm auto_commit.pid
    fi
else
    # Check if process is running without PID file
    RUNNING=$(pgrep -f "python.*auto_commit.py")
    if [ -n "$RUNNING" ]; then
        echo "⚠️ Service appears to be running but no PID file found"
        echo "PIDs: $RUNNING"
    else
        echo "❌ Service is not running"
    fi
fi

echo ""
echo "Log files:"
[ -f auto_commit.log ] && echo "  - auto_commit.log ($(wc -l < auto_commit.log) lines)"
[ -f auto_commit_service.log ] && echo "  - auto_commit_service.log ($(wc -l < auto_commit_service.log) lines)"