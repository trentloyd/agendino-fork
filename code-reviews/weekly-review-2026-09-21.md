# Weekly Code Review — Agendino

**Date:** 2026-09-21  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**Hardcoded `summary_id = 75` in `ActionItemController.create_manual_action_item`**
(`src/controllers/ActionItemController.py`, ~line 95)
```python
summary_id = 75  # Use a known valid summary_id
```
The comment reads "We know summary IDs 73, 74, 75 exist from our earlier check" — these are the developer's personal database row IDs. On any fresh installation this foreign-key value won't exist and every call to `POST /action-items/manual` will raise a constraint violation. This needs to be replaced with a proper lookup (e.g., the most-recent summary for the chosen recording) or the schema FK constraint should be made nullable/removed for manual items.

**`NotionService.publish_summary` is called with an unknown `tasks` keyword argument**
(`src/controllers/DashboardController.py`, `publish_summary` method)
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,   # ← not a param of NotionService
)
```
`NotionService.publish_summary` has no `tasks` parameter, so every Notion publish attempt raises `TypeError: publish_summary() got an unexpected keyword argument 'tasks'`. The exception is swallowed and returned as `{"ok": False, "error": "Publish failed: …"}`, so the UI shows a cryptic failure without indicating the root cause. Notion publishing is therefore permanently broken.

### High

**Absolute deployment paths hard-wired into source code**

`src/controllers/DashboardController.py` (`_sync_team_manager`):
```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```

`export_team_manager.py` (top of file):
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
These paths are silently ignored on any non-`/opt/agendino` installation: the exporter is never launched and the log is never written. Both should derive from environment variables or from the same `settings/` root the rest of the app uses.

**`_ensure_action_items_table` schema migration is data-unsafe**
(`src/repositories/SqliteDBRepository.py`)
```python
conn.execute("INSERT INTO action_items_new SELECT * FROM action_items")
```
`SELECT *` relies on identical column order. If the old and new schemas differ in column count or position the copy silently corrupts (or outright fails). A proper column-explicit `INSERT INTO … (col1, …) SELECT col1, … FROM …` is required.

**N+1 query per recording in `get_recordings_status`**
(`src/controllers/DashboardController.py`)
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
`get_summaries` issues a full DB round-trip per row. `get_latest_summaries_map()` is already fetched earlier in the same method and should be used for the count. Additionally `_hydrate_latest_summary_fields` in `get_recordings` fires one extra query per recording; a single JOIN would eliminate this.

**`DBActionItem.task_id` type annotation is wrong**
(`src/models/DBActionItem.py`)
```python
def __init__(self, id: int | None, task_id: int, ...):
```
`task_id` is annotated as `int` but is legitimately `None` for manual and summary-extracted items. Passing `task_id=None` is silently accepted by Python but will mislead type-checkers and IDEs.

**ICalSyncService silently drops timezone information**
(`src/services/ICalSyncService.py`)
```python
return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
UTC or offset-aware datetimes are stored as if they were local time. A calendar in UTC+9 and one in UTC−5 will have all their events shifted by the wrong amount relative to each other and to locally-created events.

**`create_manual_action_item` assigns a random recording to orphan items**
(`src/controllers/ActionItemController.py`)
```python
recording_id = recordings[0].id  # Use the most recent recording
meeting_title = meeting_title or "Manual Action Item"
```
When no `recording_id` is provided, the newest recording in the database is used as the anchor. This means the action item appears under an unrelated meeting in the calendar and action-item views.

### Medium

**No file-size guard on the upload endpoint**
(`src/app/api/endpoints/dashboard.py`)
```python
file_data = await file.read()
```
The entire upload is buffered into memory before any validation. A large or malicious file exhausts server RAM with no recourse.

**DDL-on-every-call anti-pattern**
(`src/repositories/SqliteDBRepository.py`)
`_ensure_calendar_tables()`, `_ensure_action_items_table()`, and `_ensure_recording_columns()` are called before every relevant operation, each executing `SELECT sql FROM sqlite_master` and potentially running `ALTER TABLE` or `executescript`. These should run once at startup rather than on the hot path.

**`export_team_manager.py` contains hardcoded personal data**
Names, roles, aliases, and sample recording IDs (235, 227, 230) are hardcoded:
```python
ROLE = { "Anna": ("VP of Product", "manager"), ... "Frank", # interview candidate }
for rid in (235, 227, 230):  # Alex, Heddy, Ariel
```
None of this is useful to any other user. The script advertises itself as a reusable exporter but is effectively single-deployment.

## 2. Performance Improvements

**`get_recordings` makes N+1 queries (one per recording for summary hydration)**
`SqliteDBRepository.get_recordings` calls `_hydrate_latest_summary_fields(conn, rec)` in a Python loop. Replace with a single query using the same lateral-join pattern already used by `get_latest_summaries_map`.

**`_reconcile_orphan_files` runs on every dashboard page load**
(`src/controllers/DashboardController.py`)
The reconciliation walk scans every local file and every DB record on each call to `get_recordings_status`. This should be triggered at most once per server start, or exposed as an explicit admin action.

**`depends.py` re-instantiates every service and repository on every request**
```python
def get_dashboard_controller() -> DashboardController:
    return DashboardController(
        sqlite_db_repository=get_sqlite_db_repository(),
        ...
    )
```
Each FastAPI request creates a fresh `SqliteDBRepository` (which calls `_ensure_recording_columns`), a fresh `VectorStoreRepository` (which connects to ChromaDB), etc. Use FastAPI's `Depends` with `lru_cache` or module-level singletons for stateless services, and `Depends` with request-scoped instances for connection-holding ones.

**`RAGController.load_transcripts` silently truncates long transcripts**
```python
chunks = [transcript_text[i:i+4000] for i in range(0, len(transcript_text), 3000)]
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```
For a 30-minute recording at 150 WPM this discards ~90 % of the transcript before indexing it, severely degrading deep-search quality. Each chunk should be stored as a separate vector document.

**`VectorStoreRepository.search` calls `count()` before every query**
The controller calls `count()` and then `search()` calls `count()` again internally for `n_results=min(top_k, count)`. One call is sufficient.

**`ActionItemController.sync_meeting_titles` loads all recordings to find one by ID**
```python
recordings = self.db_repo.get_recordings()
recording = next((r for r in recordings if r.id == recording_id), None)
```
`SqliteDBRepository` already has `get_recording_by_id`; use it.

## 3. Missing Error Handling

**`TranscriptionService.transcribe` never deletes the uploaded Gemini file**
(`src/services/TranscriptionService.py`)
After `uploaded = self._client.files.upload(...)`, transcription runs but there is no `self._client.files.delete(uploaded.name)` in a `finally` block. Uploaded files accumulate in Gemini storage, consuming quota and potentially hitting limits.

**Unguarded `await file.read()` then no cleanup on upload failure**
If `save()` raises (disk full, permissions), the file data is already in memory and the DB insert that follows will not run, leaving no record of the partial upload and no signal to the caller beyond an unhandled 500.

**`_ensure_action_items_table` `ALTER TABLE RENAME` can fail silently mid-migration**
If the server crashes between `DROP TABLE action_items` and `ALTER TABLE action_items_new RENAME TO action_items`, the table is permanently lost. No transaction wraps the migration.

**`ClaudeTaskGenerationService.generate_tasks` ignores `{"tasks": [...]}` JSON responses**
(`src/services/ClaudeTaskGenerationService.py`)
```python
if isinstance(tasks, list):
    return tasks
else:
    logger.warning("Claude returned non-list JSON for tasks")
    return []
```
Unlike `TaskGenerationService._parse_response`, the Claude variant never checks for a `tasks` key in a dict response and silently returns an empty list.

**`DailyNotificationService.start_scheduler` has no retry on send failure**
If `_send_daily_notification` raises or the SMTP server is temporarily unavailable, the error is logged and the scheduler advances to the next day with no retry attempt.

**`auto_commit.py` treats push failures as transient**
A failed `git push` (e.g., due to a diverged remote branch) is logged and ignored; the next scheduled run will try `git add -A` again and re-attempt to push the accumulated commits, which will fail again indefinitely. There is no alert mechanism or maximum-retry guard.

**No timeout on `ClaudeSummarizationService` or `ClaudeTaskGenerationService` API calls**
Both use the Anthropic SDK without an explicit `timeout=` argument, so a hung API response blocks the request handler indefinitely.

**`get_config()` side-effect silently overwrites any previous config**
(`src/app/depends.py`)
```python
config = {}
def get_config():
    items = os.environ.items()
    for item in items:
        config[item[0]] = item[1]
    return config
```
`config` is a module-level dict. It is never actually read from — `get_dashboard_controller` calls `_config = get_config()` but never uses `_config`. The function is a dead side-effect.

## 4. New Feature Ideas

**1. Full-text and date-range recording search**
The dashboard renders all recordings in a single unsorted table with no search or filter UI. A server-side or client-side filter by title, date range, transcription/summarisation status, and tag would be immediately valuable given the volume of recordings the system is designed to handle.

**2. Bulk transcript/summary re-generation**
Users can generate summaries one at a time. A "batch summarize" workflow — select multiple recordings, pick a prompt, run all — would reduce repetitive clicking for backfill scenarios and is a natural complement to the existing batch action-item operations.

**3. Recording-to-calendar-event auto-linking**
When a recording is uploaded or synced, automatically match it to a calendar event that overlaps its `recorded_at` timestamp (within a configurable window). The iCal sync infrastructure is already in place; adding a matching pass after each sync would close the loop between calendar and recordings without manual linking.

**4. Per-summary diff/version comparison**
Summaries already have a `version` field and multiple versions can exist for the same recording. A side-by-side or diff view between two versions (e.g., a re-generated DefaultSummary vs. an edited one) would make the versioning feature visible and useful rather than just an internal audit trail.

**5. Configurable action-item status workflow and per-project boards**
Currently action items have a fixed three-state lifecycle and no grouping beyond meeting. Supporting user-defined projects/boards (a lightweight Kanban) and configurable transitions (e.g., `pending → in_review → approved → done`) would make the feature competitive with dedicated task managers without needing a full external integration.

## 5. Code Quality Improvements

**Mixed `print()` and `logging` usage throughout the codebase**
`_create_action_item_from_task`, `_extract_action_items_from_summary`, and `_ensure_action_items_table` all use `print()` for warnings and operational messages that should go through the `logging` module. In production (Uvicorn/Gunicorn) stdout is not captured by the application log handlers.

**`export_team_manager.py` and `migrate_action_items.py` bypass the repository layer**
Both scripts open raw `sqlite3` connections directly to `agendino.db`:
```python
# migrate_action_items.py
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "settings/agendino.db"))
```
This duplicates connection logic, skips the `PRAGMA foreign_keys = ON` setup the repository enforces, and means schema migrations in `SqliteDBRepository` are not reflected in these scripts.

**`get_config()` in `depends.py` is never consumed**
The returned dict is assigned to `_config` in `get_dashboard_controller` but never used. `get_config()` is called only for the side-effect of populating a global `config` dict that is itself also never read. Both should be removed.

**`publish_summary` and `publish_recording` share a `_DESTINATION_META` dict that only knows about Notion**
(`src/controllers/DashboardController.py`)
```python
_DESTINATION_META: dict[str, dict] = {
    "notion": {"label": "Notion", "icon": "bi-journal-bookmark"},
}
```
Obsidian is a configured publish destination but has no entry here, so its label and icon fall back to the generic `key.capitalize()`. Adding an Obsidian entry is trivial; not doing so suggests the feature is untested end-to-end.

**`escapeHtml` is re-implemented in every JS file**
`dashboard.js`, `calendar.js`, and `proactor.js` each define their own `escapeHtml` or `esc` function with the same logic. A shared `utils.js` module would eliminate the duplication.

**`_ensure_*` migration helpers in `SqliteDBRepository` duplicate logic present in `db_init.sql`**
The `db_init.sql` file creates all tables with `CREATE TABLE IF NOT EXISTS`, so the `_ensure_calendar_tables` method re-implements the same DDL in Python. One source of truth (the SQL init script, run at startup if the DB is new) is cleaner than scattered `executescript` calls per operation.

**`src/templates/dashboard/main_template.html` is an empty file**
(`src/templates/dashboard/main_template.html`)
This appears to be an abandoned scaffold. It should be removed or turned into the base template the other pages extend.

**No input validation on `TranscribeRequestDTO.engine`**
(`src/models/dto/TranscribeRequestDTO.py`)
`engine: str = "gemini"` accepts any string. An invalid engine name silently falls through to the Gemini branch rather than returning a 422. Using `Literal["gemini", "whisper"]` from `typing` would enforce this at the Pydantic layer.

**Test suite is structurally incomplete**
Unit tests cover `DashboardController`, `DBRecording`, `LocalRecordingsRepository`, `SqliteDBRepository`, `SystemPromptsRepository`, `NotionService`, `ProactorService`, and `SummarizationService`. Nothing tests `ActionItemController`, `CalendarController`, `RAGController`, `ClaudeSummarizationService`, `ClaudeTaskGenerationService`, `WhisperTranscriptionService`, `ObsidianService`, or the API endpoint layer. The happy-path integration tests do not cover error branches or concurrent access.

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
