import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, time
from typing import List, Dict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.EmailService import EmailService
from repositories.SqliteDBRepository import SqliteDBRepository

logger = logging.getLogger(__name__)


class DailyNotificationService:
    def __init__(self, db_repo: SqliteDBRepository, email_service: EmailService):
        self.db_repo = db_repo
        self.email_service = email_service
        self.running = False
        self.task = None

    async def start_scheduler(self, notification_time: time = time(9, 0), to_email: str = None, weekdays_only: bool = True):
        """Start the daily notification scheduler.

        Args:
            notification_time: Time to send daily notifications (default 9:00 AM)
            to_email: Email address to send notifications to
            weekdays_only: If True, skip weekends (default True)
        """
        if not to_email:
            logger.error("No email address provided for daily notifications")
            return

        if not self.email_service.enabled:
            logger.error("Email service not configured - cannot start scheduler")
            return

        self.running = True
        logger.info(f"Starting daily notification scheduler for {to_email} at {notification_time.strftime('%H:%M')}")

        try:
            while self.running:
                now = datetime.now()
                target_time = datetime.combine(now.date(), notification_time)

                # If target time has passed today, schedule for tomorrow
                if target_time <= now:
                    target_time = target_time.replace(day=target_time.day + 1)

                # Skip weekends if weekdays_only is True
                if weekdays_only:
                    while target_time.weekday() >= 5:  # Saturday=5, Sunday=6
                        target_time = target_time.replace(day=target_time.day + 1)

                # Calculate seconds until next notification
                sleep_seconds = (target_time - now).total_seconds()
                weekday_name = target_time.strftime('%A')
                logger.info(f"Next notification scheduled for {weekday_name}, {target_time.strftime('%Y-%m-%d %H:%M:%S')} ({sleep_seconds:.0f} seconds)")

                # Sleep until notification time
                await asyncio.sleep(sleep_seconds)

                if self.running:  # Check if still running after sleep
                    await self._send_daily_notification(to_email)

        except asyncio.CancelledError:
            logger.info("Daily notification scheduler cancelled")
        except Exception as e:
            logger.error(f"Error in daily notification scheduler: {str(e)}")

    async def _send_daily_notification(self, to_email: str):
        """Send the daily action items notification."""
        try:
            # Get active (non-archived) action items
            active_items = self._get_active_action_items()

            if not active_items:
                logger.info("No active action items - skipping daily notification")
                return

            # Send email notification
            success = self.email_service.send_daily_action_items(to_email, active_items)

            if success:
                logger.info(f"Successfully sent daily notification with {len(active_items)} action items to {to_email}")
            else:
                logger.error(f"Failed to send daily notification to {to_email}")

        except Exception as e:
            logger.error(f"Error sending daily notification: {str(e)}")

    def _get_active_action_items(self) -> List[Dict]:
        """Get all active (non-archived, non-completed) action items."""
        try:
            # Get all action items that are not archived
            all_items = self.db_repo.get_all_action_items(include_archived=False)

            # Filter out completed items (keep pending and in_progress)
            active_items = [
                item.to_dict() for item in all_items
                if item.status in ["pending", "in_progress"]
            ]

            # Sort by priority (high -> medium -> low) and then by due date
            priority_order = {"high": 0, "medium": 1, "low": 2}

            active_items.sort(key=lambda x: (
                priority_order.get(x.get("priority", "medium"), 1),
                x.get("due_date") or "9999-12-31"  # Items without due date go to end
            ))

            return active_items

        except Exception as e:
            logger.error(f"Error retrieving active action items: {str(e)}")
            return []

    def stop_scheduler(self):
        """Stop the daily notification scheduler."""
        self.running = False
        if self.task:
            self.task.cancel()

    async def send_test_notification(self, to_email: str) -> bool:
        """Send a test notification immediately."""
        logger.info(f"Sending test notification to {to_email}")
        try:
            active_items = self._get_active_action_items()

            if not active_items:
                # Create a dummy action item for testing
                active_items = [{
                    "id": 0,
                    "title": "Test Action Item",
                    "description": "This is a test notification from Agendino",
                    "status": "pending",
                    "priority": "medium",
                    "due_date": None,
                    "assigned_to": "Test User",
                    "meeting_title": "Daily Notification Test",
                    "meeting_date": datetime.now().isoformat()
                }]

            return self.email_service.send_daily_action_items(to_email, active_items)
        except Exception as e:
            logger.error(f"Error sending test notification: {str(e)}")
            return False


def create_notification_daemon():
    """Create and run the daily notification service as a standalone daemon."""
    import sqlite3
    import os

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('daily_notifications.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Get configuration from environment
    db_name = os.environ.get("DATABASE_NAME", "agendino.db")
    notification_time_str = os.environ.get("DAILY_NOTIFICATION_TIME", "09:00")
    notification_email = os.environ.get("DAILY_NOTIFICATION_EMAIL")
    weekdays_only = os.environ.get("DAILY_NOTIFICATION_WEEKDAYS_ONLY", "true").lower() in ("true", "1", "yes")

    if not notification_email:
        logger.error("DAILY_NOTIFICATION_EMAIL environment variable not set")
        sys.exit(1)

    try:
        # Parse notification time
        hour, minute = map(int, notification_time_str.split(":"))
        notification_time = time(hour, minute)
    except (ValueError, IndexError):
        logger.error(f"Invalid notification time format: {notification_time_str}. Use HH:MM format.")
        sys.exit(1)

    # Initialize services
    try:
        # Get the root directory (go from src/services/ to project root)
        current_dir = os.path.dirname(os.path.abspath(__file__))  # src/services/
        src_dir = os.path.dirname(current_dir)  # src/
        root_path = os.path.dirname(src_dir)  # project root
        settings_path = os.path.join(root_path, "settings")
        init_sql_path = os.path.join(settings_path, "db_init.sql")

        db_repo = SqliteDBRepository(
            db_name=db_name,
            db_path=settings_path,
            init_sql_script=init_sql_path
        )
        email_service = EmailService()
        notification_service = DailyNotificationService(db_repo, email_service)
    except Exception as e:
        logger.error(f"Failed to initialize services: {str(e)}")
        sys.exit(1)

    # Setup signal handlers for graceful shutdown
    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        notification_service.stop_scheduler()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start the scheduler
    async def run():
        await notification_service.start_scheduler(notification_time, notification_email, weekdays_only)

    # Run the event loop
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Daily notification service stopped by user")
    except Exception as e:
        logger.error(f"Unexpected error in notification service: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    create_notification_daemon()