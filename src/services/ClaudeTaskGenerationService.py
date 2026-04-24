import json
import logging
import os
from anthropic import Anthropic

logger = logging.getLogger(__name__)

class ClaudeTaskGenerationService:
    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self.client = Anthropic(api_key=key)
    
    def generate_tasks(self, summary_text: str, summary_title: str = None) -> list[dict]:
        """Generate structured tasks from a summary using Claude."""
        
        system_prompt = """You are a task extraction assistant. Analyze the provided summary and extract actionable tasks.

For each task, provide:
- title: A clear, concise task description
- description: Additional context or details if available
- subtasks: Array of smaller subtasks if the main task can be broken down

Return ONLY valid JSON in this format:
[
  {
    "title": "Main task description",
    "description": "Additional context",
    "subtasks": [
      {"title": "Subtask 1", "description": "Details"},
      {"title": "Subtask 2", "description": "Details"}
    ]
  }
]

Rules:
- Only include clear, actionable tasks
- If no tasks are found, return an empty array []
- Do not include vague or uncertain items
- Keep titles concise (under 100 characters)
"""
        
        user_message = f"""Summary Title: {summary_title or 'Untitled'}

Summary:
{summary_text}

Extract all actionable tasks from this summary."""
        
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": user_message
                }]
            )
            
            response_text = response.content[0].text
            
            # Parse JSON response
            try:
                tasks = json.loads(response_text)
                if isinstance(tasks, list):
                    return tasks
                else:
                    logger.warning("Claude returned non-list JSON for tasks")
                    return []
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse Claude task response as JSON: {e}")
                logger.debug(f"Response was: {response_text}")
                return []
                
        except Exception as e:
            logger.error(f"Claude task generation failed: {e}")
            raise
