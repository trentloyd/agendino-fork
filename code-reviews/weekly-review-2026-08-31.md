# Weekly Code Review — Agendino

**Date:** 2026-08-31  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**`tasks` keyword argument crash when publishing to Notion** (`src/controllers/DashboardController.py`, `publish_summary`):
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,   # ← passed to every destination
)
```
`NotionService.publish_summary` has no `tasks` parameter. Every Notion publish call raises `TypeError: publish_summary() got an unexpected keyword argument 'tasks'`. `ObsidianService` accepts it fine, so the bug only surfaces on Notion.

**Hardcoded `summary_id = 75` in production code** (`src/controllers/ActionItemController.py`, `create_manual_action_item`):
```python
summary_id = 75  # Use a known valid summary_id
```
This is a developer's personal DB row ID committed to production. On any other install this foreign-key reference will fail or silently attach items to the wrong summary.

**Hardcoded absolute paths in `_sync_team_manager`** (`src/controllers/DashboardController.py`):
```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
These paths are hard-coded to one specific Linux installation. The method silently returns `None` on any other host (`os.path.exists(exporter)` is false), but if the paths exist but point to wrong files the method could corrupt state. Use `__file__`-relative paths or a config value.

**`export_team_manager.py` hardcodes DB and vault paths** (top of file):
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
The script is useless on any machine other than the author's. No env-var fallback exists.

**N+1 connection issue in `_initialize_db`** (`src/repositories/SqliteDBRepository.py`):
```python
conn = self._connect()
conn.executescript(sql)
conn.commit()
conn.close()       # ← not in a finally block
```
If `executescript` raises, the connection is never closed, leaking the file handle until GC collects it. All other `_connect()` calls in the class correctly use `try/finally`.

**Race condition on transcription** (`src/controllers/DashboardController.py`, `transcribe_recording`): The check-then-transcribe-then-save sequence has no locking. Two concurrent HTTP requests for the same recording will both see no cached transcript, both call the (expensive, paid) AI API, and both write to the DB. With `fastapi dev` running in async mode this is reachable.

**Lazy Whisper model initialization is not thread-safe** (`src/services/WhisperTranscriptionService.py`):
```python
if self._model is None:
    ...
    self._model = WhisperModel(...)
```
Under concurrent load, multiple threads can pass the `None` check simultaneously and each load a multi-hundred-MB model, causing OOM. A threading lock or `functools.cached_property` is needed.

**`create_manual_action_item` silently re-parents items to an arbitrary recording** (`src/controllers/ActionItemController.py`):
```python
recordings = self.db_repo.get_recordings()
if recordings:
    recording_id = recordings[0].id  # Use the most recent recording
    meeting_title = meeting_title or "Manual Action Item"
```
When no `recording_id` is provided, the item is silently attached to the first (most recently inserted) recording in the DB. Users creating standalone action items will see them appear under an unrelated meeting.

### Moderate

**`get_config()` result is never consumed, but mutates module-level state** (`src/app/depends.py`):
```python
config = {}
def get_config():
    for item in os.environ.items():
        config[item[0]] = item[1]
    return config

def get_dashboard_controller() -> DashboardController:
    _config = get_config()   # _config is never read
    return DashboardController(...)
```
`get_config` populates a module-level dict on every request (not thread-safe) but the returned value is unused. The `config` dict is never read anywhere.

**`DBActionItem` constructor type annotation says `task_id: int` but the value is routinely `None`** (`src/models/DBActionItem.py`):
Manual and summary-extracted items pass `task_id=None`, violating the declared type. Python doesn't enforce this at runtime, but it misleads static analysis and IDEs.

**`ICalSyncService` silently strips timezone information** (`src/services/ICalSyncService.py`):
```python
return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
Events in UTC or non-local timezones are stored as naive datetimes in the server's local time (or UTC, depending on Python's behavior). Users in different timezones will see wrong event times.

**`sync_meeting_titles` loads all recordings to find one by ID** (`src/controllers/ActionItemController.py`):
```python
recordings = self.db_repo.get_recordings()
recording = next((r for r in recordings if r.id == recording_id), None)
```
There is no `get_recording_by_id` method on the repository; the workaround fetches every recording. This is also the silent data-quality risk: if `recording_id` is not found, `ValueError` is raised without a DB-level lookup confirming the ID truly doesn't exist.

---

## 2. Performance Improvements

**N+1 query in `get_recordings_status`** (`src/controllers/DashboardController.py`):
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
For each recording in the list, a separate `SELECT … WHERE r.name = ?` query is issued. With 100 recordings this is 100+ round-trips. Replace with a single `GROUP BY` count query analogous to `get_latest_summaries_map`.

**`_hydrate_latest_summary_fields` is called per-row inside `get_recordings`** (`src/repositories/SqliteDBRepository.py`):
```python
for rec in recordings:
    self._hydrate_latest_summary_fields(conn, rec)
```
Each call runs a separate `SELECT … WHERE recording_id = ?`. Fold this into the original query with a window function or the same lateral join used in `get_latest_summaries_map`.

**`_ensure_action_items_table` and `_ensure_calendar_tables` are called on every DB operation** (`src/repositories/SqliteDBRepository.py`): These run `SELECT sql FROM sqlite_master` (and potentially `ALTER TABLE`) on every single read or write. Use a boolean flag that is set once at construction time or at app startup.

**`get_recordings_for_day` loads every recording and filters in Python** (`src/repositories/SqliteDBRepository.py`):
```python
rows = conn.execute("SELECT … FROM recording").fetchall()
```
All rows are fetched and date-matched in a Python loop. Add a `WHERE` clause using SQLite's `substr(recorded_at, 1, 10) = ?` and the name-parsed date fallback.

**New service/repository instances are created per HTTP request** (`src/app/depends.py`): FastAPI dependency functions run on every request. `WhisperTranscriptionService` holds a lazy-loaded model; `VectorStoreRepository` opens a ChromaDB PersistentClient; `SqliteDBRepository` constructs paths. These should be module-level singletons (via `functools.lru_cache()` on the factory functions, or FastAPI's `Depends(…, use_cache=True)` / lifespan pattern).

**`_reconcile_orphan_files` runs unconditionally on every dashboard load** (`src/controllers/DashboardController.py`): The file-system scan and normalized-name matching runs every time `get_recordings_status` is called, even when no orphans exist. Gate it behind a lightweight "any local files not in DB?" check, or run it on demand / at startup only.

**Transcript chunking in `load_transcripts` discards most of the text** (`src/controllers/RAGController.py`):
```python
if len(transcript_text) > 5000:
    chunks = [...]
    transcript_text = chunks[0]
    if len(chunks) > 1:
        transcript_text += "..." + chunks[1][:1000]
```
Only the first ~5 KB of a long transcript is stored. Store each chunk as a separate vector with a `chunk_index` metadata field so the entire transcript is searchable.

---

## 3. Missing Error Handling

**Gemini-uploaded files are never deleted on transcription failure** (`src/services/TranscriptionService.py`):
```python
uploaded = self._client.files.upload(file=path, config=...)
response = self._client.models.generate_content(...)   # may raise
return response.text
```
If `generate_content` raises, the uploaded file persists on Google's servers indefinitely, consuming file-storage quota. Add a `try/finally` block that calls `self._client.files.delete(uploaded.name)`.

**`EmailService.__init__` can raise `ValueError` at startup** (`src/services/EmailService.py`):
```python
self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
```
A non-numeric `SMTP_PORT` in `.env` raises an uncaught `ValueError` at import time, crashing the entire server without an actionable message.

**`save_daily_recap` can return `None` after upsert** (`src/repositories/SqliteDBRepository.py`): The method calls `self.get_daily_recap(recap.date)` and returns the result, but if the upsert somehow fails silently (e.g., unique constraint edge case), `get_daily_recap` returns `None`. The caller does `saved.to_dict()` with no None-guard, causing `AttributeError`.

**Subprocess shell errors in `_sync_team_manager` are silently swallowed** (`src/controllers/DashboardController.py`):
```python
subprocess.Popen(["/bin/bash", "-c", cmd], stdout=logf, stderr=logf, start_new_session=True)
```
`Popen` is fire-and-forget; a non-zero exit code is never checked and the caller only logs the launch failure, not the subprocess's actual error.

**`VectorStoreRepository._embed` silently drops null embeddings** (`src/repositories/VectorStoreRepository.py`):
```python
return [e.values for e in embeddings if e.values is not None]
```
If the Gemini API returns a partial response or all-null embeddings, an empty list is returned. ChromaDB will then raise an opaque dimension-mismatch or empty-list error at the point of upsert, far removed from the actual failure.

**`DailyNotificationService._get_active_action_items` silently returns `[]` on any exception** (`src/services/DailyNotificationService.py`):
```python
except Exception as e:
    logger.error(...)
    return []
```
A DB connection failure causes the notification to be silently skipped with "No active action items" logged. Callers should distinguish "no items" from "error fetching items".

**`_extract_action_items_from_summary` uses a bare `except:`** (`src/controllers/DashboardController.py`):
```python
except:
    pass
```
This swallows `KeyboardInterrupt`, `SystemExit`, and generator-based exceptions, making the application hard to stop gracefully in some edge cases.

**`auto_commit.py` has no retry or pull-before-push logic**: If the remote has diverged, `git push origin master` fails with exit code 1. The script logs the error but does nothing to recover, meaning the next scheduled commit will fail for the same reason indefinitely.

---

## 4. New Feature Ideas

**Async transcription/summarization job queue**: Currently, transcribing a long recording blocks the HTTP response for potentially minutes. Add a simple job queue (e.g., using `asyncio.Queue` or a lightweight library like `arq`) that processes long-running AI tasks in the background and pushes status updates via Server-Sent Events or WebSockets. The dashboard already polls for status; this would make the UX non-blocking.

**Per-recording transcript diff / edit history**: Users can manually edit transcripts, but edits are destructive — there is no way to see what changed or revert. Store versioned transcript snapshots (similar to the existing `summary.version` pattern) so users can compare an AI transcript against their edited version or roll back mistakes.

**Automatic calendar event ↔ recording matching**: The calendar already has a manual "link recording" UI. Add an automatic matcher that, on each sync or recording upload, queries calendar events whose `start_at … end_at` interval overlaps the recording's `recorded_at` + `duration`. Present candidates to the user as suggested links, reducing the manual step.

**Configurable roster for Team Manager via a YAML/JSON file**: The `ROLE`, `ALIAS`, and `NONPERSON` dictionaries in `export_team_manager.py` contain real names and role descriptions hardcoded in the Python source. Extract them into a `team_manager.yaml` config file that users edit without modifying source code, making the feature genuinely reusable and keeping PII out of the repository.

**Summary quality feedback loop**: Add a thumbs-up/thumbs-down rating on each summary version, stored in the DB. Use the feedback to surface consistently low-rated prompts in the UI so users know which system prompts produce poor results for their recording style, and optionally export the feedback as few-shot examples for future prompt refinement.

---

## 5. Code Quality Improvements

**`SqliteDBRepository` is a God class of ~1 200 lines** (`src/repositories/SqliteDBRepository.py`): It manages eight distinct domain entities (recordings, summaries, tasks, calendar events, daily recaps, shared calendars, action items, and recording-event links). Split into `RecordingRepository`, `SummaryRepository`, `CalendarRepository`, etc., each injected where needed. This also makes unit testing individual methods practical.

**`_ensure_action_items_table` inspects schema and potentially runs DDL on every call** (`src/repositories/SqliteDBRepository.py`): The pattern is also repeated for `_ensure_recording_columns` and `_ensure_calendar_tables`. Replace all three with a single versioned migration runner (even a simple list of idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` statements executed once at startup).

**All dependency factory functions create new instances per request** (`src/app/depends.py`): None of the `get_*` functions are cached. Annotate long-lived, stateful services with `@lru_cache(maxsize=1)` or use FastAPI's lifespan `app.state`:
```python
@lru_cache(maxsize=1)
def get_whisper_transcription_service() -> WhisperTranscriptionService:
    ...
```

**`get_config()` is dead code with a side-effect** (`src/app/depends.py`): The function populates a module-level `config` dict that is never read; the dict itself is never passed to anything. The function should either be deleted or replaced with direct `os.getenv()` calls at the point of use.

**`DBActionItem.__init__` declares `task_id: int` but accepts `None`** (`src/models/DBActionItem.py`): The type hint is wrong and contradicts `from_dict`, the DB schema (`task_id INTEGER` nullable), and every caller that passes `task_id=None`. Change to `task_id: int | None`.

**Hardcoded personal data in a committed source file** (`export_team_manager.py`): The `ROLE` dict contains real employee names, titles, and reporting relationships. The `NONPERSON` set contains team/project names specific to one company. This data should live in a config file outside version control, and `export_team_manager.py` should read it at runtime.

**`sync_meeting_titles` in `ActionItemController` lacks `get_recording_by_id`** (`src/controllers/ActionItemController.py` + `src/repositories/SqliteDBRepository.py`): The controller loads all recordings to find one by ID. Add `get_recording_by_id(self, recording_id: int) -> DBRecording | None` to the repository (a trivial `SELECT … WHERE id = ?`) and remove the linear scan.

**`main_template.html` is empty** (`src/templates/dashboard/main_template.html`): An empty template file is committed. Either populate it or delete it to reduce confusion.

**`publish_summary` / `publish_recording` legacy vs. current split is untested and undocumented** (`src/controllers/DashboardController.py`): Two methods exist (`publish_recording` wraps `publish_summary`) but there are no tests for either. The interface contract for `publish_services` (what kwargs each service's `publish_summary` accepts) is implicit, leading directly to the Notion `tasks` bug in section 1. Define a `PublishService` Protocol or ABC with a typed signature.

**Test coverage gaps**: The test suite has no tests for `ActionItemController`, `CalendarController`, `RAGController`, `SqliteDBRepository` action-item or calendar methods, `ClaudeSummarizationService`, `WhisperTranscriptionService`, or any of the API endpoint routing. `tests/integration/repositories/test_integration_LocalRecordingsRepository.py` contains a single `test_it_can_get_all` that only calls `print(result)` with no assertion.

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
