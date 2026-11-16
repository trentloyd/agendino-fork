# Weekly Code Review — Agendino

**Date:** 2026-11-16  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical: `NotionService.publish_summary` called with unsupported `tasks=` keyword argument
**File:** `src/controllers/DashboardController.py` → `publish_summary()`

```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,  # *** Pass tasks to Obsidian ***
)
```

`NotionService.publish_summary` (`src/services/NotionService.py`) has no `tasks` parameter. Calling it this way raises `TypeError: got an unexpected keyword argument 'tasks'` at runtime. The surrounding `except Exception` catches it and returns `{"ok": False, "error": "Publish failed: ..."}`, meaning **Notion publishing is silently broken for every user**. The comment says "Pass tasks to Obsidian" but the same code path is taken for Notion.

### Critical: Hardcoded `summary_id = 75` in `create_manual_action_item`
**File:** `src/controllers/ActionItemController.py`

```python
# Get a valid summary_id (needed due to foreign key constraints)
# For manual items, just use the most recent summary from any recording
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```

This row ID is from the developer's personal database. On any other installation this will either violate the foreign key constraint (failing the INSERT) or silently associate the item with a wrong summary. The correct fix is to query for the most recent summary ID dynamically.

### High: Path traversal in `SystemPromptsRepository.get_prompt_content`
**File:** `src/repositories/SystemPromptsRepository.py`

```python
def get_prompt_content(self, prompt_id: str) -> str | None:
    prompt_path = self._prompts_dir / f"{prompt_id}.txt"
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8")
```

`prompt_id` arrives from the HTTP request body (`SummarizeRequestDTO.prompt_id`) with no sanitization. `pathlib` resolves `..` segments, so a client supplying `prompt_id = "../../src/app/depends"` would read `src/app/depends.txt` if it existed. A payload like `"../../.env"` could leak API keys. The path must be validated to stay within `_prompts_dir` using `prompt_path.resolve().is_relative_to(self._prompts_dir.resolve())`.

### High: Hardcoded absolute paths in `DashboardController._sync_team_manager`
**File:** `src/controllers/DashboardController.py`

```python
exporter = "/opt/agendino/export_team_manager.py"
...
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```

Both paths are specific to one deployment. On any other machine the exporter check silently returns (acceptable), but the `open()` will raise `FileNotFoundError` during summarization if the directory doesn't exist. The log path should use a configurable location derived from project root.

### High: Personal data and machine-specific configuration committed to version control
**File:** `export_team_manager.py`

The file contains hardcoded paths (`DB = "/opt/agendino/settings/agendino.db"`, `VAULT = "/home/git/obsidian-working"`), a real person's name (`ME = "Trent"`), a full organizational roster in `ROLE` with names, roles, and relations, and personally identified group membership lists in `NONPERSON`. This should not be in a shared repository.

### Medium: `create_manual_action_item` falls back to arbitrary `recordings[0]`
**File:** `src/controllers/ActionItemController.py`

```python
recordings = self.db_repo.get_recordings()
if recordings:
    recording_id = recordings[0].id  # Use the most recent recording
```

The comment says "most recent" but `get_recordings()` returns rows in insertion order with no `ORDER BY`, so `recordings[0]` is the **oldest** recording. Manual action items will be incorrectly associated with an unrelated recording.

### Medium: `SystemPromptsRepository.get_all` silently ignores Italian prompts
**File:** `src/repositories/SystemPromptsRepository.py`

```python
if lang_dir.name != "en":
    continue
```

The repo contains a full `system_prompts/it/` tree with Italian prompts, but this filter makes them completely invisible to the API. Users who added Italian prompts will get no feedback that they are excluded.

### Medium: `DBActionItem.__init__` type annotation for `task_id` does not reflect nullability
**File:** `src/models/DBActionItem.py`

```python
def __init__(self, id: int | None, task_id: int, ...):
```

`task_id` is typed as `int` but is passed as `None` in numerous call sites (`_extract_action_items_from_summary`, `create_manual_action_item`, etc.). The type annotation should be `int | None`.

### Low: `_ensure_action_items_table` schema migration is not atomic
**File:** `src/repositories/SqliteDBRepository.py`

The migration creates a new table, copies data, drops the old table, and renames — across multiple statements without wrapping in an explicit transaction. If any step fails mid-migration, the table is left in a partial state. This should use `BEGIN` / `COMMIT` with `PRAGMA foreign_keys = OFF` inside the transaction.

---

## 2. Performance Improvements

### N+1 query in `get_recordings_status`
**File:** `src/controllers/DashboardController.py`

```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```

This fires one SQL query per recording to count summaries. With 100 recordings, 100 extra queries hit SQLite on every dashboard load. `get_latest_summaries_map()` is already called; a parallel `get_summary_counts_map()` returning `{recording_name: count}` via a single `GROUP BY` query would eliminate this entirely.

### Migration-check methods called on every DB operation
**Files:** `src/repositories/SqliteDBRepository.py` → `_ensure_action_items_table`, `_ensure_calendar_tables`, `_ensure_recording_columns`

Each of these opens a connection, runs `SELECT` or `ALTER TABLE` statements, and closes it — on **every call** to any action item or calendar method. At scale these add measurable latency. These checks should run once at startup (`__init__`) using a `_migrated` flag.

### New service instances created per request
**File:** `src/app/depends.py`

Every HTTP request to any endpoint constructs a new `DashboardController`, `SqliteDBRepository`, `LocalRecordingsRepository`, `ClaudeSummarizationService`, etc. FastAPI's `Depends` does not cache these by default. The heavyweight services (Whisper model loader, Anthropic client, ChromaDB client) should be singletons at module or lifespan level.

### `get_recordings_for_day` filters in Python after a full table scan
**File:** `src/repositories/SqliteDBRepository.py`

```python
rows = conn.execute("SELECT id, name, label, duration, recorded_at, transcript FROM recording").fetchall()
```

This loads **all** recordings then filters by date in Python. A simple `WHERE recorded_at LIKE ? OR name LIKE ?` with the date string would restrict data at the DB level.

### `sync_meeting_titles` loads all recordings to find one
**File:** `src/controllers/ActionItemController.py`

```python
recordings = self.db_repo.get_recordings()
recording = next((r for r in recordings if r.id == recording_id), None)
```

`get_recording_by_id` already exists on the repository (`SqliteDBRepository` has it). This should simply call that.

### `RAGController.load_summaries` re-embeds already-loaded summaries
**File:** `src/controllers/RAGController.py`

`is_loaded(summary_id)` exists on `VectorStoreRepository` but is never called in `load_summaries`. Every click of "Load / Refresh Summaries" re-embeds every document, consuming Gemini API quota unnecessarily.

---

## 3. Missing Error Handling

### `_sync_team_manager` opens a hardcoded file unconditionally
**File:** `src/controllers/DashboardController.py`

```python
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```

If `/opt/agendino/` does not exist (which it won't on most installations), this raises `FileNotFoundError` **during the `summarize_recording` path**, causing the summarization response to fail with an unhandled 500 error. The file open should be inside the same guard that checks `os.path.exists(exporter)`.

### Gemini uploaded file is never deleted after transcription
**File:** `src/services/TranscriptionService.py`

```python
uploaded = self._client.files.upload(file=path, config=...)
response = self._client.models.generate_content(...)
return response.text
```

The Gemini Files API has storage limits and retention windows. The uploaded file object (`uploaded`) is never deleted. Under heavy use this will exhaust the API's file storage quota. `self._client.files.delete(uploaded.name)` should be called in a `finally` block.

### SMTP connection has no timeout
**File:** `src/services/EmailService.py`

```python
with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
    server.starttls()
    server.login(self.email_user, self.email_password)
    server.send_message(msg)
```

`smtplib.SMTP` defaults to no timeout. A slow or unreachable SMTP server will block the async event loop indefinitely. The constructor call should include `timeout=30`.

### `ObsidianService` auto-commit exit code is ignored
**File:** `src/services/ObsidianService.py`

```python
result = subprocess.run(['/bin/bash', self.auto_commit_script], capture_output=True, text=True, check=False)
logging.info(f"Auto-commit return code: {result.returncode}")
```

The return code is logged but no action is taken on failure. If the commit fails (e.g., merge conflict, auth error), the caller receives `{"ok": True}` and the user has no indication their data wasn't committed.

### `auto_commit.py` will commit `.env` files if present
**File:** `auto_commit.py`

```python
subprocess.run(['git', 'add', '-A'], cwd=REPO_PATH, ...)
```

`git add -A` stages everything including `.env` if it has been created at the repo root. If a user creates a real `.env` with API keys and the auto-commit service is running, those secrets will be committed to git history. At minimum, the script should verify `.env` is in `.gitignore` before staging.

### `migrate_action_items.py` opens a raw SQLite connection alongside the ORM
**File:** `migrate_action_items.py`

```python
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "settings/agendino.db"))
...
conn.close()
```

The connection is closed in a `try` block without a `finally`. If an exception is raised before `conn.close()`, the connection leaks. Use `with sqlite3.connect(...) as conn:` instead.

---

## 4. New Feature Ideas

### 1. Dashboard search and filter bar
The recordings table can grow to hundreds of entries with no way to filter or search by name, date range, tag, or status (transcribed / summarized). A search input combined with status/date filters (implemented client-side on the JSON payload) would dramatically improve usability, especially since the endpoint already returns all metadata needed.

### 2. Transcript-synchronized audio playback
The transcript modal shows the full text and plays audio independently. Highlighting the current transcript segment as audio plays (using the existing `[MM:SS]` timestamps in the transcript format) would let users quickly verify speaker attribution and spot transcription errors, reducing the manual review burden.

### 3. Recording-to-calendar auto-linking by time
The CalendarController already has `link_recording_to_event`. After a recording is synced or uploaded with a known `recorded_at`, the system could automatically search for calendar events whose time range overlaps with the recording duration and suggest or auto-create the link. This would surface meeting context (description, attendees, meeting URL) alongside the transcript without manual work.

### 4. Bulk transcription / summarization queue
Currently users click per-recording. A "Select All → Transcribe" or "Transcribe remaining" batch action that sequentially processes recordings (respecting API rate limits with a simple queue) would be valuable for initial setup when importing many recordings at once.

### 5. Webhook / Zapier integration for action items
Completed action items and new summaries are only accessible through the dashboard or email. Exposing a configurable outbound webhook (fired after summarization or when an action item is marked complete) would let users pipe data into tools like Slack, Linear, Jira, or Zapier without custom code.

---

## 5. Code Quality Improvements

### Service instances recreated on every request — no dependency caching
**File:** `src/app/depends.py`

Every `Depends(depends.get_dashboard_controller)` call constructs a new controller, repository, and all services. For services like `WhisperTranscriptionService` (which lazy-loads a ~500 MB model) or `VectorStoreRepository` (which opens a ChromaDB client), this is especially wasteful. These should be module-level singletons or use FastAPI's `lifespan` context. At minimum, add `@lru_cache()` to the pure-configuration factory functions.

### `_parse_recording_datetime` duplicated across controllers
**Files:** `src/controllers/DashboardController.py`, `src/controllers/RAGController.py`

Both classes define identical static methods:
```python
@staticmethod
def _parse_recording_datetime(bare_name: str) -> str | None:
    ...
```
This should live in a shared utility module (e.g., `src/utils/recording.py`).

### Type mismatch: `get_summarization_service` returns `ClaudeSummarizationService` typed as `SummarizationService`
**File:** `src/app/depends.py`

```python
def get_summarization_service() -> SummarizationService:
    return ClaudeSummarizationService(api_key=os.getenv("ANTHROPIC_API_KEY"))
```

`ClaudeSummarizationService` and `SummarizationService` are unrelated classes (both have `.summarize()` but share no base class or Protocol). The parallel `TaskGenerationService`/`ClaudeTaskGenerationService` pair has the same problem. A `SummarizationProtocol` (using `typing.Protocol`) with `.summarize(transcript, system_prompt, recording_datetime)` would make this explicit.

### `SqliteDBRepository` is a God class with ~1,500 lines
**File:** `src/repositories/SqliteDBRepository.py`

It manages six separate domain areas: recordings, summaries, tasks, calendar events, shared calendars, action items, and daily recaps. Each area should be a separate repository class (e.g., `CalendarEventRepository`, `ActionItemRepository`) sharing a connection factory. This would also make the per-method migration-check calls easier to consolidate.

### `escapeHtml` defined twice inside `dashboard.js`
**File:** `src/static/dashboard.js`

There is a module-level `escapeHtml` definition and a second identical definition inside the `DOMContentLoaded` callback. The inner definition shadows the outer one for all closures in that callback. The inner definition should be removed.

### `get_summary` in `DashboardController` is dead legacy code
**File:** `src/controllers/DashboardController.py`

`get_summary` exists alongside `get_summaries`, attempts to access `db_rec.summary` (a compatibility field), and falls through to `get_summaries` anyway. No external endpoint calls `get_summary` directly (the API uses `get_summaries`). This method adds confusion and should be removed.

### Test coverage gaps for core business logic
**File:** `tests/`

The following are entirely absent from tests: `ClaudeSummarizationService`, `ClaudeTaskGenerationService`, `EmailService`, `ICalSyncService`, `ObsidianService`, `RAGService`, `RAGController`, `ActionItemController`, and `CalendarController`. The `NotionService` test only covers static/pure methods — the actual HTTP publish path is untested. Given the production bugs noted above (the `tasks=` kwarg, the `summary_id = 75`), test coverage of the publish flow would have caught the issue immediately.

### `get_config()` mutates and returns a shared module-level dict
**File:** `src/app/depends.py`

```python
config = {}

def get_config():
    items = os.environ.items()
    for item in items:
        config[item[0]] = item[1]
    return config
```

`config` is a module-level mutable dict that is updated and returned on each call. The function is not used in any meaningful way — all callers use `os.getenv()` directly. This should be removed entirely.

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
