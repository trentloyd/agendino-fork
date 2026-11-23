# Weekly Code Review — Agendino

**Date:** 2026-11-23  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**`ActionItemController.create_manual_action_item` hardcodes a database row ID**
`src/controllers/ActionItemController.py` lines approx. 85-87:
```python
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```
This hardcoded value is specific to the developer's local database. On any other installation this will raise an SQLite foreign-key constraint violation every time a manual action item is created without a `recording_id`.

**`DashboardController.publish_summary` passes `tasks` to `NotionService`**
`src/controllers/DashboardController.py`:
```python
result = svc.publish_summary(
    title=publish_title,
    ...
    tasks=tasks_list,  # *** Pass tasks to Obsidian ***
)
```
`NotionService.publish_summary` (`src/services/NotionService.py`) does not accept a `tasks` keyword argument. Every Notion publish raises a `TypeError`, which is caught and silently converted to `{"ok": False, "error": "Publish failed: ..."}`. Notion publishing is completely broken.

**`_sync_team_manager` and `export_team_manager.py` hardcode `/opt/agendino/`**
`src/controllers/DashboardController.py`:
```python
exporter = "/opt/agendino/export_team_manager.py"
...
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
`export_team_manager.py` also has `DB = "/opt/agendino/settings/agendino.db"` and `VAULT = "/home/git/obsidian-working"`. These silently no-op or crash on any installation not at that exact path. The exporter's presence check (`if not os.path.exists(exporter): return`) masks the misconfiguration entirely.

**Path traversal in `SystemPromptsRepository.get_prompt_content`**
`src/repositories/SystemPromptsRepository.py`:
```python
prompt_path = self._prompts_dir / f"{prompt_id}.txt"
if not prompt_path.exists():
    return None
return prompt_path.read_text(encoding="utf-8")
```
`prompt_id` comes from an API POST body (`SummarizeRequestDTO.prompt_id: str`). A value like `../../src/app/depends` would read arbitrary files from the filesystem. No sanitization is applied before constructing the path.

### High

**`WhisperTranscriptionService` model is reloaded on every request**
`src/app/depends.py`:
```python
def get_whisper_transcription_service() -> WhisperTranscriptionService:
    return WhisperTranscriptionService(...)
```
A new instance is created per request. The model is `None` on each new instance, so the multi-hundred-MB model is re-downloaded or re-loaded from disk on each whisper transcription.

**Race condition in `_ensure_action_items_table`**
`src/repositories/SqliteDBRepository.py`: The pattern of checking `sqlite_master`, conditionally dropping and recreating the `action_items` table, is not wrapped in any locking. Two concurrent requests could both see the old schema and both attempt the migration, causing "table already exists" errors or data loss.

**`upload_recording` saves file before DB insert**
`src/controllers/DashboardController.py`: `self._local_recordings_repository.save(filename, file_data)` is called before `self._sqlite_db_repository.insert_recording(db_rec)`. If the DB insert fails (e.g., duplicate name race), the file remains as an orphan on disk with no corresponding record and no cleanup.

**Hardcoded record IDs in `export_team_manager.py` preview mode**
```python
for rid in (235, 227, 230):  # Alex, Heddy, Ariel — full modern structure
```
These IDs are from the developer's production database. On a fresh installation, preview mode silently skips all three samples.

### Medium

**No validation on `TranscribeRequestDTO.engine`**: An unrecognized value silently falls through to Gemini with no error. Add a `Literal["gemini", "whisper"]` type.

**`_extract_action_items_from_summary` uses a bare `except:`**
```python
except:
    pass
```
Swallows `KeyboardInterrupt`, `SystemExit`, and all other base exceptions.

---

## 2. Performance Improvements

**N+1 query per recording in `get_recordings_status`**
`src/controllers/DashboardController.py`:
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
For every recording row this issues a full `SELECT … WHERE r.name = ?` query. With 100 recordings this is 100 extra round-trips. `get_latest_summaries_map()` already returns the latest summary map; a `GROUP BY recording_id COUNT(*)` query could supply the count in a single call.

**`get_recordings` hydrates summary fields with N per-row sub-queries**
`src/repositories/SqliteDBRepository.py`:
```python
def _hydrate_latest_summary_fields(conn, rec: DBRecording) -> None:
```
Called inside the loop in `get_recordings()`. Each call issues one `SELECT … ORDER BY version DESC LIMIT 1`. For N recordings this is N+1 database round-trips. The existing `get_latest_summaries_map()` already does this in one query and should be reused.

**`_ensure_calendar_tables()` and `_ensure_action_items_table()` called on every operation**
Both methods run `CREATE TABLE IF NOT EXISTS` DDL inside every CRUD method. This schema-check work should be done once at application startup (or in `__init__`), not before every read/write.

**`_reconcile_orphan_files()` on every dashboard load**
`src/controllers/DashboardController.py`: This method is called by `get_recordings_status()`, which is called on every dashboard page load. It scans the local filesystem and potentially renames files every request. It should be triggered only on sync or startup, not on reads.

**New service instances created per request**
`src/app/depends.py`: All `get_*` functions return freshly constructed objects. Services that hold expensive state (Gemini client, Anthropic client, ChromaDB connection, Jinja2 environment) are re-instantiated on every request. Annotate with `@lru_cache()` or use FastAPI's `Depends` with `use_cache=True` for stateless singletons.

**`load_summaries` / `load_transcripts` add to ChromaDB one document at a time**
`src/controllers/RAGController.py`: Each document is `upsert`-ed individually. ChromaDB's `upsert` accepts lists; batching these calls would be significantly faster for large collections.

---

## 3. Missing Error Handling

**No timeout on outbound SMTP connections**
`src/services/EmailService.py`:
```python
with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
    server.starttls()
```
No `timeout` argument. An unreachable SMTP host will block the async event loop thread indefinitely. Add `smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=15)`.

**`_initialize_db` does not close connection on exception**
`src/repositories/SqliteDBRepository.py`:
```python
conn = self._connect()
conn.executescript(sql)
conn.commit()
conn.close()   # never reached if executescript raises
```
If `executescript` or `commit` raises, the connection leaks. Wrap in `try/finally` or use a context manager.

**`ObsidianService.publish_summary` runs `auto_commit_script` without timeout**
`src/services/ObsidianService.py`:
```python
result = subprocess.run(['/bin/bash', self.auto_commit_script], ...)
```
No `timeout` parameter. A hanging git operation (e.g., waiting for SSH auth) blocks the HTTP response indefinitely.

**`DashboardController._create_action_item_from_task` uses `print` instead of `logging`**
```python
print(f"Warning: Failed to create action item for task '{task.title}': {e}")
```
Errors are silently dropped from structured logs. This applies to `_extract_action_items_from_summary` as well.

**`ClaudeSummarizationService` and `ClaudeTaskGenerationService` have no retry logic**
Both services propagate `anthropic.RateLimitError` and transient network errors directly. At minimum, rate-limit responses should trigger a retry with exponential back-off.

**`VectorStoreRepository.clear()` has no access control**
`src/app/api/endpoints/knowledge.py` exposes `POST /api/knowledge/clear` with no authentication. Any unauthenticated request permanently destroys all vector embeddings.

**Gemini file upload in `TranscriptionService.transcribe` has no cleanup on failure**
`src/services/TranscriptionService.py`: After `self._client.files.upload(...)`, if `generate_content` raises, the uploaded file remains in Gemini's file storage and is never deleted, consuming quota.

**`SqliteDBRepository._ensure_calendar_tables` uses `executescript` for DDL inside a method that also runs DML**
`executescript` issues an implicit `COMMIT` before executing, which can silently commit a partially-constructed transaction from a caller.

---

## 4. New Feature Ideas

**1. Webhook / outbound notification on key events**
Allow users to configure a URL that receives a POST whenever a transcription or summarization completes. This would enable integration with Slack, Microsoft Teams, or n8n workflows without polling. The payload would mirror the existing JSON response from those endpoints.

**2. Recording search / full-text transcript search without vector store**
The current semantic search requires the vector store to be populated manually. Add a lightweight SQLite FTS5 (full-text search) index on the `transcript` column so users can keyword-search across all recordings instantly, without an Anthropic API call or Gemini embedding, and without a "Load Summaries" step.

**3. Per-recording speaker diarization editor UI**
The Whisper transcription produces time-stamped segments but no speaker labels. Add a simple UI panel (similar to the existing speaker-rename editor in `dashboard.js`) that shows transcript segments, lets the user assign speaker names, and persists those renames — making Whisper output as useful as Gemini diarized output.

**4. Scheduled / automatic re-summarization when prompt changes**
When a user edits a system prompt file, there is no way to regenerate all existing summaries against the new prompt without clicking each recording individually. Add a background job UI (or CLI command) that re-runs summarization for a specified prompt across a date range or tag filter.

**5. Basic authentication / access control**
The dashboard is currently completely open. Add optional HTTP Basic Auth or a single shared passphrase (configurable via `.env`) using FastAPI middleware. This is essential for any deployment accessible beyond `localhost`.

---

## 5. Code Quality Improvements

**`depends.py` creates new service instances on every request with no caching**
Every dependency function (`get_sqlite_db_repository`, `get_transcription_service`, etc.) constructs a new object. Services that hold API clients, DB handles, or Jinja2 environments should be cached. Apply `@functools.lru_cache()` to pure-configuration factories or refactor to FastAPI's application-lifespan pattern.

**`SqliteDBRepository` is 1,200+ lines and covers 6 unrelated domains**
Calendar events, shared calendars, recordings, summaries, tasks, and action items all live in one class. Split into focused repositories: `RecordingRepository`, `SummaryRepository`, `CalendarRepository`, `ActionItemRepository`. This is currently masked by the class being referenced everywhere, but the existing test (`test_integration_SqliteDBRepository.py`) only tests the recording domain.

**`DashboardController` violates single-responsibility**
`src/controllers/DashboardController.py` handles HTTP glue, file I/O, transcription, summarization, publishing, task creation, action item creation, and team-manager sync. The `_extract_action_items_from_summary` method alone is 80+ lines of regex parsing that belongs in a dedicated service.

**`get_config()` in `depends.py` is defined but unused**
```python
config = {}
def get_config():
    items = os.environ.items()
    for item in items:
        config[item[0]] = item[1]
    return config
```
`config` is populated but never read back; every downstream call uses `os.getenv(...)` directly. Remove or complete the abstraction.

**Duplicated sidebar HTML across all five templates**
The sidebar nav (`home.html`, `calendar.html`, `action_items.html`, `proactor.html`, `knowledge/home.html`) is copy-pasted verbatim. Use a Jinja2 `{% include %}` or `{% extends %}` base template. Currently, adding a new nav link requires editing five files.

**`src/templates/dashboard/main_template.html` is empty**
The file exists but contains no content, suggesting an abandoned refactoring.

**`print()` used for operational logging in controllers and services**
`DashboardController._create_action_item_from_task`, `_extract_action_items_from_summary`, and `SqliteDBRepository._ensure_action_items_table` all use `print()`. These should use the `logging` module so output is controlled by log level and routed consistently with the rest of the application.

**Test coverage is concentrated only on the happy path**
`tests/unit/controllers/test_DashboardController.py` and the repository tests are reasonable, but there are no tests for `ActionItemController`, `CalendarController`, `RAGController`, or any service class (`EmailService`, `SummarizationService` integration, `ObsidianService`, etc.). The route-level conflict between `GET /action-items/{action_item_id}` and `GET /action-items/status/{status}` has no test.

**`UpdateCalendarEventDTO` uses `v is not None` to filter optional fields but can't clear values**
`src/app/api/endpoints/calendar.py`:
```python
kwargs = {k: v for k, v in body.model_dump().items() if v is not None}
```
If a user wants to clear the `location` field (set it to `null`), they cannot — the `None` value is filtered out before reaching the repository. Pydantic's `model_dump(exclude_unset=True)` is the correct pattern here.

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
