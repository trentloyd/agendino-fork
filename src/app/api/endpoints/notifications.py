from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from datetime import time

from app import depends
from services.EmailService import EmailService
from services.DailyNotificationService import DailyNotificationService

router = APIRouter()


class TestNotificationRequest(BaseModel):
    email: EmailStr


class NotificationConfigResponse(BaseModel):
    email_configured: bool
    smtp_server: str | None
    from_email: str | None


@router.get("/config")
async def get_notification_config(
    email_service: EmailService = Depends(depends.get_email_service),
) -> NotificationConfigResponse:
    """Get current email notification configuration."""
    return NotificationConfigResponse(
        email_configured=email_service.enabled,
        smtp_server=email_service.smtp_server,
        from_email=email_service.from_email,
    )


@router.post("/test")
async def send_test_notification(
    request: TestNotificationRequest,
    notification_service: DailyNotificationService = Depends(depends.get_notification_service),
):
    """Send a test notification email immediately."""
    try:
        success = await notification_service.send_test_notification(request.email)
        if success:
            return {"ok": True, "message": f"Test notification sent to {request.email}"}
        else:
            return {"ok": False, "error": "Failed to send test notification"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send test notification: {str(e)}")


@router.post("/test-connection")
async def test_email_connection(
    email_service: EmailService = Depends(depends.get_email_service),
):
    """Test SMTP connection configuration."""
    if not email_service.enabled:
        return {"ok": False, "error": "Email service not configured"}

    success = email_service.test_connection()
    if success:
        return {"ok": True, "message": "Email connection successful"}
    else:
        return {"ok": False, "error": "Email connection failed"}