from pydantic import BaseModel


class DeleteRecordingRequestDTO(BaseModel):
    delete_device: bool = False  # Kept for backward compat; device deletion now handled by browser WebUSB
    delete_local: bool = False
    delete_db: bool = False
