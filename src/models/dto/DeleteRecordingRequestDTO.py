from pydantic import BaseModel


class DeleteRecordingRequestDTO(BaseModel):
    delete_device: bool = False
    delete_local: bool = False
    delete_db: bool = False
