from __future__ import annotations

import os
from datetime import datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates

from models.DBRecording import DBRecording
from models.DBTask import DBTask
from repositories.LocalRecordingsRepository import LocalRecordingsRepository, ALLOWED_AUDIO_EXTENSIONS
from repositories.SqliteDBRepository import SqliteDBRepository
from repositories.SystemPromptsRepository import SystemPromptsRepository
from services.SummarizationService import SummarizationService
from services.TaskGenerationService import TaskGenerationService
from services.TranscriptionService import TranscriptionService
from services.WhisperTranscriptionService import WhisperTranscriptionService
from services.ObsidianService import ObsidianService
from models.DBActionItem import DBActionItem

MIME_TYPES = {
    "hda": "audio/mpeg",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "wma": "audio/x-ms-wma",
}


class DashboardController:
    def __init__(
        self,
        sqlite_db_repository: SqliteDBRepository,
        local_recordings_repository: LocalRecordingsRepository,
        transcription_service: TranscriptionService,
        summarization_service: SummarizationService,
        task_generation_service: TaskGenerationService,
        system_prompts_repository: SystemPromptsRepository,
        template_path: str,
        publish_services: dict[str, object] | None = None,
        whisper_transcription_service: WhisperTranscriptionService | None = None,
    ):
        self._sqlite_db_repository = sqlite_db_repository
        self._local_recordings_repository = local_recordings_repository
        self._transcription_service = transcription_service
        self._summarization_service = summarization_service
        self._task_generation_service = task_generation_service
        self._system_prompts_repository = system_prompts_repository
        self._templates = Jinja2Templates(directory=template_path)
        self._publish_services: dict[str, object] = publish_services or {}
        self._whisper_transcription_service = whisper_transcription_service

    @staticmethod
    def _bare_name(name: str) -> str:
        """Strip any known audio extension from the filename."""
        root, ext = os.path.splitext(name)
        if ext.lower() in ALLOWED_AUDIO_EXTENSIONS:
            return root
        return name

    @staticmethod
    def _parse_recording_datetime(bare_name: str) -> str | None:
        try:
            parts = bare_name.split("-")
            if len(parts) >= 2:
                dt_str = f"{parts[0]}-{parts[1]}"
                dt = datetime.strptime(dt_str, "%Y%b%d-%H%M%S")
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, IndexError):
            pass
        return None

    def home(self, request: Request):
        return self._templates.TemplateResponse(request=request, name="home.html")

    def list_local_recordings(self):
        return self._local_recordings_repository.get_all()

    def get_recordings_status(self) -> dict:
        local_files = self._local_recordings_repository.get_all()
        db_recordings = self._sqlite_db_repository.get_recordings()
        latest_summaries = self._sqlite_db_repository.get_latest_summaries_map()

        # Map bare name → local filename (preserving actual extension)
        local_map: dict[str, str] = {}
        for f in local_files:
            local_map[self._bare_name(f)] = f

        db_map = {self._bare_name(r.name): r for r in db_recordings}

        all_names = set()
        all_names.update(local_map.keys())
        all_names.update(db_map.keys())

        recordings = []
        for bare_name in sorted(
            all_names,
            key=lambda n: (
                db_map.get(n).recorded_at
                if db_map.get(n) and db_map.get(n).recorded_at
                else self._parse_recording_datetime(n) or ""
            ),
            reverse=True,
        ):
            on_local = bare_name in local_map
            db_rec = db_map.get(bare_name)
            latest_summary = latest_summaries.get(bare_name)

            # Determine file extension
            file_ext = "hda"
            if db_rec:
                file_ext = db_rec.file_extension
            elif on_local:
                _, ext = os.path.splitext(local_map[bare_name])
                file_ext = ext.lstrip(".").lower() if ext else "hda"

            # Parse date/time: DB recorded_at > name-parsed date
            rec_date = None
            rec_time = None
            db_recorded_at = db_rec.recorded_at if db_rec else None
            if db_recorded_at:
                parts = db_recorded_at.split(" ", 1)
                rec_date = parts[0]
                rec_time = parts[1] if len(parts) > 1 else None
            if not rec_date:
                parsed_dt = self._parse_recording_datetime(bare_name)
                if parsed_dt:
                    rec_date, rec_time = parsed_dt.split(" ", 1)

            # Duration from DB
            duration = None
            if db_rec and db_rec.duration and db_rec.duration > 0:
                duration = db_rec.duration

            # Size from local file
            size = None
            if on_local:
                local_filename = local_map[bare_name]
                size = self._local_recordings_repository.get_file_size(local_filename)

            recordings.append(
                {
                    "name": bare_name,
                    "on_device": False,
                    "on_local": on_local,
                    "in_db": db_rec is not None,
                    "file_extension": file_ext,
                    "duration": duration,
                    "size": size,
                    "date": rec_date,
                    "time": rec_time,
                    "recorded_at": db_recorded_at,
                    "recording_type": None,
                    "db_label": db_rec.label if db_rec else None,
                    "db_id": db_rec.id if db_rec else None,
                    "db_title": latest_summary.title if latest_summary else None,
                    "db_tags": latest_summary.tags.split(",") if latest_summary and latest_summary.tags else [],
                    "has_transcript": (
                        db_rec.transcript is not None and len(db_rec.transcript) > 0 if db_rec else False
                    ),
                    "has_summary": latest_summary is not None,
                    "summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
                    "notion_url": latest_summary.notion_url if latest_summary else None,
                }
            )

        return {
            "device": {
                "connected": False,
                "model": None,
            },
            "storage": None,
            "counts": {
                "device": 0,
                "local": len(local_files),
                "db": len(db_recordings),
            },
            "recordings": recordings,
        }


    def upload_recording(self, filename: str, file_data: bytes, label: str = "") -> dict:
        """Save an uploaded audio file locally and insert a DB record."""
        _, ext = os.path.splitext(filename)
        ext_lower = ext.lower()
        if ext_lower not in ALLOWED_AUDIO_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_AUDIO_EXTENSIONS))
            return {"ok": False, "error": f"Unsupported file type '{ext}'. Allowed: {allowed}"}

        file_ext = ext_lower.lstrip(".")
        bare_name = self._bare_name(filename)

        # Reject duplicates
        if self._local_recordings_repository.exists(filename):
            return {"ok": False, "error": f"A file named '{filename}' already exists"}
        if self._sqlite_db_repository.get_recording_by_name(bare_name):
            return {"ok": False, "error": f"A recording named '{bare_name}' already exists in the database"}

        # Save file to local_recordings
        self._local_recordings_repository.save(filename, file_data)

        # Extract audio duration using mutagen
        duration = self._get_audio_duration(self._local_recordings_repository.get_path(filename))

        # Insert DB record
        db_rec = DBRecording(
            id=None,
            name=bare_name,
            label=label or bare_name,
            duration=duration,
            file_extension=file_ext,
            created_at=datetime.now(),
        )
        new_id = self._sqlite_db_repository.insert_recording(db_rec)

        return {
            "ok": True,
            "name": bare_name,
            "file_extension": file_ext,
            "db_id": new_id,
            "message": f"Uploaded '{filename}' successfully",
        }

    @staticmethod
    def _get_audio_duration(file_path: str) -> int:
        """Extract audio duration in seconds using mutagen. Returns 0 on failure."""
        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(file_path)
            if audio and audio.info and audio.info.length:
                return int(audio.info.length)
        except Exception:
            pass
        return 0

    def update_recording_datetime(self, name: str, recorded_at: str) -> dict:
        """Update the recorded_at datetime for a recording."""
        bare_name = self._bare_name(name)
        # Validate datetime format
        try:
            datetime.strptime(recorded_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                datetime.strptime(recorded_at, "%Y-%m-%d %H:%M")
                recorded_at = f"{recorded_at}:00"
            except ValueError:
                return {"ok": False, "error": "Invalid datetime format. Use YYYY-MM-DD HH:MM:SS or YYYY-MM-DD HH:MM"}

        updated = self._sqlite_db_repository.update_recording(bare_name, recorded_at=recorded_at)
        if not updated:
            return {"ok": False, "error": f"Recording '{bare_name}' not found"}
        return {"ok": True, "name": bare_name, "recorded_at": recorded_at}

    def delete_recording(
        self,
        name: str,
        delete_local: bool,
        delete_db: bool,
    ) -> dict:
        bare_name = self._bare_name(name)
        db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
        file_ext = db_rec.file_extension if db_rec else "hda"
        local_filename = f"{bare_name}.{file_ext}"
        results = []
        errors = []


        if delete_local:
            deleted = self._local_recordings_repository.delete(local_filename)
            if deleted:
                results.append("local file")
            else:
                errors.append("Local file not found")

        if delete_db:
            deleted = self._sqlite_db_repository.delete_recording(bare_name)
            if deleted:
                results.append("database record")
            else:
                errors.append("Database record not found")

        if errors and not results:
            return {"ok": False, "error": "; ".join(errors)}

        message = f"Deleted '{bare_name}' from: {', '.join(results)}"
        if errors:
            message += f" (warnings: {'; '.join(errors)})"

        return {"ok": True, "message": message, "deleted": results, "warnings": errors}

    def _resolve_local_filename(self, bare_name: str) -> tuple[str, str]:
        """Return (local_filename, file_extension) for a recording.

        Checks the DB first, then scans local files for any known extension.
        """
        db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
        if db_rec:
            ext = db_rec.file_extension
            local_name = f"{bare_name}.{ext}"
            if self._local_recordings_repository.exists(local_name):
                return local_name, ext

        # Fallback: scan local files for any known extension
        for ext_dot in ALLOWED_AUDIO_EXTENSIONS:
            candidate = f"{bare_name}{ext_dot}"
            if self._local_recordings_repository.exists(candidate):
                return candidate, ext_dot.lstrip(".")
        return f"{bare_name}.hda", "hda"

    def transcribe_recording(self, name: str, engine: str = "gemini") -> dict:
        bare_name = self._bare_name(name)
        local_filename, file_ext = self._resolve_local_filename(bare_name)

        if not self._local_recordings_repository.exists(local_filename):
            return {"ok": False, "error": f"Local file '{local_filename}' not found"}

        db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
        if db_rec and db_rec.transcript:
            return {"ok": True, "transcript": db_rec.transcript, "cached": True}

        audio_path = self._local_recordings_repository.get_path(local_filename)
        mime_type = MIME_TYPES.get(file_ext, "audio/mpeg")

        # Select transcription engine
        if engine == "whisper":
            if not self._whisper_transcription_service:
                return {"ok": False, "error": "Whisper transcription service is not available"}
            svc = self._whisper_transcription_service
        else:
            svc = self._transcription_service

        try:
            transcript = svc.transcribe(audio_path, mime_type=mime_type)
        except Exception as e:
            return {"ok": False, "error": f"Transcription failed: {str(e)}"}

        self._sqlite_db_repository.save_transcript(bare_name, transcript)
        return {"ok": True, "transcript": transcript, "cached": False}

    def get_audio_file_path(self, name: str) -> tuple[str | None, str]:
        """Return (file_path, file_extension) or (None, '') if not found."""
        bare_name = self._bare_name(name)
        local_filename, file_ext = self._resolve_local_filename(bare_name)
        if not self._local_recordings_repository.exists(local_filename):
            return None, ""
        return self._local_recordings_repository.get_path(local_filename), file_ext

    def get_transcript(self, name: str) -> dict:
        bare_name = self._bare_name(name)
        transcript = self._sqlite_db_repository.get_transcript(bare_name)
        if transcript:
            return {"ok": True, "transcript": transcript}
        return {"ok": False, "error": "No transcript found"}

    def update_transcript(self, name: str, transcript: str) -> dict:
        bare_name = self._bare_name(name)
        updated = self._sqlite_db_repository.update_transcript(bare_name, transcript)
        if not updated:
            return {"ok": False, "error": f"Recording '{bare_name}' not found"}
        return {"ok": True, "name": bare_name, "transcript": transcript}

    def list_system_prompts(self) -> dict:
        prompts = self._system_prompts_repository.get_all()
        return {"ok": True, "prompts": prompts}

    def summarize_recording(self, name: str, prompt_id: str) -> dict:
        bare_name = self._bare_name(name)

        transcript = self._sqlite_db_repository.get_transcript(bare_name)
        if not transcript:
            return {"ok": False, "error": "No transcript found — transcribe the recording first"}

        prompt_content = self._system_prompts_repository.get_prompt_content(prompt_id)
        if not prompt_content:
            return {"ok": False, "error": f"System prompt '{prompt_id}' not found"}

        recording_datetime = self._parse_recording_datetime(bare_name)

        try:
            result = self._summarization_service.summarize(
                transcript, prompt_content, recording_datetime=recording_datetime
            )
        except Exception as e:
            return {"ok": False, "error": f"Summarization failed: {str(e)}"}

        summary = result.get("summary", "")
        title = result.get("title", "")
        tags = result.get("tags", [])
        tags_str = ",".join(tags)

        saved = self._sqlite_db_repository.save_summarization_result(
            bare_name,
            summary,
            title,
            tags_str,
            prompt_id=prompt_id,
        )

        # Auto-extract action items from DefaultSummary
        if prompt_id == "en/General/DefaultSummary":
            recording = self._sqlite_db_repository.get_recording_by_name(bare_name)
            if recording:
                self._extract_action_items_from_summary(summary, saved, recording)

        return {
            "ok": True,
            "summary_id": saved.id,
            "version": saved.version,
            "summary": summary,
            "title": title,
            "tags": tags,
        }

    def get_summaries(self, name: str) -> dict:
        bare_name = self._bare_name(name)
        summaries = self._sqlite_db_repository.get_summaries(bare_name)
        if not summaries:
            return {"ok": False, "error": "No summary found"}

        return {
            "ok": True,
            "summaries": [
                {
                    "id": s.id,
                    "version": s.version,
                    "title": s.title or "",
                    "tags": s.tags.split(",") if s.tags else [],
                    "summary": s.summary,
                    "prompt_id": s.prompt_id,
                    "notion_url": s.notion_url,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in summaries
            ],
        }

    def get_summary(self, name: str) -> dict:
        bare_name = self._bare_name(name)
        db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
        if db_rec and db_rec.summary:
            tags = db_rec.tags.split(",") if db_rec.tags else []
            return {"ok": True, "summary": db_rec.summary, "title": db_rec.title or "", "tags": tags}

        result = self.get_summaries(name)
        if not result.get("ok"):
            return result
        latest = (result.get("summaries") or [None])[0]
        if not latest:
            return {"ok": False, "error": "No summary found"}
        return {
            "ok": True,
            "summary": latest.get("summary", ""),
            "title": latest.get("title", ""),
            "tags": latest.get("tags", []),
        }

    def update_summary(
        self, summary_id: int, title: str | None = None, tags: list[str] | None = None, summary: str | None = None
    ) -> dict:
        if title is None and tags is None and summary is None:
            return {"ok": False, "error": "Nothing to update"}

        updated = self._sqlite_db_repository.get_summary_by_id(summary_id)
        if not updated:
            return {"ok": False, "error": f"Summary '{summary_id}' not found"}

        if title is not None or tags is not None:
            next_title = title.strip() if title is not None else (updated.title or "")
            next_tags = tags if tags is not None else (updated.tags.split(",") if updated.tags else [])
            tags_list = [t.strip() for t in next_tags if t.strip()]
            updated = self._sqlite_db_repository.update_summary_metadata(summary_id, next_title, ",".join(tags_list))
            if not updated:
                return {"ok": False, "error": f"Summary '{summary_id}' not found"}

        if summary is not None:
            updated = self._sqlite_db_repository.update_summary_content(summary_id, summary)
            if not updated:
                return {"ok": False, "error": f"Summary '{summary_id}' not found"}

        return {
            "ok": True,
            "summary_id": updated.id,
            "title": updated.title or "",
            "tags": updated.tags.split(",") if updated.tags else [],
            "summary": updated.summary,
        }

    def update_summary_metadata(self, summary_id: int, title: str, tags: list[str]) -> dict:
        return self.update_summary(summary_id=summary_id, title=title, tags=tags)

    def update_recording_metadata(self, name: str, title: str, tags: list[str]) -> dict:
        bare_name = self._bare_name(name)
        db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
        if not db_rec:
            return {"ok": False, "error": f"Recording '{bare_name}' not found"}
        tags_list = [t.strip() for t in tags if t.strip()]
        self._sqlite_db_repository.update_title_and_tags(bare_name, title.strip(), ",".join(tags_list))
        return {"ok": True, "title": title.strip(), "tags": tags_list}

    _DESTINATION_META: dict[str, dict] = {
        "notion": {"label": "Notion", "icon": "bi-journal-bookmark"},
    }

    def get_publish_destinations(self) -> dict:
        destinations = []
        for key, svc in self._publish_services.items():
            if hasattr(svc, "is_configured") and svc.is_configured:
                meta = self._DESTINATION_META.get(key, {})
                destinations.append(
                    {
                        "id": key,
                        "label": meta.get("label", key.capitalize()),
                        "icon": meta.get("icon", "bi-share"),
                    }
                )
        return {"ok": True, "destinations": destinations}

    def publish_recording(self, name: str, destination: str) -> dict:
        bare_name = self._bare_name(name)
        db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
        if not db_rec:
            return {"ok": False, "error": "No summary found — summarize the recording first"}
        summaries = self._sqlite_db_repository.get_summaries(bare_name)
        if not summaries:
            return {"ok": False, "error": "No summary found — summarize the recording first"}
        return self.publish_summary(summaries[0].id, destination)

    def publish_summary(self, summary_id: int, destination: str) -> dict:
        svc = self._publish_services.get(destination)
        if not svc:
            return {"ok": False, "error": f"Unknown publish destination: {destination}"}

        summary = self._sqlite_db_repository.get_summary_by_id(summary_id)
        if not summary:
            return {"ok": False, "error": "Summary not found"}

        title = summary.title or summary.recording_name
        tags = summary.tags.split(",") if summary.tags else []

        publish_title = title
        recording_dt = self._parse_recording_datetime(summary.recording_name)
        if recording_dt:
            date_only = recording_dt.split(" ")[0]
            publish_title = f"{date_only} {title}"

        # *** KEY CHANGE: Get tasks for this summary ***
        tasks = self._sqlite_db_repository.get_tasks_by_summary(summary_id)
        tasks_list = []
        if tasks:
            for t in tasks:
                tasks_list.append({
                    "description": t.title,
                    "owner": "Unassigned",  # DBTask doesn't have owner/due_date fields
                    "due_date": "",
                    "priority": ""
                })

        try:
            result = svc.publish_summary(
                title=publish_title,
                summary_markdown=summary.summary,
                tags=tags,
                recording_name=summary.recording_name,
                tasks=tasks_list,  # *** Pass tasks to Obsidian ***
            )
            if result.get("ok") and result.get("url"):
                self._sqlite_db_repository.save_notion_url(summary_id, result["url"])
            return result
        except Exception as e:
            return {"ok": False, "error": f"Publish failed: {str(e)}"}

    # ─── Tasks ───────────────────────────────────────────────────

    def generate_tasks(self, summary_id: int) -> dict:
        summary = self._sqlite_db_repository.get_summary_by_id(summary_id)
        if not summary:
            return {"ok": False, "error": f"Summary '{summary_id}' not found"}

        if not summary.summary or not summary.summary.strip():
            return {"ok": False, "error": "Summary is empty — cannot generate tasks"}

        # Delete existing tasks for this summary (regeneration replaces old ones)
        self._sqlite_db_repository.delete_tasks_by_summary(summary_id)

        try:
            raw_tasks = self._task_generation_service.generate_tasks(
                summary_text=summary.summary,
                summary_title=summary.title,
            )
        except Exception as e:
            return {"ok": False, "error": f"Task generation failed: {str(e)}"}

        if not raw_tasks:
            return {"ok": False, "error": "AI returned no tasks"}

        # Convert raw dicts to DBTask objects
        db_tasks = []
        for t in raw_tasks:
            subtasks = [
                DBTask(id=None, summary_id=summary_id, title=s["title"], description=s.get("description", ""))
                for s in t.get("subtasks", [])
            ]
            db_task = DBTask(
                id=None,
                summary_id=summary_id,
                title=t["title"],
                description=t.get("description", ""),
                subtasks=subtasks,
            )
            db_tasks.append(db_task)

        saved = self._sqlite_db_repository.insert_tasks(db_tasks)

        # Auto-create action items from tasks
        recording = self._sqlite_db_repository.get_recording_by_id(summary.recording_id)
        for task in saved:
            self._create_action_item_from_task(task, summary, recording)

        return {
            "ok": True,
            "summary_id": summary_id,
            "tasks": [task.to_dict() for task in saved],
        }

    def _create_action_item_from_task(self, task, summary, recording):
        """Create an action item from a task."""
        try:
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
                meeting_title=summary.title or recording.title if recording else None,
                meeting_date=recording.created_at if recording else None,
                created_at=datetime.now(),
            )
            self._sqlite_db_repository.create_action_item(action_item)

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
                    meeting_title=summary.title or recording.title if recording else None,
                    meeting_date=recording.created_at if recording else None,
                    created_at=datetime.now(),
                )
                self._sqlite_db_repository.create_action_item(subtask_action_item)

        except Exception as e:
            # Log the error but don't fail the entire task generation
            print(f"Warning: Failed to create action item for task '{task.title}': {e}")

    def _extract_action_items_from_summary(self, summary_text: str, summary_record, recording):
        """Extract action items from DefaultSummary output and create action item records."""
        try:
            import re

            # Look for the Action Items section in the summary
            action_items_match = re.search(r'### Action Items\s*.*?\n(.*?)(?=###|\Z)', summary_text, re.DOTALL | re.IGNORECASE)

            if not action_items_match:
                print("No Action Items section found in DefaultSummary")
                return

            action_items_text = action_items_match.group(1).strip()

            # Parse the table format - look for lines that contain action item data
            # Expected format: | Action Item | Owner | Due Date | Priority | Dependencies | Status | Source |
            lines = action_items_text.split('\n')

            for line in lines:
                # Skip empty lines, header lines, and separator lines
                line = line.strip()
                if not line or line.startswith('|--') or 'Action Item' in line:
                    continue

                # Parse table row format
                if line.startswith('|') and line.endswith('|'):
                    columns = [col.strip() for col in line.split('|')[1:-1]]  # Remove empty first/last elements

                    if len(columns) >= 6:  # Ensure we have enough columns
                        action_item_title = columns[0].strip()
                        owner = columns[1].strip() if columns[1].strip() and columns[1].strip() != '-' else None
                        due_date_str = columns[2].strip() if columns[2].strip() and columns[2].strip() != '-' else None
                        priority = columns[3].strip() if columns[3].strip() and columns[3].strip() != '-' else 'medium'
                        dependencies = columns[4].strip() if len(columns) > 4 and columns[4].strip() and columns[4].strip() != '-' else None
                        status = columns[5].strip() if len(columns) > 5 and columns[5].strip() and columns[5].strip() != '-' else 'pending'
                        source = columns[6].strip() if len(columns) > 6 and columns[6].strip() and columns[6].strip() != '-' else None

                        # Skip empty or invalid action items
                        if not action_item_title or action_item_title == '-':
                            continue

                        # Parse due date
                        due_date = None
                        if due_date_str and due_date_str.lower() not in ['owner missing', 'date missing', '-', '']:
                            try:
                                # Try to parse various date formats
                                for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%d/%m/%Y']:
                                    try:
                                        due_date = datetime.strptime(due_date_str, fmt)
                                        break
                                    except ValueError:
                                        continue
                            except:
                                pass

                        # Normalize priority
                        if priority.lower() in ['high', 'h']:
                            priority = 'high'
                        elif priority.lower() in ['low', 'l']:
                            priority = 'low'
                        else:
                            priority = 'medium'

                        # Normalize status
                        if status.lower() in ['in progress', 'in-progress', 'in_progress']:
                            status = 'in_progress'
                        elif status.lower() in ['completed', 'done', 'complete']:
                            status = 'completed'
                        elif status.lower() in ['blocked']:
                            status = 'blocked'
                        else:
                            status = 'pending'

                        # Create the action item
                        action_item = DBActionItem(
                            id=None,
                            task_id=None,  # No task_id since this comes from summary directly
                            recording_id=recording.id,
                            summary_id=summary_record.id,
                            title=action_item_title,
                            description=f"{dependencies}\n\nSource: {source}" if dependencies or source else None,
                            due_date=due_date,
                            priority=priority,
                            status=status,
                            archived=False,
                            assigned_to=owner,
                            meeting_title=summary_record.title or recording.title,
                            meeting_date=recording.created_at,
                            created_at=datetime.now(),
                        )

                        # Save the action item
                        created_item = self._sqlite_db_repository.create_action_item(action_item)
                        print(f"Created action item from summary: {created_item.title}")

        except Exception as e:
            print(f"Warning: Failed to extract action items from DefaultSummary: {e}")

    def get_tasks(self, summary_id: int) -> dict:
        tasks = self._sqlite_db_repository.get_tasks_by_summary(summary_id)
        return {
            "ok": True,
            "summary_id": summary_id,
            "tasks": [task.to_dict() for task in tasks],
        }

    def update_task(
        self, task_id: int, title: str | None = None, description: str | None = None, status: str | None = None
    ) -> dict:
        updated = self._sqlite_db_repository.update_task(task_id, title=title, description=description, status=status)
        if not updated:
            return {"ok": False, "error": f"Task '{task_id}' not found"}
        return {"ok": True, "task": updated.to_dict()}

    def delete_task(self, task_id: int) -> dict:
        deleted = self._sqlite_db_repository.delete_task(task_id)
        if not deleted:
            return {"ok": False, "error": f"Task '{task_id}' not found"}
        return {"ok": True, "deleted": task_id}
