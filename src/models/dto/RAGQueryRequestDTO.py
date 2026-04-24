from pydantic import BaseModel


class RAGQueryRequestDTO(BaseModel):
    query: str
    top_k: int | None = 5
    summary_ids: list[int] | None = None
    search_mode: str | None = "quick"  # "quick" (summaries only) or "deep" (summaries + transcripts)
