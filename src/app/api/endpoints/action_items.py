from fastapi import APIRouter, Depends, HTTPException
from typing import List, Optional

from app import depends
from controllers.ActionItemController import ActionItemController
from models.dto.CreateActionItemDTO import CreateActionItemDTO
from models.dto.CreateManualActionItemDTO import CreateManualActionItemDTO
from models.dto.UpdateActionItemDTO import UpdateActionItemDTO

router = APIRouter()


@router.get("/action-items")
async def get_all_action_items(
    include_archived: bool = False,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Get all action items, optionally including archived ones."""
    return action_item_controller.get_all_action_items(include_archived)


@router.get("/action-items/{action_item_id}")
async def get_action_item(
    action_item_id: int,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Get a specific action item by ID."""
    action_item = action_item_controller.get_action_item_by_id(action_item_id)
    if not action_item:
        raise HTTPException(status_code=404, detail="Action item not found")
    return action_item


@router.post("/action-items")
async def create_action_item(
    request: CreateActionItemDTO,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Create a new action item from an existing task."""
    return action_item_controller.create_action_item(request)


@router.post("/action-items/manual")
async def create_manual_action_item(
    request: CreateManualActionItemDTO,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Create a new action item manually without requiring a task."""
    return action_item_controller.create_manual_action_item(request)


@router.put("/action-items/{action_item_id}")
async def update_action_item(
    action_item_id: int,
    request: UpdateActionItemDTO,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Update an action item."""
    updated_item = action_item_controller.update_action_item(action_item_id, request)
    if not updated_item:
        raise HTTPException(status_code=404, detail="Action item not found")
    return updated_item


@router.post("/action-items/{action_item_id}/archive")
async def archive_action_item(
    action_item_id: int,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Archive an action item."""
    success = action_item_controller.archive_action_item(action_item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Action item not found")
    return {"message": "Action item archived successfully"}


@router.post("/action-items/{action_item_id}/unarchive")
async def unarchive_action_item(
    action_item_id: int,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Unarchive an action item."""
    success = action_item_controller.unarchive_action_item(action_item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Action item not found")
    return {"message": "Action item unarchived successfully"}


@router.delete("/action-items/{action_item_id}")
async def delete_action_item(
    action_item_id: int,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Permanently delete an action item."""
    success = action_item_controller.delete_action_item(action_item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Action item not found")
    return {"message": "Action item deleted successfully"}


@router.get("/recordings/{recording_id}/action-items")
async def get_action_items_by_meeting(
    recording_id: int,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Get all action items from a specific meeting/recording."""
    return action_item_controller.get_action_items_by_meeting(recording_id)


@router.get("/action-items/status/{status}")
async def get_action_items_by_status(
    status: str,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Get all action items with a specific status."""
    return action_item_controller.get_action_items_by_status(status)


@router.post("/tasks/{task_id}/convert-to-action-item")
async def convert_task_to_action_item(
    task_id: int,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Convert an existing task to an action item."""
    return action_item_controller.convert_task_to_action_item(task_id)


@router.post("/recordings/{recording_id}/sync-meeting-title")
async def sync_meeting_titles(
    recording_id: int,
    meeting_title: str = None,
    action_item_controller: ActionItemController = Depends(depends.get_action_item_controller),
):
    """Sync meeting title across all action items for a recording.

    If meeting_title is provided, uses that title.
    If meeting_title is None, fetches the current title from recording/summary.
    """
    updated_count, actual_title = action_item_controller.sync_meeting_titles(recording_id, meeting_title)

    if meeting_title is None:
        message = f"Synced {updated_count} action items with current meeting title: '{actual_title}'"
    else:
        message = f"Updated {updated_count} action items with new meeting title: '{actual_title}'"

    return {
        "message": message,
        "updated_count": updated_count,
        "meeting_title": actual_title
    }