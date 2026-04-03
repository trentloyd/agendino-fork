from pydantic import BaseModel


class LinkRecordingEventDTO(BaseModel):
    recording_id: int
    event_id: int
