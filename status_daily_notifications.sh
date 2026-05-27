#!/bin/bash

# Check Status of Daily Notifications Service for Agendino

echo "=== Agendino Daily Notifications Status ==="

if [ ! -f "daily_notifications.pid" ]; then
    echo "❌ Service is not running (no PID file)"
else
    PID=$(cat daily_notifications.pid)
    if ps -p $PID > /dev/null 2>&1; then
        STARTED=$(ps -p $PID -o lstart= | tr -s ' ')
        echo "✅ Service is running (PID: $PID)"
        echo "Started: $STARTED"
        echo ""

        # Show configuration
        echo "Configuration:"
        echo "  Email: ${DAILY_NOTIFICATION_EMAIL:-Not set}"
        echo "  Time: ${DAILY_NOTIFICATION_TIME:-09:00}"
        echo "  SMTP Server: ${SMTP_SERVER:-Not set}"
        echo "  Email User: ${EMAIL_USER:-Not set}"
        echo ""

        # Show recent log entries
        if [ -f "daily_notifications.log" ]; then
            echo "Recent activity from daily_notifications.log:"
            tail -5 daily_notifications.log
        fi
    else
        echo "❌ Service is not running (stale PID file)"
        rm -f daily_notifications.pid
    fi
fi

echo ""
echo "Log files:"
if [ -f "daily_notifications.log" ]; then
    LINES=$(wc -l < daily_notifications.log)
    echo "  - daily_notifications.log ($LINES lines)"
else
    echo "  - daily_notifications.log (not found)"
fi