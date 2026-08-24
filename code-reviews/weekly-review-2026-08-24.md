# Weekly Code Review — Agendino

**Date:** 2026-08-24  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical: Hardcoded magic `summary_id = 75` in `ActionItemController.create_manual_action_item`
**File:** `src/controllers/ActionItemController.py`, ~line 88  
```python
summary_id = 75  # Use a known valid summary_id
```
This is a developer artifact that will cause a foreign-key constraint violation in any environment where summary row 75 does not exist. The comment even acknowledges it by saying "We know summary IDs 73, 74, 75 exist from our earlier check." The method must query for an actual latest summary_id dynamically, or `summary_id` must be made nullable (the schema already allows NULL there).

### Critical: `NotionService.publish_summary` called with `tasks` kwarg it does not accept
**File:** `src/controllers/DashboardController.py`, `publish_summary` method  
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,   # ← not in NotionService signature
)
```
`NotionService.publish_summary` (`src/services/NotionService.py`) accepts only `(title, summary_markdown, tags, recording_name)`. Passing `tasks` raises `TypeError: publish_summary() got an unexpected keyword argument 'tasks'`, which is caught by the generic `except Exception` block and returned as `{"ok": False, "error": "Publish failed: ..."}`. Notion publishing is effectively broken.

### High: Hardcoded absolute path `/opt/agendino/…` in production controller code
**File:** `src/controllers/DashboardController.py`, `_sync_team_manager`  
```python
exporter = "/opt/agendino/export_team_manager.py"
...
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
The `open()` call will raise `FileNotFoundError` if the path does not exist. Although it is wrapped in `try/except`, the `subprocess.Popen` referencing `logf` cannot be reached, so the sync silently never runs outside the `/opt/agendino` deployment. Development installs and CI environments are permanently broken for this feature. The path should come from an environment variable or be derived from `__file__`.

### High: Timezone information silently discarded in iCal sync
**File:** `src/services/ICalSyncService.py`, `_to_datetime_str`  
```python
if isinstance(dt_val, datetime):
    # Strip timezone info for naive storage
    return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
A UTC+5 event at 9 AM will be stored as `09:00:00` instead of `04:00:00 UTC`. All time-comparison logic (overlap detection, day queries) will produce wrong results for non-UTC calendars.

### High: File saved to disk before DB insert; no rollback on insert failure
**File:** `src/controllers/DashboardController.py`, `upload_recording`  
```python
self._local_recordings_repository.save(filename, file_data)
# ...
new_id = self._sqlite_db_repository.insert_recording(db_rec)
```
If `insert_recording` raises (e.g., unique constraint, disk full on DB file), the local file is already written but has no database record. Subsequent uploads of the same file are then blocked by the exists-check on the local file.

### Medium: `_ensure_action_items_table` uses fragile string match for schema migration
**File:** `src/repositories/SqliteDBRepository.py`  
```python
if existing_schema and 'task_id INTEGER NOT NULL' in existing_schema[0]:
```
The schema string from `sqlite_master` may differ in whitespace or casing across SQLite versions. If it does not match, the old schema is silently kept, and `NULL` task_id values will violate the `NOT NULL` constraint.

### Medium: Bare `except` swallows all exceptions in date parsing
**File:** `src/controllers/DashboardController.py`, `_extract_action_items_from_summary`  
```python
except:
    pass
```
A bare `except` catches `KeyboardInterrupt`, `SystemExit`, and generator exceptions in addition to regular errors, and hides bugs entirely.

### Medium: `export_team_manager.py` contains hardcoded personal/organisational data
**File:** `export_team_manager.py`, `ROLE` dict, `NONPERSON` set  
Names, job titles, and org-chart relationships are hardcoded in source. Any real change to the team requires editing and redeploying code, and the file exposes personnel data to anyone with repository access.

---

## 2. Performance Improvements

### N+1 query on every dashboard load (`get_recordings_status`)
**File:** `src/controllers/DashboardController.py`  
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
This fires one `get_summaries` query per recording. For 200 recordings, that is 200 extra round-trips to SQLite. A single `SELECT recording_id, COUNT(*) … GROUP BY recording_id` would cover all recordings at once, similarly to how `get_latest_summaries_map` is already used.

### `_ensure_calendar_tables` and `_ensure_action_items_table` called on every request
**File:** `src/repositories/SqliteDBRepository.py`  
Both methods execute a `SELECT sql FROM sqlite_master` probe query before every single calendar or action-item operation. This should be done once at startup (or tracked with a boolean flag set after the first successful check).

### `WhisperTranscriptionService` model is not shared across requests
**File:** `src/app/depends.py`, `get_whisper_transcription_service`  
```python
def get_whisper_transcription_service() -> WhisperTranscriptionService:
    return WhisperTranscriptionService(...)
```
A new instance is created per request. The model itself is lazy-loaded on the instance, so the ~500 MB Whisper model is reloaded for each transcription request. The service should be a singleton (e.g., `@lru_cache()` on the factory function or a module-level singleton).

### `_reconcile_orphan_files` runs on every dashboard GET
**File:** `src/controllers/DashboardController.py`, `get_recordings_status`  
The reconciliation scans all local files and all DB records on every page load. With a large library this becomes expensive. It should either run on a background scheduler or be triggered only on sync operations.

### `load_transcripts` in RAGController truncates transcript to ~5 KB per document
**File:** `src/controllers/RAGController.py`  
```python
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```
Only roughly 5 KB of each transcript is embedded; the rest is discarded. Proper chunking with overlapping windows and multiple embeddings per transcript would give dramatically better retrieval.

### SQLite connection opened and closed per atomic operation
**File:** `src/repositories/SqliteDBRepository.py` (throughout)  
Every method creates its own connection. Complex workflows like summarisation (transcript read → summary write → action-item writes → label update) open and close four separate connections. A unit-of-work or context-manager pattern would reduce overhead.

---

## 3. Missing Error Handling

### Gemini file upload leaked when `generate_content` fails
**File:** `src/services/TranscriptionService.py`, `transcribe`  
```python
uploaded = self._client.files.upload(...)
response = self._client.models.generate_content(...)
return response.text
```
If the generation call raises, the uploaded file is never deleted. Over time, with repeated failures, orphaned files accumulate in Gemini storage. A `try/finally` block should call `self._client.files.delete(uploaded.name)`.

### `CalendarController.create_shared_calendar` silently drops initial sync errors
**File:** `src/controllers/CalendarController.py`  
```python
try:
    self._do_sync_calendar(saved)
except Exception:
    pass  # Don't fail creation if initial sync fails
```
No logging, no error surfaced to the caller. The user has no way to know the calendar was created but not synced. At minimum the error should be logged.

### `recording` table has no UNIQUE constraint on `name`; duplicate inserts are silently accepted
**File:** `settings/db_init.sql`  
```sql
CREATE TABLE IF NOT EXISTS recording (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT    NOT NULL,
    ...
);
```
No `UNIQUE(name)` constraint exists. The upload endpoint checks for duplicates in application code, but concurrent uploads of the same filename can race and produce two rows, breaking all name-based lookups.

### `ProactorController.analyze_date_range` validates format but not range magnitude
**File:** `src/controllers/ProactorController.py`  
A request with `start=2000-01-01&end=2030-12-31` would attempt to load and process 30 years of calendar events. There is no maximum span guard.

### `SummarizationService.summarize` does not handle `response.text is None` safely
**File:** `src/services/SummarizationService.py`  
```python
raw = response.text or ""
```
This is handled, but `response.candidates[0].finish_reason` access immediately before it is not guarded against an empty `candidates` list beyond an `(IndexError, AttributeError)` catch; a `KeyError` on the finish-reason enum lookup would escape.

### `DailyNotificationService` scheduler has no error recovery loop
**File:** `src/services/DailyNotificationService.py`, `start_scheduler`  
A transient exception inside `await asyncio.sleep(sleep_seconds)` (e.g., from clock drift) would bubble up through the `while self.running` loop and be caught by the outer `except Exception`, permanently stopping the scheduler. The inner loop should catch and log non-fatal exceptions and continue.

### `ObsidianService` auto-commit script exit code not checked
**File:** `src/services/ObsidianService.py`  
```python
result = subprocess.run(['/bin/bash', self.auto_commit_script], check=False)
```
A non-zero exit code is logged but the method still returns `{"ok": True, ...}`. The caller has no indication the commit failed.

---

## 4. New Feature Ideas

### 1. Real-time transcription/summarisation status via SSE or WebSocket
Long Gemini transcription calls (potentially several minutes for a 2-hour recording) leave the user with no feedback. Server-Sent Events on `/api/dashboard/transcribe/{name}/stream` could emit progress updates (`uploading`, `transcribing`, `done`) without blocking or polling.

### 2. Dashboard search and filter
The recordings table is rendered client-side with no server-side filtering. Adding query parameters (e.g., `?q=team+meeting&date_from=2026-01&has_summary=true`) to `GET /api/dashboard/recordings` and a search bar in the UI would greatly improve usability as the library grows past a few dozen recordings.

### 3. Scheduled background sync from HiDock device
Currently sync is entirely manual. A configurable background scheduler (similar to the existing `DailyNotificationService`) could auto-detect a paired WebUSB device—or poll via a local USB watcher—and download new recordings automatically, optionally triggering transcription.

### 4. Multi-user / workspace support with per-user isolation
The application currently stores everything in a single SQLite database with no concept of ownership. Adding a `workspace_id` or `user_id` to `recording`, `summary`, and `action_items` tables would allow multiple users or projects to share one deployment without data leakage.

### 5. Summary quality / completeness indicator
When Gemini returns a truncated response (`finish_reason == MAX_TOKENS`), the current code appends a ⚠️ warning inline in the markdown. A more systematic quality signal—stored as a `quality_flags` column on the `summary` table and displayed as a badge in the dashboard—would let users quickly identify summaries that need manual review or re-generation with Whisper.

---

## 5. Code Quality Improvements

### `SqliteDBRepository` is a God Object (~1 100 lines)
**File:** `src/repositories/SqliteDBRepository.py`  
A single class handles recordings, summaries, tasks, calendar events, shared calendars, daily recaps, and action items. Each feature group should be its own repository class (e.g., `RecordingRepository`, `SummaryRepository`, `CalendarRepository`), injected where needed. The current design means every test that touches any data model must instantiate the entire repository.

### `_parse_recording_datetime` duplicated across controllers
**File:** `src/controllers/DashboardController.py` and `src/controllers/RAGController.py`  
Identical static methods exist in both classes. Should live in a shared utility module, e.g., `src/utils/filename.py`.

### `depends.py` creates a fresh service instance on every request with no lifetime management
**File:** `src/app/depends.py`  
`get_dashboard_controller()`, `get_rag_controller()`, etc. instantiate all dependencies anew per request. For stateless services this is wasteful; for `WhisperTranscriptionService` it breaks model caching entirely. FastAPI's `@lru_cache` or a `lifespan` context with app-level singletons should be used.

### `import` statements inside a loop in `migrate_action_items.py`
**File:** `migrate_action_items.py`  
```python
for summary_row in summary_rows:
    from models.DBSummary import DBSummary
```
Imports belong at the top of the file. Python caches the import after the first call, so this works, but it is misleading and violates PEP 8.

### CDN resources loaded without Subresource Integrity hashes
**Files:** `src/templates/dashboard/home.html`, `calendar.html`, `action_items.html`, `knowledge/home.html`  
All Bootstrap, Bootstrap Icons, vis-network, and marked.js are loaded from CDNs without `integrity=` attributes. A compromised CDN could inject arbitrary JavaScript. Either add SRI hashes or vendor the assets locally.

### Inconsistent error-return convention (dict vs. HTTP exception)
**Files:** Throughout `src/app/api/endpoints/` and controllers  
Some endpoints raise `HTTPException` on failure; others return `{"ok": False, "error": "..."}` with HTTP 200. The mix makes it hard to write reliable API clients and tests. Controllers should raise typed domain exceptions; the API layer should catch them and raise `HTTPException` with the appropriate status code.

### Test coverage gaps
**Files:** `tests/`  
`ActionItemController`, `CalendarController`, `RAGController`, `EmailService`, `ObsidianService`, `ClaudeSummarizationService`, `ClaudeTaskGenerationService`, and `ICalSyncService` have no unit tests at all. The integration tests depend on a live database path (`../../../settings/agendino.db`) that may not exist in CI. `pytest-cov` is listed as a dev dependency but there is no CI configuration enforcing a minimum coverage threshold.

### `_extract_action_items_from_summary` violates single-responsibility principle
**File:** `src/controllers/DashboardController.py`  
This ~90-line method does Markdown parsing, table-column detection, date parsing in multiple formats, status normalisation, priority normalisation, and database writes. Each concern should be its own function or class, making it independently testable.

### `export_team_manager.py` `NONPERSON`, `ALIAS`, and `ROLE` dicts belong in configuration, not source
**File:** `export_team_manager.py`  
Hardcoded sets of personal and organisational data (`ROLE`, `ALIAS`, `NONPERSON`, `GROUP_SUBSTR`) must be edited in source every time the team changes. These should live in a YAML/TOML configuration file read at runtime.

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
