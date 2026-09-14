# Weekly Code Review — Agendino

**Date:** 2026-09-14  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical
**`src/controllers/ActionItemController.py` — hardcoded `summary_id = 75`**

```python
# Get a valid summary_id (needed due to foreign key constraints)
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```

Every call to `create_manual_action_item` uses a literal primary key that was valid only on the developer's database at the time of writing. On any fresh install, or after a DB reset, the foreign-key constraint `FOREIGN KEY (summary_id) REFERENCES summary (id)` will cause an `IntegrityError`, silently making manual action-item creation permanently broken.

**`requirements.txt` — `anthropic` package is missing**

`ClaudeSummarizationService`, `ClaudeTaskGenerationService`, and `RAGService` all do `from anthropic import Anthropic`, but `anthropic` is not listed in `requirements.txt`. A fresh `pip install -r requirements.txt` will produce `ImportError` at first use of Claude features. The `.env.example` also omits `ANTHROPIC_API_KEY`.

### High Severity

**`src/app/depends.py` — unguarded `ValueError` if `ANTHROPIC_API_KEY` is absent**

```python
def get_summarization_service() -> ClaudeSummarizationService:
    return ClaudeSummarizationService(api_key=os.getenv("ANTHROPIC_API_KEY"))
```

`ClaudeSummarizationService.__init__` raises `ValueError` when the key is missing, but unlike `get_daily_recap_service()` (which wraps in a try/except and returns `None`), this propagates as an unhandled 500 on every `/summarize` or `/tasks/generate` request when the key is not configured.

**`src/controllers/DashboardController.py` — Gemini services dead-imported, Claude silently substituted**

`depends.py` imports `SummarizationService` and `TaskGenerationService` (Gemini-based) but instantiates `ClaudeSummarizationService` / `ClaudeTaskGenerationService` in the factory functions. `DashboardController.__init__` is typed `summarization_service: SummarizationService` but receives a Claude object. The Gemini-based classes remain in the call stack as misleading dead imports.

**`src/controllers/ActionItemController.py` — fallback `recording_id` is arbitrary**

```python
recordings = self.db_repo.get_recordings()
if recordings:
    recording_id = recordings[0].id  # Use the most recent recording
```

The comment says "most recent" but `get_recordings()` has no `ORDER BY`; the first row is effectively random. A manual action item ends up silently linked to a random meeting recording.

### Medium Severity

**`src/repositories/SystemPromptsRepository.py` — path traversal in `get_prompt_content`**

```python
prompt_path = self._prompts_dir / f"{prompt_id}.txt"
if not prompt_path.exists():
    return None
return prompt_path.read_text(encoding="utf-8")
```

`prompt_id` arrives directly from the URL via `POST /api/dashboard/summarize/{name}` → body `prompt_id`. A value like `../../etc/hostname` resolves outside the prompts directory via normal `Path` traversal. No containment check (e.g., `prompt_path.resolve().is_relative_to(self._prompts_dir.resolve())`) is performed.

**`src/controllers/DashboardController.py` — shell-interpolated path in `_sync_team_manager`**

```python
cmd = f"{sys.executable} {exporter} sync {int(recording_id)}"
if commit_script and os.path.exists(commit_script):
    cmd += f" && /bin/bash {commit_script}"
subprocess.Popen(["/bin/bash", "-c", cmd], ...)
```

`commit_script` comes from `os.getenv("OBSIDIAN_AUTO_COMMIT_SCRIPT")` and is interpolated into a shell string. A path containing spaces or shell metacharacters will misbehave. The method also hard-codes `/opt/agendino/export_team_manager.py` and `/opt/agendino/team_manager_sync.log`, making the feature non-functional on any other deployment path.

**`src/services/EmailService.py` — HTML injection in email body**

```python
return f"""
    <div class="item-title">{item['title']}</div>
    ...
    {description}
"""
```

`item['title']`, `item['description']`, `item['meeting_title']`, and `item['assigned_to']` are interpolated into HTML without escaping. An action item title containing `<script>` or `<img onerror=...>` would inject markup into the rendered email.

**`src/repositories/SqliteDBRepository.py` — dangerous in-place migration in `_ensure_action_items_table`**

The migration that recreates the `action_items` table (`CREATE TABLE action_items_new … INSERT INTO action_items_new SELECT * FROM action_items … DROP TABLE action_items …`) runs outside an explicit transaction and has no rollback path. If the `INSERT` or `DROP` fails, data loss is possible. The schema detection string `'task_id INTEGER NOT NULL' in existing_schema[0]` is fragile against whitespace or formatting differences.

**`src/app/api/endpoints/action_items.py` — `sync_meeting_titles` sends `meeting_title` as a query param with no body DTO**

```python
async def sync_meeting_titles(
    recording_id: int,
    meeting_title: str = None,
    ...
):
```

`meeting_title` is a query parameter. The JavaScript client sends it as `?meeting_title=<value>` via `fetch(\`/api/recordings/${recordingId}/sync-meeting-title?meeting_title=...\`)`. With long titles this can exceed URL length limits and is inconsistent with the rest of the API that uses JSON bodies.

---

## 2. Performance Improvements

**N+1 queries inside `get_recordings_status`** (`src/controllers/DashboardController.py`)

```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```

For every recording a full `SELECT … JOIN … WHERE r.name = ?` is issued. With 100 recordings this is 100+ separate round-trips. A single `SELECT recording_id, COUNT(*) FROM summary GROUP BY recording_id` could replace them all. The same N+1 pattern exists in `get_day_detail`:

```python
for rec in recordings:
    rec_summaries = self._sqlite_db_repository.get_summaries(rec["name"])
```

**`_hydrate_latest_summary_fields` called per-row inside `get_recordings`**

```python
for rec in recordings:
    self._hydrate_latest_summary_fields(conn, rec)
```

Each call issues another `SELECT … ORDER BY version DESC LIMIT 1`. The `get_latest_summaries_map()` method already executes a single aggregating JOIN — `get_recordings()` should reuse it.

**Schema-check overhead on every action-items and calendar operation**

`_ensure_action_items_table()` and `_ensure_calendar_tables()` are called before every DB operation on those tables. Each invokes `SELECT sql FROM sqlite_master …`, `CREATE TABLE IF NOT EXISTS …`, and several column-existence probes. These should run once at startup (or be replaced by proper migrations) and not be inline guards.

**`_reconcile_orphan_files` on every dashboard load**

`get_recordings_status()` calls `_reconcile_orphan_files()` unconditionally, which enumerates the filesystem and scans all DB names even when nothing has changed. A simple dirty-flag or TTL cache would reduce filesystem churn.

**Synchronous blocking I/O in async FastAPI routes**

`NotionService` uses `httpx.post(...)` (blocking) from within an `async def` endpoint. `EmailService.send_daily_action_items` uses `smtplib.SMTP` (blocking) called from `await notification_service.send_test_notification(...)`. Both block the event loop for the duration of the network round-trip. They should use `httpx.AsyncClient` and `aiosmtplib` respectively, or be dispatched via `asyncio.to_thread`.

**New dependency instances created per request**

Every request calls `get_dashboard_controller()`, `get_rag_controller()`, etc., instantiating new repositories, services, and Jinja2Templates objects each time. Marking the factories with `Depends(…, use_cache=True)` (or promoting them to FastAPI lifespan singletons) would eliminate the per-request construction cost—especially relevant for `WhisperTranscriptionService` which wraps a large model object.

**`get_config()` rebuilds the dict on every call**

```python
def get_config():
    items = os.environ.items()
    for item in items:
        config[item[0]] = item[1]
    return config
```

This iterates all environment variables and writes into a module-level dict on every invocation. The result is never actually used (only `_config = get_config()` is called in `get_dashboard_controller` but `_config` itself is unused). Either cache properly or delete the function entirely.

---

## 3. Missing Error Handling

**`TranscriptionService.transcribe` — uploaded file never deleted from Gemini Files API**

```python
uploaded = self._client.files.upload(file=path, config=...)
response = self._client.models.generate_content(model=MODEL, contents=[uploaded, TRANSCRIPTION_PROMPT])
return response.text
```

The uploaded file object (`uploaded.name`) is never cleaned up with `self._client.files.delete(uploaded.name)`. Over time the Gemini file store will accumulate all uploaded recordings.

**`ClaudeTaskGenerationService` does not handle malformed JSON fallback**

```python
try:
    tasks = json.loads(response_text)
    if isinstance(tasks, list):
        return tasks
    ...
except json.JSONDecodeError as e:
    logger.error(...)
    return []
```

Unlike `SummarizationService` (which uses `json_repair`), the task-generation service returns an empty list on any JSON error with no repair attempt. Partial or slightly-malformed JSON from Claude (a code fence, a trailing comma) silently yields zero tasks.

**`DashboardController.summarize_recording` — unchecked `recording` being `None` in `_create_action_item_from_task`**

```python
recording = self._sqlite_db_repository.get_recording_by_id(summary.recording_id)
for task in saved:
    self._create_action_item_from_task(task, summary, recording)
```

Inside `_create_action_item_from_task`, `recording` is used without a None guard:

```python
meeting_title=summary.title or recording.title if recording else None,
```

Due to Python operator precedence, this parses as `(summary.title) or (recording.title if recording else None)`. When `summary.title` is a non-empty string this works, but when it is falsy and `recording` is `None`, it evaluates `None.title` and raises `AttributeError`. The same issue exists in the subtask loop.

**`ObsidianService.publish_summary` — auto-commit failures are logged but not surfaced**

```python
result = subprocess.run(
    ['/bin/bash', self.auto_commit_script],
    capture_output=True, text=True, check=False
)
logging.info(f"Auto-commit return code: {result.returncode}")
```

`check=False` means a non-zero return code is silently accepted as success from the caller's perspective. The API response always returns `{"ok": True}` even if the vault commit failed.

**`CalendarController.create_shared_calendar` — initial sync failure is completely hidden**

```python
try:
    self._do_sync_calendar(saved)
except Exception:
    pass  # Don't fail creation if initial sync fails
```

The user receives `{"ok": True}` with no indication that the initial sync encountered an error. A warning field in the response would allow the UI to display a partial-success message.

**`NotionService._append_blocks` — all HTTP errors discarded**

```python
try:
    httpx.patch(f"{self.API_BASE}/blocks/{page_id}/children", ...)
except httpx.HTTPError:
    pass
```

If the Notion page has more than 100 blocks, the overflow content is silently dropped without any error logged or returned to the caller.

**`migrate_action_items.py` — raw SQLite connection not closed on exception**

```python
conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), "settings/agendino.db"))
conn.row_factory = sqlite3.Row
rows = conn.execute(...).fetchall()
conn.close()
```

`conn.close()` is not in a `finally` block. An exception between `connect()` and `close()` leaks the connection. The script also hard-codes `"settings/agendino.db"` rather than reading `DATABASE_NAME` from the environment.

**Missing validation on `TranscribeRequestDTO.engine`**

```python
class TranscribeRequestDTO(BaseModel):
    engine: str = "gemini"  # "gemini" or "whisper"
```

Any string is accepted. An invalid value (e.g. `"openai"`) falls through to `svc = self._transcription_service` silently using Gemini, making the error very hard to diagnose.

---

## 4. New Feature Ideas

**1. Webhook / push-notification integration**
After a summary is saved, POST a structured payload to a configurable URL (Slack incoming webhook, Microsoft Teams connector, Zapier, or a custom endpoint). This would let action items, decisions, and summaries flow into existing team tooling without manual copying.

**2. Speaker identity management**
Currently speakers are labelled generically ("Speaker 1", "Speaker 2"). Add a persistent speaker profile store: the user maps a voice fingerprint (or a name they assign once) to a person, and future transcriptions auto-apply those labels. The transcript editor's existing speaker-rename UI is a good starting point; this would persist and reuse those mappings.

**3. Recurrence detection and series tracking**
For recurring 1:1s detected by the existing `canon_person` logic, automatically group sessions into a series view: a timeline of every meeting with the same person, with cross-meeting search ("what did we agree on about the roadmap across all 2026 Heddy meetings?"). This leverages the existing RAG/vector store infrastructure.

**4. Privacy-aware local transcription pipeline**
Add an option to run the full pipeline (transcription + summarization) fully offline by integrating a local LLM (via Ollama or llama.cpp) alongside the existing Whisper transcription. This is especially valuable for organisations that cannot send audio or text to third-party APIs due to confidentiality policies.

**5. Smart action-item reminder and escalation**
Items approaching their due date (or past it) could be surfaced in the daily email with visual escalation (a plain "due today" badge → a red "overdue N days" badge). Complement this with an optional desktop/mobile push via ntfy or Pushover, configurable per-priority level.

---

## 5. Code Quality Improvements

**Duplicate `_parse_recording_datetime` static method**

The method exists verbatim in both `DashboardController` and `RAGController`. Extract it to a shared `src/utils/recording_utils.py` or a base class.

**`SystemPromptsRepository.get_all` silently drops Italian prompts**

```python
if lang_dir.name != "en":
    continue
```

An entire `system_prompts/it/` subtree is committed but excluded at runtime. This is undocumented, and the Italian prompts are wasted disk space unless the filter is intentional. Either document the reason, make the allowed languages configurable, or remove the Italian prompts.

**`get_config()` is dead code in `depends.py`**

`_config = get_config()` is called in `get_dashboard_controller()` but the variable is never referenced. `os.getenv()` is used directly everywhere else. The function (and the module-level `config = {}`) should be removed to avoid confusion.

**`_DESTINATION_META` in `DashboardController` is incomplete**

Obsidian is a fully-supported publish destination, but it has no entry in `_DESTINATION_META`:

```python
_DESTINATION_META: dict[str, dict] = {
    "notion": {"label": "Notion", "icon": "bi-journal-bookmark"},
}
```

When Obsidian is returned by `get_publish_destinations()`, it falls through to `key.capitalize()` as the label and `bi-share` as the icon.

**Inconsistent error surface: `ValueError` vs `{"ok": False}` dict**

`ActionItemController` raises `ValueError` on not-found (e.g., `raise ValueError("Task not found")`), but the API endpoints do not catch it, so a 500 is returned to the client instead of a 404. Other paths return `{"ok": False, "error": "..."}` dictionaries. Decide on one contract: either raise `HTTPException` directly in the controller, or return result dicts and let the endpoint translate them.

**`import DBSummary` inside a loop in `migrate_action_items.py`**

```python
for summary_row in summary_rows:
    from models.DBSummary import DBSummary
    summary = DBSummary.from_dict(dict(summary_row))
```

Imports inside loops are bad practice and misleading. Move the import to the top of the file.

**`_extract_action_items_from_summary` is a 100-line method with a bare `except`**

```python
except:
    pass
```

This catches `SystemExit`, `KeyboardInterrupt`, and every other exception silently. Convert to `except Exception`, log the error, and extract this logic into a dedicated parser class or service.

**`DBActionItem.task_id` type annotation is wrong**

```python
def __init__(self, id: int | None, task_id: int, ...):
```

`task_id` is typed as `int` but is `None` for summary-extracted and manual items throughout the codebase. The annotation should be `task_id: int | None` to match the actual schema (which allows `NULL`).

**No tests for `ActionItemController`, `CalendarController`, `RAGController`**

The test suite covers `DashboardController`, `NotionService`, `ProactorService`, and repositories well, but the three other controllers have zero unit tests. Given that action-item creation has the hardcoded `summary_id = 75` bug, a test would have caught it immediately.

**`export_team_manager.py` contains committed personal data**

The `ROLE` dictionary encodes specific individuals' names, titles, and team relationships. This should be moved to a separate YAML/JSON config file excluded from version control, or read from environment variables, so the repo can be published without exposing private organisational data.

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
