from pydantic import BaseModel


class SummarizeRequestDTO(BaseModel):
    prompt_id: str
