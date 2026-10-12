# Weekly Code Review — Agendino

**Date:** 2026-10-12  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Severity: Critical

**Hardcoded `summary_id = 75` in `ActionItemController.create_manual_action_item`**
(`src/controllers/ActionItemController.py`, ~line 88)
```python
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```
This is hard-coded to IDs that exist only in the developer's database. Any fresh installation will violate the `FOREIGN KEY (summary_id) REFERENCES summary (id)` constraint and crash. The real fix is either to make `summary_id` nullable in the schema or to query the actual latest summary ID.

**Notion publishing always throws `TypeError`**
(`src/controllers/DashboardController.py`, `publish_summary` method)
```python
result = svc.publish_summary(
    title=publish_title,
    summary_markdown=summary.summary,
    tags=tags,
    recording_name=summary.recording_name,
    tasks=tasks_list,          # ← passed to every publish service
)
```
`NotionService.publish_summary` does not accept a `tasks` keyword argument. Every click of "Publish → Notion" raises `TypeError: publish_summary() got an unexpected keyword argument 'tasks'` and silently returns `{"ok": False, "error": "Publish failed: ..."}`. `ObsidianService` accepts `tasks`, but the signature parameter order is different from the named call order — a maintenance trap.

**`action_items` table is absent from `settings/db_init.sql`**
The table is created lazily via `_ensure_action_items_table()` which runs on every single action-item DB call. A fresh database created by `db_init.sql` will not have the table, `migrate_action_items.py` may fail on a clean install, and the deferred creation is also non-atomic (see below).

### Severity: High

**Non-atomic schema migration in `_ensure_action_items_table`**
(`src/repositories/SqliteDBRepository.py`)
```python
conn.execute("DROP TABLE action_items")
conn.execute("ALTER TABLE action_items_new RENAME TO action_items")
```
These two statements are not wrapped in a transaction (no `BEGIN`/`COMMIT`). A crash between them leaves the database without the `action_items` table entirely. Additionally, the schema comparison uses Python string matching on DDL text, which is fragile if SQLite normalises whitespace:
```python
if existing_schema and 'task_id INTEGER NOT NULL' in existing_schema[0]:
```

**Hardcoded absolute paths in `export_team_manager.py`**
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
ME = "Trent"
```
And in `DashboardController._sync_team_manager`:
```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
These paths work only on the original developer's machine. Attempting to sync the Team Manager vault on any other system silently fails (guarded by `os.path.exists(exporter)`), but the log open would crash if `/opt/agendino/` doesn't exist.

**Path-traversal in `SystemPromptsRepository.get_prompt_content`**
(`src/repositories/SystemPromptsRepository.py`)
```python
prompt_path = self._prompts_dir / f"{prompt_id}.txt"
```
`prompt_id` comes from the user (via the API body) and is used directly in a `Path` join. A value like `../../etc/passwd` would escape the prompts directory and expose arbitrary files. Use `Path.resolve()` and verify the resolved path is still inside `self._prompts_dir`.

**`get_recordings_for_day` performs a full-table scan in Python**
(`src/repositories/SqliteDBRepository.py`)
```python
rows = conn.execute("SELECT id, name, label, duration, recorded_at, transcript FROM recording").fetchall()
```
All recordings are pulled and then filtered in a Python loop. With many recordings and a loaded calendar view this becomes very slow and memory-hungry.

### Severity: Medium

**`get_config()` is dead code that mutates a module-level dict unsafely**
(`src/app/depends.py`)
```python
config = {}
def get_config():
    for item in os.environ.items():
        config[item[0]] = item[1]
    return config

def get_dashboard_controller():
    _config = get_config()     # assigned, never read
    return DashboardController(...)
```
The result is never used; every call rebuilds the global `config` dict (a race condition in concurrent workers).

**Bare `except` clause in `_extract_action_items_from_summary`**
(`src/controllers/DashboardController.py`)
```python
try:
    due_date = datetime.strptime(due_date_str, fmt)
    break
except ValueError:
    continue
except:      # catches SystemExit, KeyboardInterrupt, …
    pass
```

**`migrate_action_items.py` opens a raw SQLite connection ignoring `DATABASE_NAME`**
```python
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "settings/agendino.db"))
```
The same file has `get_db_repository()` that honours `os.getenv("DATABASE_NAME", "agendino.db")`. One of the two will operate on the wrong file if `DATABASE_NAME` is customised.

---

## 2. Performance Improvements

**N+1 query in `get_recordings_status` for `summary_count`**
(`src/controllers/DashboardController.py`)
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
This fires one `SELECT … JOIN … WHERE r.name = ?` per recording. With 200 recordings that is 200 extra round-trips. A single query counting summaries grouped by `recording_id` would replace all of them:
```sql
SELECT recording_id, COUNT(*) FROM summary GROUP BY recording_id
```

**`_ensure_action_items_table()` / `_ensure_calendar_tables()` called on every operation**
Both methods execute `SELECT sql FROM sqlite_master …` plus potentially ALTER TABLE on every action-item and calendar query. They should be replaced by a single startup migration check (or use `db_init.sql`).

**`get_recordings()` issues N+1 queries to hydrate summaries**
(`src/repositories/SqliteDBRepository.py`)
```python
for rec in recordings:
    self._hydrate_latest_summary_fields(conn, rec)
```
Each call executes a separate `SELECT … WHERE recording_id = ?` query. `get_latest_summaries_map()` already runs an optimised single-query version — it should be reused here.

**`WhisperTranscriptionService` – transcript chunking creates all chunks to discard most**
(`src/repositories/RAGController.py`)
```python
chunks = [transcript_text[i:i+4000] for i in range(0, len(transcript_text), 3000)]
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```
All chunks are computed; only the first two are used. Use a slice directly:
```python
transcript_text = transcript_text[:4000] + "..." + transcript_text[3000:4000]
```

**`WhisperTranscriptionService` is reinstantiated on every request**
`get_whisper_transcription_service()` in `depends.py` creates a new `WhisperTranscriptionService` object on every request. The model is lazily loaded, but the FastAPI dependency machinery still instantiates a new object. The Whisper model (~500 MB) is the only thing cached (via `self._model`), but a class-level or app-level singleton would be cleaner and save the object-creation overhead.

**`get_linked_recordings` executes two queries per calendar event**
For each event rendered in a day or month view, `_get_linked_recordings` fires an additional query for the linked recordings and another for each recording's latest summary. Use a single `LEFT JOIN`.

---

## 3. Missing Error Handling

**No file-size limit on uploads**
(`src/app/api/endpoints/dashboard.py`)
```python
file_data = await file.read()
```
An arbitrarily large file is read entirely into memory and then written to disk. A 10 GB upload will exhaust RAM. FastAPI supports `Content-Length` checks or streaming writes; neither is used here.

**`TranscriptionService.transcribe` and `SummarizationService.summarize` have no timeout**
Gemini API calls can hang indefinitely. The callers in `DashboardController` are synchronous (blocking an async worker) and have no timeout guard. Even a `httpx` client with `timeout=` set at the `genai.Client` level would help.

**`ObsidianService.auto_commit_script` subprocess has no timeout**
(`src/services/ObsidianService.py`)
```python
result = subprocess.run(['/bin/bash', self.auto_commit_script], ...)
```
If the git commit or push blocks (e.g., waiting for credentials), the HTTP response hangs until the OS kills the process.

**`EmailService.send_daily_action_items` has no SMTP timeout**
```python
with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
```
`smtplib.SMTP` defaults to no timeout. A non-responsive SMTP server blocks the coroutine thread.

**`ICalSyncService.fetch_and_parse` does not bound response body size**
A misbehaving or malicious iCal URL could return an unbounded body. `httpx.get` with no `max_bytes` limit will buffer the entire response.

**`SqliteDBRepository._initialize_db` — `FileNotFoundError` is unhandled**
If `init_sql_script` doesn't exist (misconfigured path), the exception propagates as an unformatted 500 error with no diagnostic message.

**`_extract_action_items_from_summary` failures are logged only with `print()`**
```python
except Exception as e:
    print(f"Warning: Failed to extract action items from DefaultSummary: {e}")
```
Errors vanish in production deployments that redirect stdout. All error reporting in production code should use the `logging` module.

**`DashboardController._create_action_item_from_task` silently swallows failures the same way**
```python
except Exception as e:
    print(f"Warning: Failed to create action item for task '{task.title}': {e}")
```

**`DBActionItem.from_dict` can raise `KeyError` on missing required keys**
```python
task_id=data["task_id"],
recording_id=data["recording_id"],
summary_id=data["summary_id"],
title=data["title"],
```
Unlike optional fields (which use `.get()`), these will raise `KeyError` if the column is missing from a DB row (e.g., after a botched migration). `.get()` with a sensible default or explicit validation is safer.

---

## 4. New Feature Ideas

**1. Scheduled / background transcription queue**
Currently, transcription blocks the HTTP request for the duration of the Gemini or Whisper call (potentially several minutes for long recordings). A task queue (e.g., backed by a simple SQLite-persisted job table or `asyncio.Queue`) would allow users to submit recordings for transcription and poll for completion, preventing HTTP timeouts and enabling batch processing.

**2. Full-text keyword search across transcripts and summaries**
The RAG semantic search is powerful but requires vector loading. A complementary SQLite FTS5 (`CREATE VIRTUAL TABLE transcript_fts USING fts5(…)`) index over the `recording.transcript` and `summary.summary` columns would allow instant keyword search without Gemini/Chroma dependencies and serve as a fallback when the vector store is empty.

**3. Per-recording access log / audit trail**
For compliance-sensitive use cases (recording conversations is regulated in many jurisdictions), a lightweight audit table logging who accessed, transcribed, summarised, or published each recording — with a timestamp and action type — would make the tool usable in regulated environments.

**4. Configurable ROLE/ALIAS/ME for Team Manager export**
`export_team_manager.py` hard-codes personal names, vault paths, and roles at module level. A YAML/TOML config file (or `.env` keys) for `VAULT_PATH`, `MY_NAME`, and a roster file would make the feature usable for any user without editing source code.

**5. Calendar-to-recording auto-linking via title matching**
When a new recording is uploaded or synced, compare its `recorded_at` timestamp against calendar events for that day and suggest (or automatically create) a `recording_event_link` if the event title overlaps with the recording filename. This would reduce the manual step of linking recordings to events on the calendar page.

---

## 5. Code Quality Improvements

**Duplicate `_parse_recording_datetime` static method**
An identical implementation exists in both `DashboardController` and `RAGController`. It should live in a shared utility module (e.g., `src/utils/datetime_utils.py`) and be imported by both.

**`TranscribeRequestDTO.engine` should be a `Literal` or `Enum`**
(`src/models/dto/TranscribeRequestDTO.py`)
```python
engine: str = "gemini"  # "gemini" or "whisper"
```
Any string is accepted; invalid values silently fall through to the Gemini path. Use `Literal["gemini", "whisper"]` to get validation and IDE completion for free.

**`depends.py` creates heavy service objects on every request**
FastAPI dependency injection re-runs `get_dashboard_controller()` on every request, creating new `SummarizationService`, `TranscriptionService`, `WhisperTranscriptionService`, and repository objects each time. Services without per-request state (transcription, summarization, vector store) should be singletons, either via `@lru_cache` on the factory functions or FastAPI `app.state`.

**`_ensure_action_items_table` / `_ensure_calendar_tables` should be removed in favour of `db_init.sql`**
Runtime DDL detection is fragile, slow, and hard to test. Add the `action_items` table and all calendar tables to `settings/db_init.sql`, and replace the runtime checks with a simple schema-version migration table. The existing `_ensure_recording_columns` migration shows the same anti-pattern (using a try/except on `SELECT` to detect missing columns instead of `PRAGMA table_info`).

**`ObsidianService.publish_summary` parameter order differs from the call site**
The method signature is `(self, recording_name, title, tags, summary_markdown, tasks=None)` but the call in `DashboardController` passes all arguments as keyword arguments in a different order. This works today but is easy to break when someone converts a call to positional. Both publish services should share a common abstract interface.

**`print()` used extensively for production-path logging**
In `DashboardController`, `ActionItemController`, `SqliteDBRepository`, and `migrate_action_items.py`, `print()` is used for warnings and status messages. All of these should use `logging.getLogger(__name__)` so output can be controlled by log-level configuration.

**Test coverage gaps**
The test suite covers `DashboardController`, `DBRecording`, `LocalRecordingsRepository`, `SqliteDBRepository` (core recording ops), `SystemPromptsRepository`, `NotionService`, and `ProactorService` well. Missing coverage includes: `ActionItemController`, `CalendarController`, `RAGController`, all action-item methods in `SqliteDBRepository`, `ObsidianService`, `EmailService`, `DailyNotificationService`, `ICalSyncService`, and all `_ensure_*` migration paths.

**`main_template.html` is an empty file**
`src/templates/dashboard/main_template.html` contains no content. It appears to be a leftover artifact and should be removed to avoid confusion.

**`export_team_manager.py` mixes SQLite Row access styles**
`r["name"]` dict-style access on `sqlite3.Row` objects is used throughout, which is correct. However, some loops use `r["id"]` and some use positional index assumptions. The code is also a standalone script with module-level side-effects (`ROLE`, `ALIAS`, `NONPERSON` as mutable globals) that make it untestable without heavy patching. Refactoring into a class with constructor injection would make it portable and testable.

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
