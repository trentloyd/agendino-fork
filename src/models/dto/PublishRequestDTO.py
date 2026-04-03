from pydantic import BaseModel


class PublishRequestDTO(BaseModel):
    destination: str
