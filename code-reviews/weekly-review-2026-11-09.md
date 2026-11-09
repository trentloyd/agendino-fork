# Weekly Code Review — Agendino

**Date:** 2026-11-09  
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

This will cause a foreign-key constraint violation on any database that does not contain a summary with ID 75. It is a development leftover that should use the actual latest summary from the target recording, or make `summary_id` nullable in the schema for manual items.

---

**`NotionService.publish_summary` will raise `TypeError` at runtime**
(`src/controllers/DashboardController.py` → `publish_summary`, `src/services/NotionService.py`)

`DashboardController.publish_summary` always passes `tasks=tasks_list` as a keyword argument, but `NotionService.publish_summary`'s signature does not accept `tasks`:

```python
# NotionService.publish_summary — no tasks param
def publish_summary(self, title, summary_markdown, tags=None, recording_name=None):
```

Every Notion publish will fail with `TypeError: publish_summary() got an unexpected keyword argument 'tasks'`.

---

**`auto_commit.py` uses `git add -A`**, staging the SQLite database (`settings/agendino.db` with full transcript and summary content), any `.env` file, and uploaded audio files. This is a data-privacy and security risk for anyone running the auto-commit service.

---

### High

**`SystemPromptsRepository.get_all` silently drops all non-English prompts**
(`src/repositories/SystemPromptsRepository.py`)

```python
if lang_dir.name != "en":
    continue
```

The repository contains 20+ Italian prompts under `system_prompts/it/`, none of which will ever appear in the UI prompt picker. The filter appears intentional but contradicts the multi-language directory structure and documentation.

---

**`WhisperTranscriptionService` model is recreated on every HTTP request**
(`src/app/depends.py` → `get_whisper_transcription_service`)

`depends.py` creates a fresh service instance per FastAPI dependency injection call. Since `_model` is an instance variable with lazy loading, the model context is lost between requests. Each request that uses Whisper re-initialises `WhisperModel(...)` from disk, adding seconds of cold-start overhead.

---

**`publish_recording` returns misleading error when recording is absent**
(`src/controllers/DashboardController.py`)

```python
db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
if not db_rec:
    return {"ok": False, "error": "No summary found — summarize the recording first"}
```

When the recording itself does not exist, the error message blames a missing summary, which will confuse callers debugging a 404-equivalent scenario.

---

### Medium

**`_sync_team_manager` hardcodes `/opt/agendino/`**
(`src/controllers/DashboardController.py`)

```python
exporter = "/opt/agendino/export_team_manager.py"
...
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```

This silently becomes a no-op for any installation not at `/opt/agendino`, with no warning to the operator. The exporter path and log path should derive from `get_root_path()` or an environment variable.

---

**`ObsidianService` auto-commit subprocess has no timeout**
(`src/services/ObsidianService.py`)

```python
result = subprocess.run(
    ['/bin/bash', self.auto_commit_script],
    capture_output=True, text=True, check=False
)
```

A hanging git operation or network issue will block the HTTP response indefinitely. The call needs `timeout=`.

---

**No path-traversal protection in `LocalRecordingsRepository.get_path`**
(`src/repositories/LocalRecordingsRepository.py`)

`get_path(filename)` does a plain `os.path.join(self._local_recordings_path, filename)`. The `name` parameter ultimately originates from a URL path parameter (e.g. `/api/dashboard/audio/{name}`). A crafted name like `../../etc/passwd` would resolve outside the recordings directory.

---

## 2. Performance Improvements

**N+1 query per recording in `get_recordings_status`**
(`src/controllers/DashboardController.py`)

```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```

For N recordings this fires N additional SQL queries. The existing `get_latest_summaries_map()` returns a single-query map; a companion `get_summary_counts_map()` or a GROUP BY sub-query would eliminate this loop entirely.

---

**Schema-existence check on every DB call**
(`src/repositories/SqliteDBRepository.py`)

Both `_ensure_action_items_table()` and `_ensure_calendar_tables()` execute DDL and SELECT probes on every read/write operation. Run migrations once at startup or in `__init__`, not inside hot-path methods.

---

**`get_recordings_for_day` loads every recording in Python**
(`src/repositories/SqliteDBRepository.py`)

```python
rows = conn.execute("SELECT ... FROM recording").fetchall()
for row in rows:
    if row["recorded_at"]:
        rec_date = row["recorded_at"][:10]
    if rec_date == date_str:
```

A SQL `WHERE DATE(recorded_at) = ?` clause (with a fallback for null dates) would avoid the full-table scan and Python-side filtering.

---

**N+1 hydration in `get_recordings`**
(`src/repositories/SqliteDBRepository.py`)

`_hydrate_latest_summary_fields(conn, rec)` runs one SQL query per recording. A single LEFT JOIN against the max-version summary sub-query (as already used in `get_latest_summaries_map`) would replace N round-trips with one.

---

**`TranscriptionService` does not delete Gemini-uploaded files**
(`src/services/TranscriptionService.py`)

After `self._client.files.upload(...)` and content generation, the remote file is never deleted. Over time this consumes Gemini storage quota and leaves meeting audio on third-party servers longer than necessary.

---

**RAG transcript chunking discards most content**
(`src/controllers/RAGController.py`)

```python
chunks = [transcript_text[i:i+4000] for i in range(0, len(transcript_text), 3000)]
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```

For a long recording only the first ~5 KB of transcript is indexed; everything after that is silently dropped, degrading RAG recall significantly.

---

## 3. Missing Error Handling

**`SqliteDBRepository._ensure_action_items_table` data-loss window**
(`src/repositories/SqliteDBRepository.py`)

The migration path (rename old table → copy → drop → rename new) is not wrapped in a transaction and does not handle the case where `action_items_new` already exists from a previous failed migration. A second concurrent call could also interleave, causing corruption.

---

**`EmailService` SMTP operations have no timeout**
(`src/services/EmailService.py`)

```python
with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
    server.starttls()
    server.login(self.email_user, self.email_password)
```

`smtplib.SMTP` accepts a `timeout` parameter. Without it, an unreachable SMTP server blocks the asyncio event loop (or the HTTP request) indefinitely.

---

**`DailyNotificationService.task` is never assigned**
(`src/services/DailyNotificationService.py`)

`self.task = None` is set but `start_scheduler` runs in the caller's event loop via `asyncio.run(run())`. `stop_scheduler` calls `self.task.cancel()` but `task` is always `None`, meaning graceful shutdown is silently broken.

---

**`VectorStoreRepository.__init__` failure is unhandled**
(`src/repositories/VectorStoreRepository.py`)

`chromadb.PersistentClient(path=persist_path)` will raise if the path is unwritable or if ChromaDB encounters a schema incompatibility after a version upgrade. The exception propagates to startup and crashes the entire application without a user-readable error.

---

**Gemini file upload not cleaned up on transcription error**
(`src/services/TranscriptionService.py`)

If `generate_content` raises, the uploaded file remains on Gemini's servers. A `try/finally` block should call `self._client.files.delete(uploaded.name)`.

---

**`create_manual_action_item` does not validate the fallback `recording_id`**
(`src/controllers/ActionItemController.py`)

```python
recordings = self.db_repo.get_recordings()
if recordings:
    recording_id = recordings[0].id  # Use the most recent recording
```

`get_recordings()` has no ORDER BY guarantee, so `recordings[0]` may not be the most recent. More importantly, the subsequent hardcoded `summary_id = 75` will cause an FK violation regardless.

---

## 4. New Feature Ideas

**1. Background transcription / summarization queue with status polling**
Transcription can take minutes for long recordings. Currently the HTTP request blocks until completion, causing timeouts and poor UX. A lightweight job queue (even a simple in-process `asyncio.Queue` backed by a `job_status` DB table) with a `/status/{job_id}` polling endpoint would let the browser show live progress without request timeouts.

**2. System-prompt editor in the web UI**
Prompts are currently managed by editing `.txt` files on the server. A CRUD UI at `/settings/prompts` — with a text editor, category/language picker, preview (run on a sample transcript), and delete — would make prompt management accessible without SSH access and would enable per-user custom prompts in a future multi-tenant scenario.

**3. Duplicate-upload detection before ingestion**
Currently a re-upload of the same file either fails with "already exists" or creates a duplicate if the name differs slightly. Computing a SHA-256 hash of the audio payload at upload time and storing it in the `recording` table (or a separate hash store) would let the system surface a "this file was already imported as X" warning before creating a duplicate transcript/summary pair.

**4. Calendar-based auto-linking of recordings to events**
When a recording is uploaded with a date/time (parsed from the filename or set via the datetime editor), the system could automatically search for calendar events whose time range overlaps the recording duration and suggest a link. This would close the gap between calendar and recordings without requiring manual linking on every sync.

**5. Per-recording notification / webhook on completion**
After summarization completes, allow users to configure a webhook URL (or choose from supported channels like Slack or email) that receives the summary JSON. This enables downstream automation — for example, posting a Slack message, creating a Jira ticket from action items, or triggering a CI pipeline — without coupling those integrations into Agendino itself.

---

## 5. Code Quality Improvements

**Services re-instantiated on every request; Whisper model is never reused**
(`src/app/depends.py`)

Every dependency factory (`get_whisper_transcription_service`, `get_sqlite_db_repository`, `get_vector_store_repository`, etc.) creates a new object on every HTTP request with no caching decorator. For stateless services this is wasteful; for `WhisperTranscriptionService` it means the model load path is exercised on every request. Marking these factories with `@lru_cache()` or using FastAPI's `Depends` with `use_cache=True` (the default, but requires the dependency to be a singleton scope) would fix this cleanly.

---

**`get_config()` is called but its return value is never used**
(`src/app/depends.py`)

```python
def get_dashboard_controller() -> DashboardController:
    _config = get_config()   # return value never referenced
    return DashboardController(
        sqlite_db_repository=get_sqlite_db_repository(),
        ...
```

`_config` is assigned and immediately discarded. All callers use `os.getenv()` directly. The `config` dict and `get_config()` function are dead code and should be removed.

---

**`_parse_recording_datetime` duplicated across controllers**
(`src/controllers/DashboardController.py`, `src/controllers/RAGController.py`)

The same 10-line static method is copy-pasted verbatim into two controllers. It should live in a shared `utils.py` or `models/utils.py` module and be imported where needed.

---

**`create_manual_action_item` has a TODO-style comment that escaped code review**
(`src/controllers/ActionItemController.py`)

```python
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```

This documents a local development assumption. In production it is a latent FK violation. It should be replaced with a proper lookup for the most recent summary (or the schema constraint should be relaxed).

---

**`export_team_manager.py` hardcodes production paths and personal data**
(`export_team_manager.py`)

```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
NONPERSON = { ... "Frank",  # Frank = interview candidate, not a report ... }
ROLE = { "Anna": ("VP of Product", "manager"), ... }
```

Hardcoded usernames, job titles, relationships, and absolute filesystem paths make this script non-portable and leak personal information into source control. At minimum `DB` and `VAULT` should be environment variables; the roster config (`ROLE`, `ALIAS`, `NONPERSON`) should live in a separate ignored config file.

---

**No formal interface contract between publish services**
(`src/services/NotionService.py`, `src/services/ObsidianService.py`)

Both are registered as "publish services" and called via `svc.publish_summary(...)`, but neither inherits from a common ABC. The `tasks` parameter mismatch (bug §1) would have been caught at import time by an abstract base class. Adding a `PublishService(ABC)` with an `@abstractmethod publish_summary(...)` signature would prevent future signature divergence.

---

**`DBActionItem.__init__` types `task_id` as `int` but accepts `None`**
(`src/models/DBActionItem.py`)

```python
def __init__(self, id: int | None, task_id: int, ...):
```

The DB schema (`task_id INTEGER` with no NOT NULL), the `from_dict` path, and the actual call sites all pass `None` for manual action items. The type annotation should be `task_id: int | None`.

---

**Test coverage gaps for key subsystems**

There are no unit or integration tests for `ActionItemController`, `CalendarController`, `RAGController`, `SqliteDBRepository` action-items methods, `VectorStoreRepository`, or any of the AI service wrappers (`ClaudeSummarizationService`, `ClaudeTaskGenerationService`, `DailyNotificationService`). The existing test suite gives confidence only in `DashboardController`, `NotionService`, `ProactorService`, and the two repositories. The uncovered surface area includes every path that touches financial-grade user data (action items, meeting summaries).

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
