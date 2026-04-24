import json
import logging
import os
import re

from anthropic import Anthropic
from json_repair import repair_json

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-20250514"

MIND_MAP_PROMPT = """You are a knowledge-mapping expert. Analyze the summaries and produce a
clean, hierarchical mind map.

RULES
1. The map has exactly THREE depth levels: central_topic → branches → children.
2. Create 3-7 branches — each is a distinct high-level THEME.
3. Each branch has 2-5 children — each is one concrete KEY INSIGHT from the summaries.
4. Labels must be SHORT: max 4 words for branches, max 6 words for children.
5. Every child MUST include `summary_ids` (array of source summary IDs).
6. Add `connections` only for genuinely cross-cutting relationships (max 3).
7. Use the same language as the summaries.
8. Do NOT repeat the same concept across branches.

Return ONLY this JSON:
{
  "central_topic": "Short overarching theme (max 4 words)",
  "branches": [
    {
      "id": "branch_1",
      "label": "Theme name",
      "children": [
        {"id": "leaf_1_1", "label": "Key insight", "summary_ids": [1, 2]}
      ]
    }
  ],
  "connections": [
    {"from": "branch_1", "to": "branch_2", "label": "relation"}
  ]
}"""

RAG_PROMPT = """You are a helpful assistant that answers questions based on the provided context.
Use ONLY the information from the context below to answer the question.
If the answer cannot be found in the context, say so clearly.
**Always respond in English, even if the context is in another language.**
Format your response in Markdown.

Context:
{context}

Question: {question}

Answer:"""


class RAGService:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self._client = Anthropic(api_key=key)

    def ask(self, question: str, context_docs: list[dict]) -> dict:
        """RAG query: answer a question using retrieved context."""
        context_parts = []
        sources = []
        for i, doc in enumerate(context_docs):
            meta = doc.get("metadata", {})
            title = meta.get("title", f"Source {i + 1}")
            text = doc.get("document", "")
            context_parts.append(f"[{title}]\n{text}")
            sources.append(
                {
                    "title": title,
                    "recording_name": meta.get("recording_name", ""),
                    "summary_id": meta.get("summary_id", ""),
                    "distance": doc.get("distance"),
                }
            )

        context = "\n\n---\n\n".join(context_parts)
        prompt = RAG_PROMPT.format(context=context, question=question)

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )

        return {
            "answer": response.content[0].text or "",
            "sources": sources,
        }

    def generate_mind_map(self, summaries: list[dict]) -> dict:
        """Generate a mind map structure from summaries using Claude."""
        summary_texts = []
        for s in summaries:
            tags = ", ".join(s.get("tags", []))
            # Truncate summary for context window efficiency
            summary_preview = s.get("summary", "")[:600]
            entry = f"[ID: {s['id']}] Title: {s.get('title', 'Untitled')}\nTags: {tags}\n{summary_preview}"
            summary_texts.append(entry)

        content = "Summaries:\n\n" + "\n\n---\n\n".join(summary_texts)

        logger.info("Generating mind map with Claude for %d summaries…", len(summaries))
        response = self._client.messages.create(
            model=MODEL,
            max_tokens=8192,
            system=MIND_MAP_PROMPT,
            messages=[{
                "role": "user",
                "content": content
            }]
        )

        raw = response.content[0].text or ""

        # Strip markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw, re.DOTALL)
        if json_match:
            raw = json_match.group(1)

        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            pass

        try:
            repaired = repair_json(raw, return_objects=True)
            if isinstance(repaired, dict):
                return repaired
        except Exception:
            pass

        logger.warning("Failed to parse mind map JSON, returning empty structure")
        return {"central_topic": "Knowledge Base", "branches": [], "connections": []}
