from pydantic import BaseModel


class CreateSharedCalendarDTO(BaseModel):
    name: str
    ical_url: str
    color: str = "#0d6efd"
    is_enabled: bool = True
    sync_interval_minutes: int = 30


class UpdateSharedCalendarDTO(BaseModel):
    name: str | None = None
    ical_url: str | None = None
    color: str | None = None
    is_enabled: bool | None = None
    sync_interval_minutes: int | None = None
