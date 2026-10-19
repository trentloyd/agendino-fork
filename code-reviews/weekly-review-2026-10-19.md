# Weekly Code Review — Agendino

**Date:** 2026-10-19  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**Hardcoded `summary_id = 75` in `ActionItemController.create_manual_action_item`**
(`src/controllers/ActionItemController.py`, around line 100)
```python
summary_id = 75  # Use a known valid summary_id
```
This hardcoded ID works only in one specific database instance. Any other installation will produce a foreign-key constraint error when creating a manual action item without a recording. The comment admits it is a hack. The correct fix is to remove the `summary_id` foreign-key constraint for manual (task-less) items or look up the actual latest summary for the selected `recording_id`.

**`NotionService.publish_summary` receives an unexpected `tasks` kwarg**
(`src/controllers/DashboardController.py`, `publish_summary`)
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,   # ← NotionService does not accept this parameter
)
```
`ObsidianService.publish_summary` accepts `tasks`, but `NotionService.publish_summary` does not — it will raise `TypeError` on every Notion publish after task extraction is enabled. The two services share a dispatch call but have incompatible signatures.

**`DBActionItem.task_id` typed as `int` but used as `int | None` everywhere**
(`src/models/DBActionItem.py`)
```python
def __init__(self, id: int | None, task_id: int, ...):
```
`task_id=None` is passed from at least six call sites (`_extract_action_items_from_summary`, `create_manual_action_item`, `_create_action_item_from_task` subtasks, etc.). The type annotation is wrong and the `from_dict` factory does `task_id=data["task_id"]` without a None-guard, so a missing column would raise `KeyError`.

**Hardcoded `/opt/agendino/` paths in production code**
(`src/controllers/DashboardController.py`, `_sync_team_manager`)
```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
These paths are developer-machine-specific. They silently do nothing on any other installation (`os.path.exists(exporter)` returns False), but the log file open will raise `FileNotFoundError` if the directory doesn't exist.

**`export_team_manager.py` hardcodes developer-specific database and vault paths at module level**
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
Similarly, `ROLE`, `NONPERSON` (including `"Frank"` with a comment naming a specific interview candidate), and `ME = "Trent"` are all hardcoded personal data.

### High

**`_ensure_action_items_table` / `_ensure_calendar_tables` run a full schema inspection on every single DB operation**
(`src/repositories/SqliteDBRepository.py`)
Every `create_action_item`, `get_all_action_items`, `insert_calendar_event`, `get_calendar_events_for_day`, etc. calls `_ensure_action_items_table()` or `_ensure_calendar_tables()`, which runs `SELECT sql FROM sqlite_master` and potentially issues `ALTER TABLE` or `CREATE TABLE`. Under concurrent FastAPI requests this causes unnecessary SQLite locking and query overhead on every request.

**Race condition with `subprocess.Popen` and file handle in `_sync_team_manager`**
```python
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
    logf.flush()
    subprocess.Popen(["/bin/bash", "-c", cmd], stdout=logf, stderr=logf, start_new_session=True)
```
The `with` block closes `logf` immediately after `Popen` returns, while the subprocess continues writing to the file descriptor. The OS keeps the fd open as long as the subprocess holds it, so data isn't lost, but Python's file object is closed before the child finishes writing, which can cause buffered writes to be discarded.

**N+1 query in `get_recordings_status`**
(`src/controllers/DashboardController.py`)
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
`get_summaries()` executes a separate SQL `JOIN` per recording. For a dashboard with 100 recordings this is 100 additional round-trips per page load.

**`_reconcile_orphan_files` is called on every `get_recordings_status` request**, which is the main dashboard endpoint. It scans the filesystem twice (once for all files, once as part of normal status) and may call `os.rename` under normal dashboard refresh, which is surprising side-effect behaviour.

**ICalendar timezone stripping causes incorrect event times**
(`src/services/ICalSyncService.py`)
```python
return dt_val.strftime("%Y-%m-%d %H:%M:%S")  # Strip timezone info for naive storage
```
For users outside UTC, recurring events (e.g., a 9 AM US/Eastern meeting) will be stored at the wrong time. The correct approach is to convert to UTC before stripping.

**`get_config()` is called on every request but its result is always discarded**
(`src/app/depends.py`)
```python
def get_dashboard_controller() -> DashboardController:
    _config = get_config()  # result is never used
    return DashboardController(...)
```
The function also mutates a module-level `config` dict on each call, which is not thread-safe with multiple workers.

**Bare `except:` swallows `SystemExit` and `KeyboardInterrupt`**
(`src/controllers/DashboardController.py`, `_extract_action_items_from_summary`)
```python
except:
    pass
```
Should be `except Exception:`.

**`SystemPromptsRepository.get_all` silently ignores all non-English prompts**
despite the `system_prompts/it/` directory being committed:
```python
if lang_dir.name != "en":
    continue
```
All Italian prompts are dead code and the UI never shows them.

---

## 2. Performance Improvements

**Batch-hydrate summary fields instead of N+1 per recording**
(`src/repositories/SqliteDBRepository.py`, `get_recordings`)
`_hydrate_latest_summary_fields` issues one query per recording. Replace with a single `LEFT JOIN` on `summary` (using the `get_latest_summaries_map` pattern) and populate all recordings in one pass.

**`_ensure_action_items_table` / `_ensure_calendar_tables` — call once at startup, not on every operation**
Move schema migration to `__init__` or a dedicated `migrate()` call. Checking `sqlite_master` on every action-item read/write adds latency and contention under load.

**`get_recordings_status` calls `get_summaries(bare_name)` per row for `summary_count`**
(`src/controllers/DashboardController.py`)
Use a single `SELECT recording_id, COUNT(*) FROM summary GROUP BY recording_id` query and merge the counts into the result map. Also consider caching the entire status response for a few seconds since it's read-only at display time.

**`sync_meeting_titles` loads all recordings to find one by ID**
(`src/controllers/ActionItemController.py`)
```python
recordings = self.db_repo.get_recordings()
recording = next((r for r in recordings if r.id == recording_id), None)
```
The repository already has `get_recording_by_id`. Use it.

**`RAGController.load_transcripts` truncates long transcripts to ~5 KB**
```python
chunks = [transcript_text[i:i+4000] for i in range(0, len(transcript_text), 3000)]
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```
This discards 95 %+ of a long transcript. Implement proper chunking with overlap and store each chunk as a separate vector document (using `recording_id_chunk_N` as the key), then de-duplicate results in `search()`.

**`WhisperTranscriptionService._get_model()` lazy-loads the model but has no warm-up**
The first transcription request takes tens of seconds while the model downloads/loads. Consider pre-loading on startup (controlled by env flag) so the first user request isn't penalised.

**`DashboardController._reconcile_orphan_files` runs a filesystem scan on every page load**
Cache the result for a configurable TTL (e.g., 30 s) or only run it on explicit sync/upload actions.

---

## 3. Missing Error Handling

**No file-size limit on uploads**
(`src/app/api/endpoints/dashboard.py`, `upload_recording`)
```python
file_data = await file.read()
```
A 10 GB file will be read entirely into memory before validation. Add a maximum size check (via `Content-Length` header or streaming with a byte counter).

**`SqliteDBRepository` opens a new SQLite connection per method call — "database is locked" under concurrency**
FastAPI can serve multiple concurrent requests. SQLite in WAL mode handles concurrent reads but serialises writes. The current pattern of `_connect()` → write → `_close()` with no connection pooling or retry will produce `sqlite3.OperationalError: database is locked` under moderate load. Consider `check_same_thread=False` with a module-level lock, or switch to `aiosqlite`.

**Gemini/Anthropic API calls have no retry or timeout logic**
`TranscriptionService.transcribe`, `ClaudeSummarizationService.summarize`, `RAGService.ask`, etc. make blocking HTTP calls with no timeout parameter, no exponential back-off, and no retry on 429/503. A transient API error surfaces as a 500 to the user.

**`ICalSyncService.fetch_and_parse` has no guard on expanded event count**
A pathological recurring iCal feed (e.g., every 5-minute event for 3 years) could expand to tens of thousands of events. Add a cap (e.g., 5,000 events) with a warning.

**`_initialize_db` reads the SQL file without handling `FileNotFoundError`**
(`src/repositories/SqliteDBRepository.py`)
If `settings/db_init.sql` is missing, the exception propagates with a generic OS error message. Catch and re-raise with a helpful message pointing to the file.

**`WhisperTranscriptionService` can raise `ModuleNotFoundError`** if `faster-whisper` is not installed (it is optional). The error should be caught in `_get_model()` and returned as a structured `{"ok": False, "error": "faster-whisper is not installed"}` rather than a 500.

**`ObsidianService.publish_summary` writes the entire file at once**
If `filepath.write_text(content)` raises mid-write (disk full, permissions), the note file is left empty. Use a temp file + atomic rename.

**`DailyNotificationService.create_notification_daemon` calls `sys.exit(1)` from inside a module**
This function is also imported and called via FastAPI's dependency injection (`get_notification_service`). If configuration is missing, `sys.exit(1)` would kill the entire web server process.

**`export_team_manager.py` opens the database without a `try/except`**
```python
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
```
If `DB` path does not exist, this raises `sqlite3.OperationalError` with no user-friendly message.

**`get_audio_file_path` returns a fallback `(f"{bare_name}.hda", "hda")` when no file is found**
(`src/controllers/DashboardController.py`, `_resolve_local_filename`)
The fallback path doesn't exist, but the caller receives a non-None path and must call `exists()` again. The return value could more clearly signal "not found" by returning `(None, "")` consistently.

---

## 4. New Feature Ideas

**1. Background task queue for transcription and summarization**
Transcription (especially Whisper) and summarization can take minutes. Currently these are synchronous HTTP handlers that block the request. Introducing FastAPI `BackgroundTasks` (or a lightweight queue like `dramatiq`/`arq`) with a polling endpoint (`GET /api/dashboard/jobs/{job_id}`) would prevent gateway timeouts, allow batching, and give users real progress feedback instead of a spinning UI.

**2. Full-text search across transcripts and summaries**
The knowledge-base RAG requires loading embeddings into ChromaDB before search works. Add a simpler SQLite FTS5 index on the `recording.transcript` and `summary.summary` columns so users can do instant keyword search from the recordings dashboard without a vector store. This could replace the current "Semantic Search" fallback for users without a Gemini key.

**3. Automatic calendar-to-recording linkage by time overlap**
Currently, linking a recording to a calendar event is manual. Given that `recording.recorded_at` and `calendar_event.start_at`/`end_at` are both stored, implement a background job (triggered on sync or upload) that auto-links recordings to events whose time window overlaps within a configurable tolerance (e.g., ±15 min). Expose this as a "Smart Link" button in the calendar day view.

**4. Multi-speaker diarisation editing UI**
The transcript modal has a basic speaker-rename tool, but it only finds speakers by regex and does a text replacement. A proper editor would show each speaker turn as a labelled segment (like a chat UI), let the user merge or re-label speaker segments, and save the changes back as structured data (JSON array of `{speaker, start, end, text}` segments). This data would power better action-item attribution and smarter mind-map edges.

**5. Configurable retention and archival policies**
There is no mechanism to manage storage growth. Add a settings page (or `.env` variables) for rules like: "delete local audio files older than 90 days if transcript exists", "archive DB records with no local file after 30 days", and "purge completed+archived action items older than 1 year". The `delete_recording` endpoint already supports selective deletion; a scheduler (like the existing `DailyNotificationService`) could apply rules automatically.

---

## 5. Code Quality Improvements

**`_ensure_action_items_table` and `_ensure_calendar_tables` are ad-hoc migration scripts**
The project has no migration framework. Schema changes are applied via `ALTER TABLE` guards scattered across `SqliteDBRepository`. This makes it impossible to know what schema version a DB is at, and the `_ensure_action_items_table` method even drops and recreates the table when it detects an old schema (losing data if the INSERT fails). Use a proper migration table (e.g., `schema_version`) with numbered scripts, or adopt `alembic`.

**`get_config()` mutates a module-level dict and its result is always discarded**
(`src/app/depends.py`)
The entire `get_config` function and the `config = {}` global can be deleted. All values are read directly via `os.getenv()` in the individual `get_*` factory functions.

**`DashboardController` is a 700-line monolith**
It handles USB sync, upload, transcription, summarization, publishing, task generation, action-item extraction, and Team Manager sync. Split into `RecordingController`, `TranscriptionController`, `SummarizationController`, and keep `DashboardController` as a thin coordinator.

**`_parse_recording_datetime` is copy-pasted between `DashboardController` and `RAGController`**
(`src/controllers/DashboardController.py` and `src/controllers/RAGController.py`)
Extract to a shared `utils.py` module.

**`SystemPromptsRepository.get_all` silently drops non-English prompts**
The filter `if lang_dir.name != "en": continue` should either be removed (exposing all languages), made configurable, or at minimum logged. The Italian prompt files shipped in the repo are currently unreachable.

**No validation on `prompt_id`, `destination`, or `engine` parameters in API endpoints**
These are arbitrary strings. FastAPI `Literal` types or `Enum` fields in the DTOs would provide automatic 422 validation and Swagger documentation without extra code.

**`print()` statements used instead of `logger` in production code**
(`src/controllers/DashboardController.py`, `_extract_action_items_from_summary`, `_create_action_item_from_task`)
```python
print(f"Warning: Failed to create action item for task '{task.title}': {e}")
```
These should use `logging.warning()` so they respect log level configuration and appear in structured logs.

**No tests for service classes with external dependencies**
`TranscriptionService`, `SummarizationService`, `ClaudeSummarizationService`, `ObsidianService`, `ICalSyncService`, and `EmailService` have zero unit tests. At minimum, extract pure logic methods (JSON parsing, markdown conversion, date parsing) into testable static/module-level functions (the existing `_parse_response` tests on `SummarizationService` are a good model to replicate).

**`migrate_action_items.py` uses `import` inside a loop and bypasses the repository pattern**
```python
for summary_row in summary_rows:
    from models.DBSummary import DBSummary   # ← inside loop
    summary = DBSummary.from_dict(dict(summary_row))
```
Imports should be at the top. The script also opens a raw `sqlite3` connection with a hardcoded path alongside the repository, creating two parallel access patterns.

**`export_team_manager.py` embeds developer-personal data**
`ME = "Trent"`, the `ROLE` dictionary, specific names in `NONPERSON` (including `"Frank"` with a comment about a named interview candidate), and hardcoded vault/DB paths should all move to a config section or a separate JSON file that is gitignored.

**FastAPI dependency factories create a new service instance on every request**
(`src/app/depends.py`)
```python
def get_dashboard_controller() -> DashboardController:
    return DashboardController(
        sqlite_db_repository=get_sqlite_db_repository(),
        ...
    )
```
Each factory function is called fresh per request, creating new `SqliteDBRepository`, `TranscriptionService`, `VectorStoreRepository` (which opens a ChromaDB connection), etc. objects on every hit. Mark expensive singletons with `@lru_cache()` or use FastAPI's `Depends` caching for objects that should be shared across requests.

---

<details><summary>Files reviewed</summary>

- `.env.example`
- `README.md`
- `auto_commit.py`
- `export_team_manager.py`
- `migrate_action_items.py`
- `pyproject.toml`
- `pytest.ini`
- `requirements-dev.txt`
- `requirements.txt`
- `settings/db_init.sql`
- `src/__init__.py`
- `src/app/__init__.py`
- `src/app/api/__init__.py`
- `src/app/api/api.py`
- `src/app/api/endpoints/__init__.py`
- `src/app/api/endpoints/action_items.py`
- `src/app/api/endpoints/calendar.py`
- `src/app/api/endpoints/dashboard.py`
- `src/app/api/endpoints/knowledge.py`
- `src/app/api/endpoints/notifications.py`
- `src/app/api/endpoints/proactor.py`
- `src/app/depends.py`
- `src/app/router.py`
- `src/app/web/__init__.py`
- `src/app/web/dashboard.py`
- `src/app/web/knowledge.py`
- `src/controllers/ActionItemController.py`
- `src/controllers/CalendarController.py`
- `src/controllers/DashboardController.py`
- `src/controllers/ProactorController.py`
- `src/controllers/RAGController.py`
- `src/controllers/__init__.py`
- `src/main.py`
- `src/models/DBActionItem.py`
- `src/models/DBCalendarEvent.py`
- `src/models/DBDailyRecap.py`
- `src/models/DBRecording.py`
- `src/models/DBSharedCalendar.py`
- `src/models/DBSummary.py`
- `src/models/DBTask.py`
- `src/models/__init__.py`
- `src/models/dto/CreateActionItemDTO.py`
- `src/models/dto/CreateCalendarEventDTO.py`
- `src/models/dto/CreateManualActionItemDTO.py`
- `src/models/dto/DeleteRecordingRequestDTO.py`
- `src/models/dto/GenerateTasksRequestDTO.py`
- `src/models/dto/LinkRecordingEventDTO.py`
- `src/models/dto/MindMapRequestDTO.py`
- `src/models/dto/ProactorAnalyzeRequestDTO.py`
- `src/models/dto/PublishRequestDTO.py`
- `src/models/dto/RAGQueryRequestDTO.py`
- `src/models/dto/SharedCalendarDTO.py`
- `src/models/dto/SummarizeRequestDTO.py`
- `src/models/dto/TranscribeRequestDTO.py`
- `src/models/dto/UpdateActionItemDTO.py`
- `src/models/dto/UpdateCalendarEventDTO.py`
- `src/models/dto/UpdateRecordingRequestDTO.py`
- `src/models/dto/UpdateSummaryRequestDTO.py`
- `src/models/dto/UpdateTaskRequestDTO.py`
- `src/models/dto/UpdateTranscriptRequestDTO.py`
- `src/models/dto/__init__.py`
- `src/repositories/LocalRecordingsRepository.py`
- `src/repositories/SqliteDBRepository.py`
- `src/repositories/SystemPromptsRepository.py`
- `src/repositories/VectorStoreRepository.py`
- `src/repositories/__init__.py`
- `src/services/ClaudeSummarizationService.py`
- `src/services/ClaudeTaskGenerationService.py`
- `src/services/DailyNotificationService.py`
- `src/services/DailyRecapService.py`
- `src/services/EmailService.py`
- `src/services/ICalSyncService.py`
- `src/services/NotionService.py`
- `src/services/ObsidianService.py`
- `src/services/ProactorService.py`
- `src/services/RAGService.py`
- `src/services/SummarizationService.py`
- `src/services/TaskGenerationService.py`
- `src/services/TranscriptionService.py`
- `src/services/WhisperTranscriptionService.py`
- `src/services/__init__.py`
- `src/static/action_items.js`
- `src/static/app.js`
- `src/static/calendar.css`
- `src/static/calendar.js`
- `src/static/dashboard.css`
- `src/static/dashboard.js`
- `src/static/hidock-device.js`
- `src/static/knowledge.css`
- `src/static/knowledge.js`
- `src/static/proactor.css`
- `src/static/proactor.js`
- `src/templates/dashboard/action_items.html`
- `src/templates/dashboard/calendar.html`
- `src/templates/dashboard/home.html`
- `src/templates/dashboard/main_template.html`
- `src/templates/dashboard/proactor.html`
- `src/templates/knowledge/home.html`
- `start_autocommit.sh`
- `start_daily_notifications.sh`
- `status_autocommit.sh`
- `status_daily_notifications.sh`
- `stop_autocommit.sh`
- `stop_daily_notifications.sh`
- `system_prompts/en/General/AdaptiveSummary.txt`
- `system_prompts/en/General/CleanTranscript.txt`
- `system_prompts/en/General/ClearSummary.txt`
- `system_prompts/en/General/CompleteTranscript.txt`
- `system_prompts/en/General/DecisionsAndRisks.txt`
- `system_prompts/en/General/DefaultSummary.txt`
- `system_prompts/en/General/ExecutiveQuality.txt`
- `system_prompts/en/General/ExecutiveSummary.txt`
- `system_prompts/en/General/ExecutiveTLDR.txt`
- `system_prompts/en/General/JobInterview.txt`
- `system_prompts/en/General/QAMinutes.txt`
- `system_prompts/en/General/ReasoningSummary.txt`
- `system_prompts/en/Meeting/ActionTracker.txt`
- `system_prompts/en/Meeting/AdvancedStrategicMinutes.txt`
- `system_prompts/en/Meeting/ClientRecap.txt`
- `system_prompts/en/Meeting/DetailedSummary.txt`
- `system_prompts/en/Meeting/ITMinutes.txt`
- `system_prompts/en/Meeting/LightPostMortem.txt`
- `system_prompts/en/Meeting/MeetingNote.txt`
- `system_prompts/en/Meeting/MeetingNotes.txt`
- `system_prompts/en/Meeting/OperationalSummary.txt`
- `system_prompts/en/Meeting/RequirementsMinutes.txt`
- `system_prompts/en/Meeting/Secretary.txt`
- `system_prompts/en/Meeting/TeamMinutes.txt`
- `system_prompts/it/Generale/ColloquioLavoro.txt`
- `system_prompts/it/Generale/DecisioniERischi.txt`
- `system_prompts/it/Generale/RiepilogoRagionamento.txt`
- `system_prompts/it/Generale/Riunione.txt`
- `system_prompts/it/Generale/SintesiAdattiva.txt`
- `system_prompts/it/Generale/SintesiChiara.txt`
- `system_prompts/it/Generale/TLDRDirigenziale.txt`
- `system_prompts/it/Generale/TrascrizioneCompleta.txt`
- `system_prompts/it/Generale/VerbaleQ&A.txt`
- `system_prompts/it/IT&Engineering/PostMortemLeggero.txt`
- `system_prompts/it/IT&Engineering/VerbaleIT.txt`
- `system_prompts/it/IT&Engineering/VerbaleRequisiti.txt`
- `system_prompts/it/IT&Engineering/VerbaleTeam.txt`
- `system_prompts/it/Riunione/ActionTracker.txt`
- `system_prompts/it/Riunione/Nota.txt`
- `system_prompts/it/Riunione/RecapCliente.txt`
- `system_prompts/it/Riunione/RiepilogoDettagliato.txt`
- `system_prompts/it/Riunione/Segretario.txt`
- `system_prompts/it/Riunione/SintesiOperativa.txt`
- `system_prompts/it/Riunione/VerbaleStrategicoAvanzato.txt`
- `tests/integration/repositories/test_integration_LocalRecordingsRepository.py`
- `tests/integration/repositories/test_integration_SqliteDBRepository.py`
- `tests/unit/controllers/test_DashboardController.py`
- `tests/unit/models/test_DBRecording.py`
- `tests/unit/repositories/test_LocalRecordingsRepository.py`
- `tests/unit/repositories/test_SqliteDBRepository.py`
- `tests/unit/repositories/test_SystemPromptsRepository.py`
- `tests/unit/services/test_NotionService.py`
- `tests/unit/services/test_ProactorService.py`
- `tests/unit/services/test_SummarizationService.py`

</details>

*Generated automatically by weekly_code_review.py*
