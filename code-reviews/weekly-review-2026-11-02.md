# Weekly Code Review — Agendino

**Date:** 2026-11-02  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**`NotionService.publish_summary` called with an unsupported `tasks` kwarg — Notion publishing is always broken** (`src/controllers/DashboardController.py`)

`DashboardController.publish_summary` calls every publish service with an identical signature including `tasks=tasks_list`, but `NotionService.publish_summary` only accepts `title`, `summary_markdown`, `tags`, and `recording_name`. Every Notion publish attempt raises `TypeError: got an unexpected keyword argument 'tasks'`, which the `except Exception as e` block silently converts to `{"ok": False, "error": "Publish failed: …"}`. The surrounding unit test (`test_publish_success`) uses a `MagicMock`, so it never exercises the real type check.

```python
# DashboardController.publish_summary — always blows up for Notion
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,           # NotionService has no such parameter
)
```

Fix: drop `tasks` from the `NotionService` call path, or add `**kwargs` to `NotionService.publish_summary`.

---

**Hardcoded `summary_id = 75` in `ActionItemController.create_manual_action_item`** (`src/controllers/ActionItemController.py`)

```python
# Get a valid summary_id (needed due to foreign key constraints)
# For manual items, just use the most recent summary from any recording
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```

On any installation other than the developer's own, ID 75 almost certainly does not exist. The INSERT will fail with a foreign-key constraint violation. The correct fix is to look up the highest existing summary ID, or make `summary_id` nullable in the schema and remove the FK.

---

### High

**Path traversal in `SystemPromptsRepository.get_prompt_content`** (`src/repositories/SystemPromptsRepository.py`)

```python
def get_prompt_content(self, prompt_id: str) -> str | None:
    prompt_path = self._prompts_dir / f"{prompt_id}.txt"
    if not prompt_path.exists():
        return None
    return prompt_path.read_text(encoding="utf-8")
```

`prompt_id` comes directly from the API request (`POST /api/dashboard/summarize/{name}` body field). A caller can supply `prompt_id = "../../.env"` and read arbitrary files relative to the prompts directory. Add a check that the resolved path stays within `self._prompts_dir`:

```python
resolved = prompt_path.resolve()
if not resolved.is_relative_to(self._prompts_dir.resolve()):
    return None
```

---

**SSRF via untrusted iCal URL** (`src/services/ICalSyncService.py`)

```python
response = httpx.get(ical_url, timeout=self._timeout, follow_redirects=True)
```

No validation of the URL scheme or host. An authenticated user can point the server at `http://169.254.169.254/` (AWS metadata), `http://localhost:5432/`, or any internal service. At minimum, reject non-`https` URLs and block RFC-1918 address ranges before fetching.

---

**GET `/api/action-items/status/{status}` is permanently shadowed** (`src/app/api/endpoints/action_items.py`)

Starlette matches routes in definition order. `GET /action-items/{action_item_id}` (defined earlier, `action_item_id: int`) is tried first for any path matching `/action-items/<segment>`. A request to `/action-items/status/pending` attempts to cast the literal string `"status"` to `int`, fails validation, and returns HTTP 422 — the status-filter endpoint is unreachable. The fix is to move the literal-prefix routes above the parameterised route.

---

**XSS in HTML email bodies** (`src/services/EmailService.py`)

User-controlled strings are interpolated verbatim into the email HTML:

```python
html += f"""
<div class="item-title">{item['title']}</div>
<div class="item-meta">… {item['meeting_title']} …</div>
"""
```

An action item with title `<img src=x onerror=alert(1)>` will inject active HTML/JS into the rendered email. Escape all user-provided values with `html.escape()`.

---

### Medium

**`_sync_team_manager` relies on hardcoded absolute paths** (`src/controllers/DashboardController.py`)

```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```

These paths are silently skipped on any non-`/opt/agendino` deployment, but will raise `FileNotFoundError` for the log open if `/opt/agendino` doesn't exist as a directory, since the log open is inside the `try` block only for the `Popen` launch. The exporter path and log path should be derived from the project root (already available via `get_root_path()`).

---

**`WhisperTranscriptionService` model reloaded on every HTTP request** (`src/app/depends.py`)

`get_dashboard_controller()` (called on every FastAPI request) calls `get_whisper_transcription_service()` which creates a new `WhisperTranscriptionService` with `self._model = None`. The faster-whisper model (~500 MB) is lazy-loaded per instance, so each new transcription request triggers a fresh model load. The service should be a module-level singleton (e.g., via `@lru_cache` on the factory).

---

**`DBActionItem` type annotation `task_id: int` contradicts nullable usage** (`src/models/DBActionItem.py`)

The constructor signature declares `task_id: int` (non-optional), but every manual/summary-extracted action item passes `task_id=None`. The annotation should be `task_id: int | None` to match the actual schema and avoid misleading type checkers.

---

### Low

**Timezone information stripped silently during iCal import** (`src/services/ICalSyncService.py`)

```python
return dt_val.strftime("%Y-%m-%d %H:%M:%S")   # drops tzinfo
```

Calendar events are stored in naive local time without any normalisation. A meeting in `America/New_York` in a European server timezone will appear shifted by hours in the calendar view with no indication that conversion occurred.

**`TranscriptionService` leaks uploaded Gemini files** (`src/services/TranscriptionService.py`)

`self._client.files.upload(…)` is never followed by `self._client.files.delete(uploaded.name)`. Gemini File API has storage quotas and per-file lifetime limits; leaving files undeleted accumulates usage and may hit limits.

---

## 2. Performance Improvements

**N+1 `get_summaries` calls in `get_recordings_status`** (`src/controllers/DashboardController.py`)

For each database recording in the list, a separate SQL round-trip fetches the full summary version list just to count it:

```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```

With 200 recordings this is 201 DB queries. Add a `get_summary_counts_map()` method (a single `SELECT recording_id, COUNT(*) … GROUP BY recording_id`) and look up counts from the resulting dict.

---

**`_ensure_action_items_table` and `_ensure_calendar_tables` called on every CRUD method** (`src/repositories/SqliteDBRepository.py`)

Every `get_all_action_items`, `create_action_item`, `archive_action_item`, etc., opens a connection and issues a `SELECT sql FROM sqlite_master` check. These guards are necessary for first-use migration but become dead overhead thereafter. Cache a `bool` flag at the repository level once the table is confirmed to exist.

---

**`get_recordings` fetches full transcript text for all recordings** (`src/repositories/SqliteDBRepository.py`)

```sql
SELECT id, name, label, duration, file_extension, recorded_at, created_at, transcript FROM recording
```

`transcript` can be hundreds of kilobytes per row and is fetched on every dashboard load, even though only `has_transcript = transcript IS NOT NULL` is needed. Either add a separate `get_transcript_presence_map()` method or replace the column with `transcript IS NOT NULL AS has_transcript` for the listing query.

---

**`sync_meeting_titles` fetches all recordings to find one by ID** (`src/controllers/ActionItemController.py`)

```python
recordings = self.db_repo.get_recordings()
recording = next((r for r in recordings if r.id == recording_id), None)
```

`SqliteDBRepository` already has `get_recording_by_id`; use it.

---

**No embedding batching in `VectorStoreRepository`** (`src/repositories/VectorStoreRepository.py`)

`_embed` is called with a single-element list per document inside the `load_summaries` loop. The Gemini Embedding API supports batching multiple texts in one call, which would reduce network round-trips from O(n) to O(⌈n/batch_size⌉) when loading hundreds of summaries.

---

**`get_config()` executed but its return value is discarded on every request** (`src/app/depends.py`)

```python
def get_dashboard_controller() -> DashboardController:
    _config = get_config()          # iterates os.environ, result never read
    return DashboardController(…)
```

`_config` is computed but never passed anywhere. Remove the call entirely; all callers already use `os.getenv()` directly.

---

## 3. Missing Error Handling

**`smtplib.SMTP` connections have no timeout** (`src/services/EmailService.py`)

```python
with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
```

A misconfigured or unreachable SMTP host will block the request thread indefinitely. `smtplib.SMTP` accepts a `timeout` parameter; set it (e.g., `timeout=10`). The same applies to the `test_connection` method.

---

**`NotionService._append_blocks` silently swallows HTTP errors** (`src/services/NotionService.py`)

```python
try:
    httpx.patch(f"{self.API_BASE}/blocks/{page_id}/children", …)
except httpx.HTTPError:
    pass
```

When a summary exceeds 100 Notion blocks, the overflow is published in chunks. Any failure in a subsequent chunk is discarded, silently producing a truncated Notion page with no indication of failure. These errors should be accumulated and surfaced in the return value.

---

**`SqliteDBRepository._initialize_db` has no error handling for a missing init script** (`src/repositories/SqliteDBRepository.py`)

If `init_sql_script` path doesn't exist (e.g., wrong working directory), `open(init_sql_script, "r")` raises an unhandled `FileNotFoundError` at startup with no helpful message. Wrap it with a clear error:

```python
if not os.path.exists(init_sql_script):
    raise FileNotFoundError(f"DB init script not found: {init_sql_script}")
```

---

**`priority` and `status` fields accept arbitrary strings with no validation** (`src/models/dto/UpdateActionItemDTO.py`, `CreateActionItemDTO.py`)

```python
priority: str = "medium"
status: Optional[str] = None
```

Pydantic can enforce allowed values with `Literal` or an `Enum`; without it, callers can store any string, causing silent inconsistencies in the filter/display logic that expects exact values (`"high"`, `"medium"`, `"low"`, `"pending"`, `"in_progress"`, `"completed"`).

---

**`export_team_manager.py` opens SQLite in read-only mode without verifying the DB exists** (`export_team_manager.py`)

```python
c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
```

If the hardcoded path `/opt/agendino/settings/agendino.db` doesn't exist, `sqlite3.connect` with `mode=ro` raises `sqlite3.OperationalError: unable to open database file`. There is no `try/except`, so the script crashes without a useful user-facing message.

---

**`ObsidianService.publish_summary` runs the auto-commit script with `check=False`** (`src/services/ObsidianService.py`)

Failures of the git commit script are logged but not reflected in the return value. If the vault write succeeds but the commit fails, the caller gets `{"ok": True}` with silent data-loss risk. Return `{"ok": True, "warnings": ["auto-commit failed: …"]}` at minimum.

---

## 4. New Feature Ideas

1. **Server-Sent Events (SSE) for long-running operations.** Transcription and summarization can take 30–120 seconds. Currently the frontend polls or blocks. An `/api/dashboard/transcribe/{name}/stream` SSE endpoint would let the dashboard show live progress (e.g., Whisper segment count) and eliminate timeouts on slow audio files.

2. **SQLite FTS5 full-text search across transcripts.** The vector store provides semantic search, but users often want exact keyword search (names, project codes, acronyms). Adding an FTS5 virtual table mirroring the `transcript` column — a few lines of SQL — would provide near-instant substring search without any external service, useful when the vector store is empty or the query is lexical.

3. **Background job queue for transcription/summarization with status tracking.** Currently every transcription/summarisation request blocks an HTTP worker thread for the duration of the AI call. A lightweight SQLite-backed queue (an `async_job` table: `id`, `type`, `recording_name`, `status`, `result`, `created_at`) would let the API return immediately with a job ID, allow multiple jobs to run concurrently, and give the UI a `/api/jobs/{id}` polling endpoint to show real progress without blocking.

4. **Pre-meeting brief generation from calendar integration.** The data model already links `recording` → `calendar_event`. A `GET /api/calendar/events/{id}/brief` endpoint could pull all past summaries from recordings linked to the same recurring event series (matched by title similarity or shared participants) and ask Gemini/Claude to generate a "context going into this meeting" brief. This closes the loop between the calendar and knowledge-base features.

5. **Full data export and import (backup/restore).** There is no way to back up or migrate an Agendino instance today. A `GET /api/export` endpoint that produces a structured ZIP (SQLite dump + all local recordings + vector store snapshot) and a matching `POST /api/import` would make the tool production-safe and enable moving between machines. This also addresses the current risk that deleting the SQLite file loses all transcripts/summaries with no recovery path.

---

## 5. Code Quality Improvements

**`_parse_recording_datetime` is duplicated** (`src/controllers/DashboardController.py`, `src/controllers/RAGController.py`)

Identical static methods appear in both controllers. Extract to a shared utility module (e.g., `src/utils/recording_utils.py`) and import from both sites.

---

**`SqliteDBRepository` has too many responsibilities and is ~1 000 lines long** (`src/repositories/SqliteDBRepository.py`)

The class handles recordings, summaries, tasks, calendar events, shared calendars, daily recaps, and action items — eight unrelated domains. Each domain adds another `_ensure_*_table()` migration guard. Split into focused repositories: `RecordingRepository`, `SummaryRepository`, `CalendarRepository`, `ActionItemRepository`, etc. The current class violates the Single-Responsibility Principle and makes unit testing extremely hard.

---

**`action_items` table schema is absent from `db_init.sql`** (`settings/db_init.sql`, `src/repositories/SqliteDBRepository.py`)

All other tables are defined in `db_init.sql`, but `action_items` is created entirely at runtime via `_ensure_action_items_table()`. This means the canonical schema definition is spread across two files, `db_init.sql` doesn't reflect the full database structure, and schema migration logic (the `NOT NULL → NULL` change for `task_id`) lives in application code rather than a migration file. Add the table to `db_init.sql` and use a proper migration approach.

---

**`_extract_action_items_from_summary` is a 100-line deeply-nested method** (`src/controllers/DashboardController.py`)

The method parses a markdown table inline in the controller with multiple nested loops and try/excepts. It should be extracted to a dedicated `ActionItemParser` or `SummaryParser` class, making the logic independently testable and the controller easier to read.

---

**`migrate_action_items.py` bypasses the repository pattern** (`migrate_action_items.py`)

The script uses a raw `sqlite3.connect` with a hardcoded path in addition to instantiating `SqliteDBRepository`. This creates two code paths for the same data, risks schema drift, and won't work if the DB path is customised. All DB access should go through the repository.

---

**No singleton pattern for expensive services** (`src/app/depends.py`)

`WhisperTranscriptionService`, `VectorStoreRepository` (ChromaDB client), `ClaudeSummarizationService` (Anthropic client), and `EmailService` are all re-instantiated on every request because the factory functions are plain functions, not `@lru_cache`-decorated singletons. Heavy initialisation (model load, ChromaDB PersistentClient) is repeated unnecessarily:

```python
# Current
def get_whisper_transcription_service() -> WhisperTranscriptionService:
    return WhisperTranscriptionService(…)          # new instance every request

# Better
@lru_cache(maxsize=1)
def get_whisper_transcription_service() -> WhisperTranscriptionService:
    return WhisperTranscriptionService(…)
```

---

**Missing tests for most controllers and services** (`tests/`)

The test suite covers `DashboardController`, `DBRecording`, `LocalRecordingsRepository`, `SqliteDBRepository`, `SystemPromptsRepository`, `NotionService`, `ProactorService`, and `SummarizationService`. Entirely untested: `ActionItemController`, `RAGController`, `CalendarController`, `ICalSyncService`, `EmailService`, `DailyNotificationService`, `ClaudeSummarizationService`, `ObsidianService`, and `VectorStoreRepository`. Adding tests for `ActionItemController` in particular would have caught the `summary_id = 75` regression.

---

**`dashboard.js` calls `loadDashboard()` (full table refresh) after every action** (`src/static/dashboard.js`)

After upload, transcription, summarisation, task toggle, and deletion, `await loadDashboard()` is called, which re-fetches all recordings and rebuilds the entire table. For users with many recordings, this causes a noticeable flash on every interaction. The API already returns the updated record; update only the affected `<tr>` via the returned data instead of rebuilding the full table.

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
