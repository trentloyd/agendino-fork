import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import List, Dict

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        """Initialize EmailService with SMTP configuration from environment variables."""
        self.smtp_server = os.environ.get("SMTP_SERVER")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.email_user = os.environ.get("EMAIL_USER")
        self.email_password = os.environ.get("EMAIL_PASSWORD")
        self.from_email = os.environ.get("FROM_EMAIL") or self.email_user
        self.enabled = all([self.smtp_server, self.email_user, self.email_password])

        if not self.enabled:
            logger.warning("Email service disabled - missing required environment variables")

    def send_daily_action_items(self, to_email: str, action_items: List[Dict]) -> bool:
        """Send a formatted email with daily action items summary."""
        if not self.enabled:
            logger.error("Cannot send email - service not properly configured")
            return False

        if not action_items:
            logger.info("No active action items to send")
            return True

        try:
            # Create email content
            subject = f"Daily Action Items Summary - {datetime.now().strftime('%B %d, %Y')}"
            html_body = self._create_action_items_html(action_items)
            text_body = self._create_action_items_text(action_items)

            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.from_email
            msg["To"] = to_email

            # Attach both plain text and HTML versions
            text_part = MIMEText(text_body, "plain")
            html_part = MIMEText(html_body, "html")
            msg.attach(text_part)
            msg.attach(html_part)

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_user, self.email_password)
                server.send_message(msg)

            logger.info(f"Successfully sent daily action items email to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    def _create_action_items_html(self, action_items: List[Dict]) -> str:
        """Create HTML email content for action items."""
        today = datetime.now().strftime('%B %d, %Y')

        # Group action items by status and priority
        pending_items = [item for item in action_items if item["status"] == "pending"]
        in_progress_items = [item for item in action_items if item["status"] == "in_progress"]

        # Group by priority
        high_priority = [item for item in action_items if item["priority"] == "high"]
        medium_priority = [item for item in action_items if item["priority"] == "medium"]
        low_priority = [item for item in action_items if item["priority"] == "low"]

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 800px; margin: 0 auto; background-color: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
        h2 {{ color: #34495e; margin-top: 25px; }}
        .summary {{ background-color: #ecf0f1; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .priority-high {{ border-left: 4px solid #e74c3c; }}
        .priority-medium {{ border-left: 4px solid #f39c12; }}
        .priority-low {{ border-left: 4px solid #27ae60; }}
        .action-item {{ margin: 10px 0; padding: 15px; background-color: #f8f9fa; border-radius: 5px; }}
        .item-title {{ font-weight: bold; color: #2c3e50; }}
        .item-meta {{ font-size: 0.9em; color: #7f8c8d; margin-top: 5px; }}
        .item-description {{ margin-top: 8px; color: #34495e; }}
        .status-badge {{ padding: 3px 8px; border-radius: 12px; font-size: 0.8em; font-weight: bold; }}
        .status-pending {{ background-color: #f39c12; color: white; }}
        .status-in-progress {{ background-color: #3498db; color: white; }}
        .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #bdc3c7; text-align: center; font-size: 0.9em; color: #7f8c8d; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📋 Daily Action Items Summary</h1>
        <p><strong>Date:</strong> {today}</p>

        <div class="summary">
            <h3>📊 Summary</h3>
            <ul>
                <li><strong>Total Active Items:</strong> {len(action_items)}</li>
                <li><strong>Pending:</strong> {len(pending_items)}</li>
                <li><strong>In Progress:</strong> {len(in_progress_items)}</li>
                <li><strong>High Priority:</strong> {len(high_priority)}</li>
                <li><strong>Medium Priority:</strong> {len(medium_priority)}</li>
                <li><strong>Low Priority:</strong> {len(low_priority)}</li>
            </ul>
        </div>
"""

        # High Priority Items
        if high_priority:
            html += """
        <h2>🚨 High Priority Items</h2>
"""
            for item in high_priority:
                html += self._format_action_item_html(item, "priority-high")

        # In Progress Items
        if in_progress_items:
            html += """
        <h2>🔄 In Progress</h2>
"""
            for item in in_progress_items:
                priority_class = f"priority-{item['priority']}"
                html += self._format_action_item_html(item, priority_class)

        # Pending Items
        if pending_items:
            html += """
        <h2>⏳ Pending Items</h2>
"""
            for item in pending_items:
                priority_class = f"priority-{item['priority']}"
                html += self._format_action_item_html(item, priority_class)

        html += """
        <div class="footer">
            <p>Generated by <strong>Agendino</strong> • <a href="http://127.0.0.1:8000/action-items">View in Dashboard</a></p>
        </div>
    </div>
</body>
</html>
"""
        return html

    def _format_action_item_html(self, item: Dict, priority_class: str) -> str:
        """Format a single action item for HTML email."""
        due_date = ""
        if item.get("due_date"):
            try:
                due_dt = datetime.fromisoformat(item["due_date"])
                due_date = f" • Due: {due_dt.strftime('%m/%d/%Y')}"
            except ValueError:
                pass

        assigned_to = f" • Assigned to: {item['assigned_to']}" if item.get("assigned_to") else ""
        meeting_title = f" • From: {item['meeting_title']}" if item.get("meeting_title") else ""
        description = f'<div class="item-description">{item["description"]}</div>' if item.get("description") else ""

        return f"""
        <div class="action-item {priority_class}">
            <div class="item-title">{item['title']}</div>
            <div class="item-meta">
                <span class="status-badge status-{item['status'].replace('_', '-')}">{item['status'].replace('_', ' ').title()}</span>
                <span>Priority: {item['priority'].title()}</span>{due_date}{assigned_to}{meeting_title}
            </div>
            {description}
        </div>
"""

    def _create_action_items_text(self, action_items: List[Dict]) -> str:
        """Create plain text email content for action items."""
        today = datetime.now().strftime('%B %d, %Y')

        text = f"DAILY ACTION ITEMS SUMMARY - {today}\n"
        text += "=" * 50 + "\n\n"

        # Summary
        pending_items = [item for item in action_items if item["status"] == "pending"]
        in_progress_items = [item for item in action_items if item["status"] == "in_progress"]
        high_priority = [item for item in action_items if item["priority"] == "high"]

        text += "SUMMARY:\n"
        text += f"• Total Active Items: {len(action_items)}\n"
        text += f"• Pending: {len(pending_items)}\n"
        text += f"• In Progress: {len(in_progress_items)}\n"
        text += f"• High Priority: {len(high_priority)}\n\n"

        # High Priority Items
        if high_priority:
            text += "🚨 HIGH PRIORITY ITEMS:\n"
            text += "-" * 25 + "\n"
            for item in high_priority:
                text += self._format_action_item_text(item)
            text += "\n"

        # In Progress Items
        if in_progress_items:
            text += "🔄 IN PROGRESS:\n"
            text += "-" * 15 + "\n"
            for item in in_progress_items:
                text += self._format_action_item_text(item)
            text += "\n"

        # Pending Items
        if pending_items:
            text += "⏳ PENDING ITEMS:\n"
            text += "-" * 17 + "\n"
            for item in pending_items:
                text += self._format_action_item_text(item)

        text += "\nGenerated by Agendino\nView in Dashboard: http://127.0.0.1:8000/action-items\n"
        return text

    def _format_action_item_text(self, item: Dict) -> str:
        """Format a single action item for plain text email."""
        due_date = ""
        if item.get("due_date"):
            try:
                due_dt = datetime.fromisoformat(item["due_date"])
                due_date = f" | Due: {due_dt.strftime('%m/%d/%Y')}"
            except ValueError:
                pass

        assigned_to = f" | Assigned: {item['assigned_to']}" if item.get("assigned_to") else ""
        meeting_title = f" | From: {item['meeting_title']}" if item.get("meeting_title") else ""

        result = f"• {item['title']}\n"
        result += f"  Status: {item['status'].title()} | Priority: {item['priority'].title()}{due_date}{assigned_to}{meeting_title}\n"

        if item.get("description"):
            result += f"  Description: {item['description']}\n"

        result += "\n"
        return result

    def test_connection(self) -> bool:
        """Test email configuration and connection."""
        if not self.enabled:
            return False

        try:
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.email_user, self.email_password)
            return True
        except Exception as e:
            logger.error(f"Email connection test failed: {str(e)}")
            return False