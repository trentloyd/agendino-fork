import logging

import chromadb

logger = logging.getLogger(__name__)


class VectorStoreRepository:
    """Wraps ChromaDB with Gemini embeddings for summary vector storage."""

    def __init__(self, persist_path: str, api_key: str | None = None):
        self._persist_path = persist_path
        self._client = chromadb.PersistentClient(path=persist_path)
        self._collection = self._client.get_or_create_collection(
            name="summaries",
            metadata={"hnsw:space": "cosine"},
        )
        self._api_key = api_key
        self._genai_client = None
        if api_key:
            from google import genai

            self._genai_client = genai.Client(api_key=api_key)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not self._genai_client:
            raise ValueError("No API key configured for embeddings")
        result = self._genai_client.models.embed_content(
            model="gemini-embedding-001",
            contents=texts,
        )
        embeddings = result.embeddings or []
        return [e.values for e in embeddings if e.values is not None]

    def add_summary(self, summary_id: int, text: str, metadata: dict) -> None:
        doc_id = f"summary_{summary_id}"
        embeddings = self._embed([text])
        metadata["document_type"] = "summary"  # Mark as summary for filtering
        self._collection.upsert(
            ids=[doc_id],
            embeddings=embeddings,
            documents=[text],
            metadatas=[metadata],
        )

    def add_transcript(self, recording_id: int, text: str, metadata: dict) -> None:
        """Add a transcript to the vector store with a different ID pattern."""
        doc_id = f"transcript_{recording_id}"
        embeddings = self._embed([text])
        metadata["document_type"] = "transcript"  # Mark as transcript for filtering
        self._collection.upsert(
            ids=[doc_id],
            embeddings=embeddings,
            documents=[text],
            metadatas=[metadata],
        )

    def search(self, query: str, top_k: int = 5, summary_ids: list[int] | None = None, search_mode: str = "quick", summary_to_recording_map: dict[int, int] | None = None) -> list[dict]:
        """Search summaries and optionally transcripts based on search mode.

        Args:
            query: Search query text
            top_k: Maximum number of results to return
            summary_ids: Optional filter by specific summary IDs
            search_mode: "quick" (summaries only) or "deep" (summaries + transcripts)
        """
        count = self._collection.count()
        if count == 0:
            return []
        query_embedding = self._embed([query])

        where_filter = None
        if search_mode == "quick":
            # Only search summaries
            where_filter = {"document_type": "summary"}
            if summary_ids:
                where_filter = {"$and": [
                    {"document_type": "summary"},
                    {"summary_id": {"$in": summary_ids}}
                ]}
        elif search_mode == "deep":
            # Search both summaries and transcripts
            if summary_ids and summary_to_recording_map:
                # Map summary_ids to recording_ids for transcript filtering
                recording_ids = [summary_to_recording_map.get(sid) for sid in summary_ids if summary_to_recording_map.get(sid)]
                where_filter = {"$or": [
                    {"$and": [{"document_type": "summary"}, {"summary_id": {"$in": summary_ids}}]},
                    {"$and": [{"document_type": "transcript"}, {"recording_id": {"$in": recording_ids}}]}
                ]}
            elif summary_ids:
                # If no mapping provided, just search summaries with the given IDs
                where_filter = {"$and": [{"document_type": "summary"}, {"summary_id": {"$in": summary_ids}}]}
            # If no summary_ids filter, search everything (summaries + transcripts)

        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=min(top_k, count),
            where=where_filter,
        )
        items = []
        for i in range(len(results["ids"][0])):
            items.append(
                {
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] if results.get("distances") else None,
                }
            )
        return items

    def is_loaded(self, summary_id: int) -> bool:
        doc_id = f"summary_{summary_id}"
        try:
            result = self._collection.get(ids=[doc_id])
            return len(result["ids"]) > 0
        except Exception:
            return False

    def is_transcript_loaded(self, recording_id: int) -> bool:
        """Check if a transcript is already loaded in the vector store."""
        doc_id = f"transcript_{recording_id}"
        try:
            result = self._collection.get(ids=[doc_id])
            return len(result["ids"]) > 0
        except Exception:
            return False

    def get_all(self):
        return self._collection.get(include=["documents", "metadatas", "embeddings"])

    def count(self) -> int:
        return self._collection.count()

    def delete_summary(self, summary_id: int) -> None:
        doc_id = f"summary_{summary_id}"
        try:
            self._collection.delete(ids=[doc_id])
        except Exception:
            pass

    def delete_transcript(self, recording_id: int) -> None:
        """Delete a transcript from the vector store."""
        doc_id = f"transcript_{recording_id}"
        try:
            self._collection.delete(ids=[doc_id])
        except Exception:
            pass

    def clear(self) -> None:
        self._client.delete_collection("summaries")
        self._collection = self._client.get_or_create_collection(
            name="summaries",
            metadata={"hnsw:space": "cosine"},
        )
