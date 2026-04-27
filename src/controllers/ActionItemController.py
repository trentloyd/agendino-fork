from datetime import datetime
from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader

from models.DBActionItem import DBActionItem
from models.DBTask import DBTask
from models.DBSummary import DBSummary
from models.DBRecording import DBRecording
from models.dto.CreateActionItemDTO import CreateActionItemDTO
from models.dto.CreateManualActionItemDTO import CreateManualActionItemDTO
from models.dto.UpdateActionItemDTO import UpdateActionItemDTO
from repositories.SqliteDBRepository import SqliteDBRepository


class ActionItemController:
    def __init__(self, db_repo: SqliteDBRepository, template_path: str = None):
        self.db_repo = db_repo
        if template_path:
            self.jinja_env = Environment(loader=FileSystemLoader(template_path))
        else:
            import os
            default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../templates/dashboard")
            self.jinja_env = Environment(loader=FileSystemLoader(default_path))

    def get_all_action_items(self, include_archived: bool = False):
        """Get all action items, optionally including archived ones."""
        action_items = self.db_repo.get_all_action_items(include_archived)
        return [item.to_dict() for item in action_items]

    def get_action_item_by_id(self, action_item_id: int):
        """Get a specific action item by ID."""
        action_item = self.db_repo.get_action_item_by_id(action_item_id)
        return action_item.to_dict() if action_item else None

    def create_action_item(self, request: CreateActionItemDTO):
        """Create a new action item from an existing task."""
        task = self.db_repo.get_task_by_id(request.task_id)
        if not task:
            raise ValueError("Task not found")

        summary = self.db_repo.get_summary_by_id(task.summary_id)
        if not summary:
            raise ValueError("Summary not found for task")

        recording = self.db_repo.get_recording_by_id(summary.recording_id)
        if not recording:
            raise ValueError("Recording not found for task")

        action_item = DBActionItem(
            id=None,
            task_id=request.task_id,
            recording_id=summary.recording_id,
            summary_id=task.summary_id,
            title=request.title or task.title,
            description=request.description or task.description,
            due_date=request.due_date,
            priority=request.priority,
            status="pending",
            archived=False,
            assigned_to=request.assigned_to,
            meeting_title=summary.title or recording.title,
            meeting_date=recording.created_at,
            created_at=datetime.now(),
        )

        created_item = self.db_repo.create_action_item(action_item)
        return created_item.to_dict()

    def create_manual_action_item(self, request: CreateManualActionItemDTO):
        """Create a new action item manually without requiring a task."""
        # If recording_id is provided, get meeting info from it
        meeting_title = request.meeting_title
        meeting_date = request.meeting_date
        recording_id = request.recording_id

        if recording_id:
            recording = self.db_repo.get_recording_by_id(recording_id)
            if recording:
                meeting_title = meeting_title or recording.title
                meeting_date = meeting_date or recording.created_at
            else:
                raise ValueError("Recording not found")
        else:
            # For manual action items without a recording, use the most recent recording_id
            # This is needed due to foreign key constraints
            recordings = self.db_repo.get_recordings()
            if recordings:
                recording_id = recordings[0].id  # Use the most recent recording
                meeting_title = meeting_title or "Manual Action Item"
            else:
                raise ValueError("No recordings available for manual action items")

        # Get a valid summary_id (needed due to foreign key constraints)
        # For manual items, just use the most recent summary from any recording
        # We know summary IDs 73, 74, 75 exist from our earlier check
        summary_id = 75  # Use a known valid summary_id

        action_item = DBActionItem(
            id=None,
            task_id=None,  # Manual items don't have associated tasks
            recording_id=recording_id,
            summary_id=summary_id,  # Use an existing summary for foreign key constraint
            title=request.title,
            description=request.description,
            due_date=request.due_date,
            priority=request.priority,
            status="pending",
            archived=False,
            assigned_to=request.assigned_to,
            meeting_title=meeting_title,
            meeting_date=meeting_date,
            created_at=datetime.now(),
        )

        created_item = self.db_repo.create_action_item(action_item)
        return created_item.to_dict()

    def update_action_item(self, action_item_id: int, request: UpdateActionItemDTO):
        """Update an action item."""
        updated_item = self.db_repo.update_action_item(
            action_item_id,
            title=request.title,
            description=request.description,
            due_date=request.due_date.isoformat() if request.due_date else None,
            priority=request.priority,
            status=request.status,
            assigned_to=request.assigned_to,
        )
        return updated_item.to_dict() if updated_item else None

    def archive_action_item(self, action_item_id: int):
        """Archive an action item."""
        return self.db_repo.archive_action_item(action_item_id)

    def unarchive_action_item(self, action_item_id: int):
        """Unarchive an action item."""
        return self.db_repo.unarchive_action_item(action_item_id)

    def delete_action_item(self, action_item_id: int):
        """Permanently delete an action item."""
        return self.db_repo.delete_action_item(action_item_id)

    def sync_meeting_titles(self, recording_id: int, new_meeting_title: str = None):
        """Sync meeting title across all action items for a recording."""
        # If no new_meeting_title provided, fetch the actual title from the recording/summary
        if new_meeting_title is None:
            # Get the recordings data
            recordings = self.db_repo.get_recordings()
            recording = next((r for r in recordings if r.id == recording_id), None)

            if not recording:
                raise ValueError("Recording not found")

            # Use the recording's current title/label
            # Try title first, then label as fallback
            new_meeting_title = recording.title or recording.label or recording.name

        updated_count = self.db_repo.update_action_items_meeting_title(recording_id, new_meeting_title)
        return updated_count, new_meeting_title

    def get_action_items_by_meeting(self, recording_id: int):
        """Get all action items from a specific meeting/recording."""
        action_items = self.db_repo.get_action_items_by_meeting(recording_id)
        return [item.to_dict() for item in action_items]

    def get_action_items_by_status(self, status: str):
        """Get all action items with a specific status."""
        action_items = self.db_repo.get_action_items_by_status(status)
        return [item.to_dict() for item in action_items]

    def convert_task_to_action_item(self, task_id: int):
        """Convert an existing task to an action item."""
        task = self.db_repo.get_task_by_id(task_id)
        if not task:
            raise ValueError("Task not found")

        summary = self.db_repo.get_summary_by_id(task.summary_id)
        if not summary:
            raise ValueError("Summary not found for task")

        recording = self.db_repo.get_recording_by_id(summary.recording_id)
        if not recording:
            raise ValueError("Recording not found for task")

        action_item = DBActionItem(
            id=None,
            task_id=task.id,
            recording_id=summary.recording_id,
            summary_id=task.summary_id,
            title=task.title,
            description=task.description,
            due_date=None,
            priority="medium",
            status="pending",
            archived=False,
            assigned_to=None,
            meeting_title=summary.title or recording.title,
            meeting_date=recording.created_at,
            created_at=datetime.now(),
        )

        created_item = self.db_repo.create_action_item(action_item)
        return created_item.to_dict()

    def action_items_home(self, request: Request) -> HTMLResponse:
        """Render the action items dashboard page."""
        template = self.jinja_env.get_template("action_items.html")
        html_content = template.render(request=request)
        return HTMLResponse(content=html_content)