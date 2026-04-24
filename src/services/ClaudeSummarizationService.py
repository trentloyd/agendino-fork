import json
import logging
import os
import re
from anthropic import Anthropic

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
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": user_message
                }]
            )
            
            response_text = response.content[0].text
            
            # Strip markdown code blocks if present
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                response_text = json_match.group(1)
            
            # Try to parse JSON response
            try:
                result = json.loads(response_text)
                return {
                    "title": result.get("title", "Untitled"),
                    "tags": result.get("tags", []),
                    "summary": result.get("summary", response_text)
                }
            except json.JSONDecodeError:
                # If not JSON, treat entire response as summary
                logger.warning("Claude response was not valid JSON, using as plain summary")
                return {
                    "title": "Summary",
                    "tags": [],
                    "summary": response_text
                }
                
        except Exception as e:
            logger.error(f"Claude summarization failed: {e}")
            raise
