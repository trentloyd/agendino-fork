# Weekly Code Review — Agendino

**Date:** 2026-08-18  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### **[Critical] Notion publishing is permanently broken — unexpected `tasks` kwarg**
**File:** `src/controllers/DashboardController.py` → `publish_summary`; `src/services/NotionService.py` → `publish_summary`

`DashboardController.publish_summary` calls every publish service with `tasks=tasks_list`:
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,   # ← added for Obsidian
)
```
`NotionService.publish_summary` does **not** accept a `tasks` parameter and has no `**kwargs`. Every Notion publish attempt raises `TypeError: publish_summary() got an unexpected keyword argument 'tasks'`, which is silently swallowed by the `except Exception as e` wrapper and returned as `{"ok": False, "error": "Publish failed: …"}`. Obsidian works because its method does accept `tasks`.

---

### **[Critical] Hardcoded `summary_id = 75` in `create_manual_action_item`**
**File:** `src/controllers/ActionItemController.py`

```python
summary_id = 75  # Use a known valid summary_id
```
This literal ID will not exist on any fresh installation, causing a foreign key constraint violation for every manual action-item creation. The correct approach is to query for the most recent summary of the chosen recording or make `summary_id` nullable.

---

### **[Critical] Path traversal vulnerability in audio file endpoint**
**File:** `src/app/api/endpoints/dashboard.py`, `src/repositories/LocalRecordingsRepository.py`

`GET /api/dashboard/audio/{name}` passes `name` directly to:
```python
def get_path(self, filename: str) -> str:
    return os.path.join(self._local_recordings_path, filename)
```
A crafted URL name containing `..` segments can escape the recordings directory. The name should be sanitised (e.g., `pathlib.Path(name).name` to strip directory components) before constructing the path.

---

### **[Critical] Non-atomic schema migration in `_ensure_action_items_table` can corrupt the DB**
**File:** `src/repositories/SqliteDBRepository.py`

The method performs a CREATE / INSERT / DROP / RENAME sequence outside any transaction:
```python
conn.execute("CREATE TABLE action_items_new ...")
conn.execute("INSERT INTO action_items_new SELECT * FROM action_items")
conn.execute("DROP TABLE action_items")
conn.execute("ALTER TABLE action_items_new RENAME TO action_items")
```
If any step fails mid-way (disk full, power loss), the `action_items` table is dropped but the rename never happens, permanently destroying data. The whole block must be wrapped in `BEGIN EXCLUSIVE`/`COMMIT`. Additionally, this migration runs on **every single action-item operation** rather than once on startup.

---

### **[High] XSS in HTML email generation**
**File:** `src/services/EmailService.py` → `_format_action_item_html`, `_create_action_items_html`

Action-item `title`, `description`, `meeting_title`, and `assigned_to` values are inserted directly into HTML without escaping:
```python
return f"""
    <div class="item-title">{item['title']}</div>
```
If any of these fields contains `<script>` or other HTML, it is injected into the email body. Use `html.escape()` on all user-controlled strings.

---

### **[High] Bare `except` clause silently swallows `KeyboardInterrupt` / `SystemExit`**
**File:** `src/controllers/DashboardController.py` → `_extract_action_items_from_summary`

```python
except:
    pass
```
This bare clause catches every Python exception including signals. Change to `except Exception:` and at minimum log the error.

---

### **[High] Timezone stripping without conversion in iCal sync**
**File:** `src/services/ICalSyncService.py` → `_to_datetime_str`

```python
if isinstance(dt_val, datetime):
    # Strip timezone info for naive storage
    return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
Timezone-aware `datetime` objects (e.g., UTC events from Google Calendar) are stored as-is without first converting to local or UTC. A 3 PM UTC event stored without conversion would appear 3 PM local time everywhere, which is wrong. Convert to a canonical timezone (UTC recommended) before formatting.

---

### **[Medium] N+1 query in `get_recordings_status` — fetches full summary text per recording**
**File:** `src/controllers/DashboardController.py`

```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
For N recordings, this issues N extra queries each returning **all summary text** just to count rows. This should be a single `SELECT recording_id, COUNT(*) FROM summary GROUP BY recording_id` query issued once.

---

### **[Medium] Hardcoded absolute paths tied to `/opt/agendino/`**
**File:** `src/controllers/DashboardController.py` → `_sync_team_manager`; `export_team_manager.py`

```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
These hard-coded paths break any installation not at `/opt/agendino`. `export_team_manager.py` also embeds a real person's name (`ME = "Trent"`), real colleague names, and organizational roles as literal code.

---

### **[Low] `DBActionItem.task_id` typed as `int` but treated as `int | None` everywhere**
**File:** `src/models/DBActionItem.py`

```python
def __init__(self, id: int | None, task_id: int, ...):
```
But throughout the codebase `task_id=None` is passed for summary-extracted items. The annotation should be `task_id: int | None`.

---

## 2. Performance Improvements

### **1. `_ensure_action_items_table` / `_ensure_calendar_tables` called on every operation**
Both methods check (and potentially alter) the schema on every DB call. Move migration/creation to `__init__` or a one-time startup hook, and guard with a module-level flag.

### **2. Full summary text loaded just to count summaries**
As noted in §1, `get_summaries()` fetches entire `summary` TEXT columns to count. Replace with a dedicated `COUNT(*)` query, or add a summary-count column to the single `get_latest_summaries_map` query.

### **3. `depends.py` creates new service/controller instances on every HTTP request**
```python
def get_dashboard_controller() -> DashboardController:
    return DashboardController(
        sqlite_db_repository=get_sqlite_db_repository(),
        ...
    )
```
Every request constructs 6+ objects including DB repository, Jinja2 templates, and service objects. FastAPI's `@lru_cache` or `Depends(..., use_cache=True)` should cache at least the stateless services. This is especially costly for `WhisperTranscriptionService`, which manages a heavy ML model.

### **4. `get_recordings` fetches full `transcript` TEXT for all recordings**
```python
result = conn.execute(
    "SELECT id, name, label, duration, file_extension, recorded_at, created_at, transcript FROM recording"
)
```
The `transcript` field can be megabytes per row. When only the dashboard list needs to know whether a transcript *exists*, use `CASE WHEN transcript IS NOT NULL THEN 1 ELSE 0 END AS has_transcript` instead of selecting the full text.

### **5. Vector-store embeddings are called one-at-a-time**
**File:** `src/repositories/VectorStoreRepository.py`

`_embed([text])` is called with a single-element list per document in `load_summaries`. The Gemini embedding API supports batch input; loading 50 summaries currently makes 50 separate API calls. Batch in groups of, say, 50.

### **6. `LocalRecordingsRepository.get_all` scans the full directory on every dashboard refresh**
For directories with many files, `os.listdir` + extension filtering on every status poll is wasteful. Consider caching with an inotify/FSEvents watcher on supported platforms, or at minimum debouncing.

---

## 3. Missing Error Handling

### **1. Gemini uploaded files are never deleted after transcription**
**File:** `src/services/TranscriptionService.py`

```python
uploaded = self._client.files.upload(file=path, ...)
response = self._client.models.generate_content(...)
return response.text
```
If `generate_content` raises (rate limit, timeout, malformed response), the uploaded file stays in the user's Gemini file store indefinitely. Wrap in `try/finally` and call `self._client.files.delete(uploaded.name)`.

### **2. `CalendarController.create_shared_calendar` silently discards sync failure**
**File:** `src/controllers/CalendarController.py`

```python
try:
    self._do_sync_calendar(saved)
except Exception:
    pass  # Don't fail creation if initial sync fails
```
The exception is never logged. The caller gets a success response but the calendar has zero events and no indication of the sync failure. At minimum, log the exception and include `"sync_warning"` in the response.

### **3. SMTP operations lack an explicit timeout**
**File:** `src/services/EmailService.py`

```python
with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
    server.starttls()
    server.login(self.email_user, self.email_password)
    server.send_message(msg)
```
No `timeout` argument is passed to `smtplib.SMTP(...)`. A hung SMTP server will block the async event loop indefinitely. Pass `timeout=30`.

### **4. `DashboardController._sync_team_manager` opens a log file at a hardcoded path without error handling**
**File:** `src/controllers/DashboardController.py`

```python
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
If `/opt/agendino/` does not exist or is not writable, this raises `FileNotFoundError`/`PermissionError` which propagates up and silently fails the summarize response. The outer `except Exception as e` in `_sync_team_manager` prevents a crash, but the error is not surfaced to the user.

### **5. `SqliteDBRepository._initialize_db` has no fallback if the init SQL file is missing**
**File:** `src/repositories/SqliteDBRepository.py`

```python
with open(init_sql_script, "r") as f:
    sql = f.read()
```
If `init_sql_script` does not exist (misconfigured deployment), an unhandled `FileNotFoundError` bubbles all the way to the HTTP layer. A clear startup error would be far more helpful.

### **6. `ObsidianService.publish_summary` writes files without validating the vault path still exists**
**File:** `src/services/ObsidianService.py`

The vault existence is checked only in `__init__`. If the vault is on a removable drive or network share that disappears after startup, `filepath.write_text(...)` will raise without an informative error message. Re-validate before writing.

---

## 4. New Feature Ideas

### **1. Automatic recording pipeline (watch-folder + auto-transcribe/summarize)**
Currently users must manually trigger transcription and summarization for each recording. Adding a background task that watches `local_recordings/` for new files and automatically runs the transcription → summarization → action-item extraction pipeline would eliminate the largest manual friction point. Users could configure which prompt is applied automatically.

### **2. Full-text search across transcripts from the dashboard**
The Knowledge Base page provides semantic (vector) search over summaries, but the main dashboard has no search at all. A simple SQLite FTS5 index on `recording.transcript` and `summary.summary` would allow fast keyword search directly from the recording list (e.g., "find all recordings mentioning 'budget'"). This complements but does not duplicate the RAG approach.

### **3. Export summaries to PDF / DOCX**
Users frequently share meeting notes via email or in document management systems. Adding an export button that converts the markdown summary to a formatted PDF (using `weasyprint` or `reportlab`) or DOCX (using `python-docx`) would make the tool immediately useful outside the web UI. The existing structured sections (Executive Summary, Decisions, Action Items) map naturally to document heading styles.

### **4. Calendar event creation from action item due dates**
Action items with `due_date` set are currently tracked only within Agendino. A one-click "Add to calendar" feature that creates a `calendar_event` row (and optionally syncs to the user's external calendar via the iCal API) would close the loop between the action-items list and the calendar page. This reuses existing `CalendarController` infrastructure.

### **5. Side-by-side multi-version summary diff**
When re-summarizing a recording with a different prompt (e.g., `DefaultSummary` vs `ExecutiveTLDR`), the new version is stored but comparing versions requires opening each one separately. A diff view (highlighting added/removed bullets or sentences between versions) would help users choose the best prompt for a recording type and understand what information different prompts surface or discard.

---

## 5. Code Quality Improvements

### **1. `SqliteDBRepository` is a 1 000-line god class**
**File:** `src/repositories/SqliteDBRepository.py`

The single class handles recordings, summaries, tasks, action items, calendar events, shared calendars, recording-event links, and daily recaps. Split into domain-focused repository classes (`RecordingRepository`, `SummaryRepository`, `ActionItemRepository`, `CalendarRepository`) that share a common `_connect()` helper. This would also make the tests vastly more targeted.

### **2. `depends.py` uses global mutable state and rebuilds everything per request**
**File:** `src/app/depends.py`

```python
config = {}

def get_config():
    items = os.environ.items()
    for item in items:
        config[item[0]] = item[1]
    return config
```
`config` is a module-level dict mutated on every call. Use `functools.lru_cache` on pure factory functions (`get_sqlite_db_repository`, `get_transcription_service`, etc.) so that long-lived objects (Jinja2 templates, service wrappers, the Whisper model holder) are created once per process rather than once per request.

### **3. Hardcoded magic comment left in production code**
**File:** `src/controllers/ActionItemController.py`

```python
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```
This is debugging scaffolding that was never removed. Beyond being broken (§1), it is documentation of the development environment leaking into production code.

### **4. Test coverage is very thin for the most critical paths**
**File:** `tests/`

The test suite covers `DashboardController`, `DBRecording`, `LocalRecordingsRepository`, `SqliteDBRepository` (basic CRUD), `SystemPromptsRepository`, `NotionService` (static helpers), and `ProactorService`. Completely uncovered: `ActionItemController`, `RAGController`, `CalendarController`, `EmailService`, `ICalSyncService`, `SummarizationService`/`ClaudeSummarizationService` (only `_parse_response` is tested), all `depends.py` wiring, and the entire `VectorStoreRepository`. Adding integration tests with a real in-memory SQLite DB for the action-item and calendar flows would catch the hardcoded-ID bug and schema-migration issues described above.

### **5. Duplicate action-item creation paths create silent duplicates**
**File:** `src/controllers/DashboardController.py`

`summarize_recording` calls `_extract_action_items_from_summary` when `prompt_id == "en/General/DefaultSummary"`. If the user then calls `POST /api/dashboard/tasks/generate` for the same summary, `_create_action_item_from_task` creates a second set of action items for the same content. There is no deduplication check (`task_id` uniqueness is not enforced in the schema). Either the two extraction paths should be mutually exclusive, or a `UNIQUE(recording_id, title)` constraint should be added with an `INSERT OR IGNORE`.

### **6. `print` used as a logging mechanism throughout**
**Files:** `src/repositories/SqliteDBRepository.py`, `src/controllers/DashboardController.py`, `src/services/ClaudeTaskGenerationService.py`

Examples:
```python
print("Updating action_items table schema to allow NULL task_id...")
print(f"Warning: Failed to create action item for task '{task.title}': {e}")
```
These bypass the configured `logging` infrastructure, cannot be filtered by log level, and appear in stdout rather than the application log file. Replace with `logger.info(...)` / `logger.warning(...)`.

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
