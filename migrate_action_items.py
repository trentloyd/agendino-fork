#!/usr/bin/env python3
"""
One-time migration script to populate action items from all existing tasks.
Run this once to backfill action items from your existing meetings.
"""

import os
import sys
from datetime import datetime

# Add the src directory to Python path so we can import modules
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

from repositories.SqliteDBRepository import SqliteDBRepository
from models.DBActionItem import DBActionItem


def get_db_repository():
    """Get database repository instance."""
    root_path = os.path.dirname(os.path.abspath(__file__))
    return SqliteDBRepository(
        db_name=os.getenv("DATABASE_NAME", "agendino.db"),
        db_path=os.path.join(root_path, "settings"),
        init_sql_script=os.path.join(root_path, "settings/db_init.sql"),
    )


def migrate_tasks_to_action_items():
    """Migrate all existing tasks to action items."""
    print("🚀 Starting action items migration...")

    db_repo = get_db_repository()

    # Get all recordings, then find their summaries and tasks
    recordings = db_repo.get_recordings()
    print(f"📊 Found {len(recordings)} recordings to process")

    total_action_items = 0

    for recording in recordings:
        print(f"\n📝 Processing recording: {recording.title or recording.name}")

        # Get the latest summary for this recording
        summary_rows = []
        try:
            import sqlite3
            conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "settings/agendino.db"))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT s.*, r.name as recording_name
                FROM summary s
                JOIN recording r ON s.recording_id = r.id
                WHERE s.recording_id = ?
                ORDER BY s.version DESC
                """,
                (recording.id,)
            ).fetchall()
            conn.close()
            summary_rows = rows
        except Exception as e:
            print(f"   ❌ Error fetching summaries for recording {recording.id}: {e}")
            continue

        if not summary_rows:
            print(f"   ⚠️  No summaries found for recording {recording.id}")
            continue

        # Process each summary (usually just the latest one, but handle multiple versions)
        for summary_row in summary_rows:
            from models.DBSummary import DBSummary
            summary = DBSummary.from_dict(dict(summary_row))

            print(f"   📝 Processing summary: {summary.title or f'Summary {summary.id}'}")

            # Get tasks for this summary
            tasks = db_repo.get_tasks_by_summary(summary.id)
            if not tasks:
                print(f"   ⚠️  No tasks found for summary {summary.id}")
                continue

            print(f"   ✅ Found {len(tasks)} tasks")

            # Process each task
            for task in tasks:
                # Check if action item already exists for this task
                existing_items = db_repo.get_action_items_by_meeting(summary.recording_id)
                task_exists = any(item.task_id == task.id for item in existing_items)

                if task_exists:
                    print(f"      ⏭️  Action item already exists for task: {task.title}")
                    continue

                # Create action item from task
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

                try:
                    created_item = db_repo.create_action_item(action_item)
                    print(f"      ✨ Created action item: {created_item.title}")
                    total_action_items += 1

                    # Also create action items for subtasks
                    for subtask in task.subtasks:
                        subtask_action_item = DBActionItem(
                            id=None,
                            task_id=subtask.id,
                            recording_id=summary.recording_id,
                            summary_id=subtask.summary_id,
                            title=subtask.title,
                            description=subtask.description,
                            due_date=None,
                            priority="low",
                            status="pending",
                            archived=False,
                            assigned_to=None,
                            meeting_title=summary.title or recording.title,
                            meeting_date=recording.created_at,
                            created_at=datetime.now(),
                        )

                        created_subtask_item = db_repo.create_action_item(subtask_action_item)
                        print(f"      ✨ Created subtask action item: {created_subtask_item.title}")
                        total_action_items += 1

                except Exception as e:
                    print(f"      ❌ Failed to create action item for task '{task.title}': {e}")

    print(f"\n🎉 Migration complete! Created {total_action_items} action items")
    return total_action_items


def main():
    """Main function to run the migration."""
    print("=" * 60)
    print("ACTION ITEMS MIGRATION SCRIPT")
    print("=" * 60)

    try:
        count = migrate_tasks_to_action_items()

        print("\n" + "=" * 60)
        print("✅ MIGRATION SUCCESSFUL")
        print(f"📈 Total action items created: {count}")
        print("\n💡 Going forward, action items will be automatically")
        print("   created when you generate tasks from new summaries.")
        print("\n🔗 Visit /action-items to view and manage your action items!")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        print("\nPlease check your database configuration and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()