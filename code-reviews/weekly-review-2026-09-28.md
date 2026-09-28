# Weekly Code Review — Agendino

**Date:** 2026-09-28  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**`tasks=tasks_list` passed to `NotionService.publish_summary` causes `TypeError`** (`DashboardController.py`, `publish_summary`)
`NotionService.publish_summary` accepts `(title, summary_markdown, tags, recording_name)` — no `tasks` parameter. Every Notion publish will crash with an unhandled `TypeError`. `ObsidianService` accepts `tasks`; Notion does not.

```python
result = svc.publish_summary(
    ...
    tasks=tasks_list,  # *** Pass tasks to Obsidian ***  ← TypeError for Notion
)
```

**Hardcoded `summary_id = 75` in `create_manual_action_item`** (`ActionItemController.py`)
```python
summary_id = 75  # Use a known valid summary_id
```
On any fresh installation (or after a database reset), summary 75 won't exist and the `FOREIGN KEY` constraint on `action_items.summary_id` will raise an `IntegrityError` for every manual action item creation. This should be derived from `recording_id` or made nullable.

**Path traversal in system prompt loading** (`SystemPromptsRepository.get_prompt_content`)
```python
prompt_path = self._prompts_dir / f"{prompt_id}.txt"
```
`prompt_id` comes directly from the API request (`POST /api/dashboard/summarize/{name}` with `body.prompt_id`). A value like `../../.env` or `../../settings/agendino.db` would escape the prompts directory. There is no check that the resolved path is within `_prompts_dir`.

**Hardcoded absolute paths break every non-`/opt/agendino` deployment** (`DashboardController._sync_team_manager`, `export_team_manager.py`)
```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
```python
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
The exporter silently returns without doing anything (`os.path.exists(exporter)` is False) in standard installs, and the log open fails if the directory doesn't exist, surfacing as a generic exception.

**`_ensure_action_items_table` schema detection via string-match is fragile and non-atomic** (`SqliteDBRepository.py`)
```python
if existing_schema and 'task_id INTEGER NOT NULL' in existing_schema[0]:
```
The schema string depends on exact SQL formatting. If the table was created with minor whitespace differences, the migration doesn't fire (or misfires). The `DROP TABLE` + `RENAME` sequence is also not wrapped in a single transaction, leaving a window where both old and new tables could be absent.

**`_ensure_recording_columns` and `_ensure_calendar_tables` execute DDL on every request** (`SqliteDBRepository.py`)
These migration helpers are called before every calendar or action-item operation. In a busy app this runs `CREATE TABLE IF NOT EXISTS` and probe `SELECT` queries thousands of times. They should run once at startup.

### Moderate

**Timezone information silently dropped on iCal sync** (`ICalSyncService._to_datetime_str`)
```python
# Strip timezone info for naive storage
return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
An event at `15:00 UTC+3` is stored as `15:00` — not `12:00`. Users in different timezones will see incorrect event times with no warning.

**Bare `except:` in `_extract_action_items_from_summary`** (`DashboardController.py`)
```python
try:
    due_date = datetime.strptime(due_date_str, fmt)
    break
except:
    continue
```
A bare `except` swallows `KeyboardInterrupt`, `SystemExit`, and `MemoryError`. Should be `except ValueError:`.

**XSS in generated HTML emails** (`EmailService._format_action_item_html`)
Action item fields (title, description, meeting_title, assigned_to) are interpolated directly into the HTML email body without HTML escaping. A malicious title containing `<script>` or `<img onerror=...>` would execute in HTML-capable email clients.

**`publish_recording` (legacy route) ignores the new multi-summary model** (`DashboardController.py`)
It checks `db_rec.summary` (a compatibility field on the recording object) but passes `summaries[0].id` to `publish_summary`. If the recording has no summary rows, the error message says "No summary found" but the actual guard is `not db_rec` — a recording without a summary would pass the first guard and crash on `summaries[0]`.

**`get_config()` mutates a module-level dict on every call** (`depends.py`)
```python
config = {}
def get_config():
    for item in os.environ.items():
        config[item[0]] = item[1]
    return config
```
Not thread-safe. The `_config = get_config()` result in `get_dashboard_controller()` is never actually used by the constructor.

---

## 2. Performance Improvements

**N+1 queries in `get_recordings`** (`SqliteDBRepository.py`)
`_hydrate_latest_summary_fields` fires one SQL query per recording. With 200 recordings that's 200 + 1 queries per dashboard load. Fix with a single JOIN using `get_latest_summaries_map()` (which already exists) applied after the bulk fetch.

**Another N+1 in `get_recordings_status`** (`DashboardController.py`)
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
One `get_summaries` call per recording. Should be replaced with a single `SELECT recording_id, COUNT(*) FROM summary GROUP BY recording_id` query.

**`sync_meeting_titles` fetches all recordings to find one by ID** (`ActionItemController.py`)
```python
recordings = self.db_repo.get_recordings()
recording = next((r for r in recordings if r.id == recording_id), None)
```
`db_repo.get_recording_by_id()` already exists and is used elsewhere. This is a full table scan in Python instead of a keyed DB lookup.

**New service/repository instances on every request** (`depends.py`)
Every HTTP request instantiates new `SqliteDBRepository`, `TranscriptionService`, `ClaudeSummarizationService`, `VectorStoreRepository`, etc. The Anthropic and Gemini clients perform HTTP setup per instantiation, and `VectorStoreRepository` reopens the ChromaDB persistent client. Use `@lru_cache()` on the factory functions or FastAPI's `Depends` with `use_cache=True`.

**`_reconcile_orphan_files` runs a filesystem scan on every dashboard load** (`DashboardController.get_recordings_status`)
`os.listdir` + potential `os.rename` on every `GET /api/dashboard/recordings` call. This should be triggered on demand (e.g., after a sync) or on a schedule, not on every read.

**`get_recordings_for_day` loads entire `recording` table** (`SqliteDBRepository.py`)
```python
rows = conn.execute("SELECT id, name, label, duration, recorded_at, transcript FROM recording").fetchall()
```
All recordings are fetched and date-filtered in Python. Should filter by `recorded_at` in SQL, with an index on that column.

**Transcript chunking discards most content** (`RAGController.load_transcripts`)
```python
chunks = [transcript_text[i:i+4000] for i in range(0, len(transcript_text), 3000)]
transcript_text = chunks[0]
if len(chunks) > 1:
    transcript_text += "..." + chunks[1][:1000]
```
For a 50,000-character transcript (≈1h meeting), only ≈5,000 characters are indexed. The remaining ~90% is silently discarded, making deep search nearly useless on long meetings.

---

## 3. Missing Error Handling

**No file upload size limit** (`dashboard.py`, `upload_recording` endpoint)
```python
file_data = await file.read()
```
No `Content-Length` check or stream limit. An attacker can upload multi-gigabyte files, exhausting memory and disk. FastAPI itself does not impose a default limit.

**`ValueError` from `create_manual_action_item` is not caught by the API endpoint** (`action_items.py`)
```python
async def create_manual_action_item(request, action_item_controller):
    return action_item_controller.create_manual_action_item(request)
```
If the controller raises `ValueError("No recordings available...")`, FastAPI converts it to a 500 Internal Server Error with a stack trace exposed in the response rather than a 400/422.

**Same pattern in `create_action_item`, `convert_task_to_action_item`, `sync_meeting_titles`** — all `ValueError` raises from the controller surface as 500s.

**`_sync_team_manager` subprocess is fully fire-and-forget with no failure tracking** (`DashboardController.py`)
The `Popen` is launched detached; any error (exporter crash, commit failure) is only in the log file. There is no mechanism to surface this failure to the user or retry.

**`auto_commit.py` log rotation breaks the active log handler**
When `log_file.rename(backup_name)` moves the file, the `FileHandler` still holds the old file descriptor open. Subsequent log messages are written to the renamed (backup) file, not to `auto_commit.log`. The handler is never re-opened.

**`_get_audio_duration` silently returns 0 on all errors** (`DashboardController.py`)
A corrupt file, a missing `mutagen` dependency, or an unsupported codec all produce `duration=0` with no log message, making diagnostic of missing durations difficult.

**`DailyNotificationService.start_scheduler` has no upper bound on scheduling failures**
If `send_daily_action_items` fails (SMTP unreachable), the exception is logged and the scheduler continues running. There is no alerting, dead-letter queue, or back-off. Failed sends are silently dropped.

**Missing null check before accessing `response.candidates[0]`** (`SummarizationService.summarize`)
```python
finish_reason = response.candidates[0].finish_reason
```
An empty candidates list (e.g., content filtered by Gemini safety) raises `IndexError`, not caught by the `except (IndexError, AttributeError)` guard — wait, `IndexError` is caught. But it's caught with `pass`, silently ignoring the truncation flag rather than logging a warning.

**`migrate_action_items.py` opens a raw `sqlite3` connection inside a try block but closes it unconditionally**
```python
conn = sqlite3.connect(...)
conn.row_factory = sqlite3.Row
rows = conn.execute(...).fetchall()
conn.close()
```
If `.execute()` or `.fetchall()` raises, `conn.close()` is skipped and the connection leaks.

---

## 4. New Feature Ideas

1. **Background task queue for transcription and summarization.** Currently, transcription blocks the HTTP request for the full duration (minutes for long recordings). Integrate FastAPI's `BackgroundTasks` or Celery/RQ to queue these jobs, return a job ID immediately, and let the frontend poll for completion. Add a status indicator per recording.

2. **Recording search and filtering.** The recordings dashboard shows all recordings in reverse-chronological order with no way to filter. Add server-side filtering by date range, transcription/summary status, file type, label, and full-text search across transcripts and summary titles. This would make the tool practical with hundreds of recordings.

3. **Webhook / export API for integrations.** Add a configurable outbound webhook that fires after each summarization (with title, tags, summary text, and action items as JSON payload). This enables integration with Zapier, n8n, Slack, or any custom pipeline without modifying Agendino's code.

4. **Summary version diffing and rollback.** The data model already stores multiple summary versions per recording (`summary.version`). The UI shows all versions but doesn't highlight what changed between them. Add a side-by-side or inline diff view, and a "restore this version" action that creates a new version with the old content.

5. **Recurring meeting detection and longitudinal view.** Given the 1:1 tracking feature already present, extend it to any recurring meeting pattern. Detect recordings with similar names across weeks, group them, and show a timeline view: how did action item completion rates trend, which topics recurred, and what decisions were revisited.

---

## 5. Code Quality Improvements

**`action_items` table absent from `db_init.sql`**
Every other table is declared in `settings/db_init.sql` and created at init time. The `action_items` table is created on first use by `_ensure_action_items_table()`. This inconsistency means `db_init.sql` is not a complete schema definition, complicates migrations, and causes the first action-item call to do DDL work. Move the table definition to `db_init.sql`.

**Duplicate `_parse_recording_datetime` static method** appears verbatim in both `DashboardController` and `RAGController`. Extract to a shared utility module.

**`get_dashboard_controller` ignores its own config call** (`depends.py`)
```python
def get_dashboard_controller() -> DashboardController:
    _config = get_config()   # result never used
    return DashboardController(...)
```
Dead code that also has a side effect (mutating the global `config` dict). Remove it.

**`ClaudeSummarizationService` is type-hinted as `SummarizationService` throughout** (`DashboardController.__init__`, `depends.get_summarization_service`)
`depends.py` returns `ClaudeSummarizationService` where `SummarizationService` (Gemini) is declared. The two classes have incompatible constructor signatures and `summarize()` parameter lists. The type annotation is misleading and mypy/pyright would flag this.

**Magic string `"en/General/DefaultSummary"` repeated in two call sites** (`DashboardController.summarize_recording`)
```python
if prompt_id == "en/General/DefaultSummary" and recording:
    self._extract_action_items_from_summary(...)
```
And in `dashboard.js`:
```python
body: JSON.stringify({ prompt_id: "en/General/DefaultSummary" }),
```
Should be a named constant, ideally surfaced via the prompts API so the frontend doesn't need to hardcode it.

**`main_template.html` is an empty file** (`src/templates/dashboard/main_template.html`)
An empty template file with no content. Either remove it or document its purpose.

**`NONPERSON` set in `export_team_manager.py` contains a real person's first name with an explanatory comment**
```python
"Frank",  # Frank = interview candidate, not a report
```
Personal data (even just a first name tied to a professional role) should not be committed to source code.

**`requirements.txt` has no version pins**
All dependencies (`fastapi[standard]`, `chromadb`, `anthropic`, etc.) are unpinned. A `pip install` six months from now will pull different versions, potentially breaking the app. Pin at least major versions; add a `requirements-lock.txt` or use `pip-tools`.

**Inconsistent HTTP error handling across the API surface**
Dashboard endpoints return `{"ok": False, "error": "..."}` with HTTP 200. Action-items endpoints raise `HTTPException` with proper 404/500 codes. This means API consumers cannot rely on HTTP status codes for error detection — they must inspect the response body. Pick one convention and apply it consistently.

**No unit tests for `ActionItemController`, `CalendarController`, `RAGController`, or any service beyond `NotionService`, `ProactorService`, and `SummarizationService`**
The `ClaudeSummarizationService`, `ClaudeTaskGenerationService`, `RAGService`, `ObsidianService`, `EmailService`, and `DailyNotificationService` have zero test coverage. At minimum, the JSON parsing and error-path logic in these services should have unit tests similar to those for `SummarizationService._parse_response`.

**`_extract_action_items_from_summary` is 80+ lines of ad-hoc markdown parsing** (`DashboardController.py`)
The same markdown table parsing logic exists in `export_team_manager.py` (`parse_action_items`). Consolidate into a shared parser, add unit tests, and cover edge cases (tables with missing columns, bullet-style fallback, empty rows). Currently the two implementations handle the same format differently.

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
