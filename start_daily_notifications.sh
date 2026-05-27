#!/bin/bash

# Start Daily Notifications Service for Agendino
# This script starts the daily notification daemon that sends action items via email

if [ -f "daily_notifications.pid" ]; then
    PID=$(cat daily_notifications.pid)
    if ps -p $PID > /dev/null 2>&1; then
        echo "Daily notifications service is already running with PID: $PID"
        exit 1
    else
        echo "Removing stale PID file..."
        rm -f daily_notifications.pid
    fi
fi

echo "Starting Agendino Daily Notifications Service..."

# Check if required environment variables are set
if [ -z "$DAILY_NOTIFICATION_EMAIL" ]; then
    echo "❌ Error: DAILY_NOTIFICATION_EMAIL environment variable not set"
    echo "Please set your work email address:"
    echo "export DAILY_NOTIFICATION_EMAIL=your-work-email@company.com"
    exit 1
fi

if [ -z "$EMAIL_USER" ] || [ -z "$EMAIL_PASSWORD" ] || [ -z "$SMTP_SERVER" ]; then
    echo "❌ Error: Email configuration missing"
    echo "Please set the following environment variables:"
    echo "export EMAIL_USER=your-email@gmail.com"
    echo "export EMAIL_PASSWORD=your-app-password"
    echo "export SMTP_SERVER=smtp.gmail.com"
    echo "export SMTP_PORT=587  # (optional, defaults to 587)"
    exit 1
fi

# Start the service in background
cd src
nohup python3 services/DailyNotificationService.py > ../daily_notifications.log 2>&1 &
PID=$!

# Save PID to file
echo $PID > ../daily_notifications.pid

echo "Daily notifications service started with PID: $PID"
echo "Logs: daily_notifications.log"
echo "Email: $DAILY_NOTIFICATION_EMAIL"
echo "Time: ${DAILY_NOTIFICATION_TIME:-09:00}"
echo ""
echo "To stop the service, run: ./stop_daily_notifications.sh"
echo "To check status, run: ./status_daily_notifications.sh"