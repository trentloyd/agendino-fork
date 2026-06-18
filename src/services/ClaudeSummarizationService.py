import json
import logging
import os
from anthropic import Anthropic
from json_repair import repair_json

logger = logging.getLogger(__name__)

class ClaudeSummarizationService:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self.client = Anthropic(api_key=key)

    def summarize(self, transcript: str, system_prompt: str, recording_datetime: str = None) -> dict:
        """Generate summary, title, and tags from transcript using Claude."""

        user_message = f"""Transcript to summarize:

{transcript}"""

        if recording_datetime:
            user_message = f"Recording date/time: {recording_datetime}\n\n{user_message}"

        user_message += """

Please provide your response as JSON with this structure:
{
  "title": "Brief descriptive title",
  "tags": ["tag1", "tag2", "tag3"],
  "summary": "Full markdown summary following the system prompt"
}"""

        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8096,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": user_message
                }]
            )

            raw = response.content[0].text
            return self._parse_response(raw)

        except Exception as e:
            logger.error(f"Claude summarization failed: {e}")
            raise

    @staticmethod
    def _parse_response(raw: str) -> dict:
        """Parse the JSON response from Claude into title, tags, summary."""

        def _extract(data: dict) -> dict:
            title = data.get("title", "").strip()
            tags_raw = data.get("tags", [])
            if isinstance(tags_raw, list):
                tags = [str(t).strip() for t in tags_raw if str(t).strip()]
            else:
                tags = [t.strip() for t in str(tags_raw).split(",") if t.strip()]
            summary = data.get("summary", "").strip()
            return {"title": title, "tags": tags, "summary": summary}

        # Strip markdown code fences if present
        stripped = raw.strip()
        if stripped.startswith("```"):
            newline_pos = stripped.find("\n")
            if newline_pos != -1:
                stripped = stripped[newline_pos + 1:]
            if stripped.rstrip().endswith("```"):
                stripped = stripped.rstrip()[:-3].rstrip()
            stripped = stripped.strip()
        else:
            stripped = stripped

        # 1. Try strict JSON parse
        try:
            data = json.loads(stripped)
            if isinstance(data, str):
                data = json.loads(data)
            if isinstance(data, dict):
                return _extract(data)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        # 2. Fallback: repair malformed JSON (handles unescaped newlines in strings, etc.)
        logger.warning("Strict JSON parse failed, attempting repair…")
        try:
            repaired = repair_json(stripped, return_objects=True)
            if isinstance(repaired, str):
                repaired = json.loads(repaired)
            if isinstance(repaired, dict):
                logger.info("JSON repair succeeded")
                return _extract(repaired)
        except Exception as e:
            logger.warning("JSON repair also failed: %s", e)

        # 3. Last resort: return raw text as summary
        logger.warning("All JSON parsing failed, returning raw text as summary")
        return {"title": "", "tags": [], "summary": raw.strip()}
