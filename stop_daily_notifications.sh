#!/bin/bash

# Stop Daily Notifications Service for Agendino

if [ ! -f "daily_notifications.pid" ]; then
    echo "❌ Daily notifications service is not running (no PID file found)"
    exit 1
fi

PID=$(cat daily_notifications.pid)

if ps -p $PID > /dev/null 2>&1; then
    echo "Stopping daily notifications service (PID: $PID)..."
    kill $PID

    # Wait for process to stop
    sleep 2

    if ps -p $PID > /dev/null 2>&1; then
        echo "Process still running, forcing kill..."
        kill -9 $PID
    fi

    rm -f daily_notifications.pid
    echo "✅ Daily notifications service stopped"
else
    echo "❌ Daily notifications service is not running (stale PID file)"
    rm -f daily_notifications.pid
fi