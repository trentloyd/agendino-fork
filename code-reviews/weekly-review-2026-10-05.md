# Weekly Code Review — Agendino

**Date:** 2026-10-05  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**`NotionService.publish_summary` receives an unexpected `tasks` kwarg and always errors**
`DashboardController.publish_summary` (line `result = svc.publish_summary(…, tasks=tasks_list)`) passes `tasks=` to every publish service. `NotionService.publish_summary` has no such parameter, so every Notion publish call raises `TypeError: got an unexpected keyword argument 'tasks'`, which is caught by the bare `except Exception as e:` and returned as `{"ok": False, "error": "Publish failed: …"}`. Publishing to Notion is silently broken.

**Path traversal in `SystemPromptsRepository.get_prompt_content`**
```python
# src/repositories/SystemPromptsRepository.py
prompt_path = self._prompts_dir / f"{prompt_id}.txt"
```
`prompt_id` comes straight from user input (`SummarizeRequestDTO.prompt_id`). A value such as `../../../../etc/passwd` will read an arbitrary file from the filesystem. The path should be validated (e.g., `prompt_path.resolve().is_relative_to(self._prompts_dir.resolve())`).

**Hardcoded `summary_id = 75` in `ActionItemController.create_manual_action_item`**
```python
# src/controllers/ActionItemController.py
summary_id = 75  # Use a known valid summary_id
```
This breaks on any installation that doesn't happen to have summary row 75. Any foreign-key constraint failure from this will surface as a 500 error.

**Non-transactional table migration in `_ensure_action_items_table`**
The `DROP TABLE action_items` / `ALTER TABLE action_items_new RENAME TO action_items` sequence in `SqliteDBRepository._ensure_action_items_table` is not wrapped in a transaction. A crash or interrupt between the two statements permanently destroys all action items. Additionally, `_ensure_action_items_table` is called inside every single action-item DB method (read, write, archive, delete…), running `SELECT sql FROM sqlite_master` on every request.

**Hardcoded paths in `DashboardController._sync_team_manager` and `export_team_manager.py`**
```python
# src/controllers/DashboardController.py
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```
```python
# export_team_manager.py
DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
```
These hard-codes mean the feature is dead on any installation that does not live at `/opt/agendino` and is not the specific author's machine. The DB path should be resolved through `DATABASE_NAME` / `get_root_path()`, and vault path via `OBSIDIAN_VAULT_PATH`.

### High

**XSS in HTML email generation**
`EmailService._format_action_item_html` interpolates `item['title']`, `item['description']`, `item['assigned_to']`, and `item['meeting_title']` directly into the HTML string without escaping. A meeting title of `<img src=x onerror=alert(1)>` stored in the DB would produce a malformed or potentially exploitable email body.

**`SystemPromptsRepository.get_all` silently drops all non-English prompts**
```python
# src/repositories/SystemPromptsRepository.py
if lang_dir.name != "en":
    continue
```
A full directory tree of Italian prompts exists under `system_prompts/it/` but is never returned. The condition was almost certainly intended to restrict to English for a release but was left in accidentally.

**iCal timezone information silently discarded**
`ICalSyncService._to_datetime_str` strips tz-aware datetimes with:
```python
return dt_val.strftime("%Y-%m-%d %H:%M:%S")
```
Events from a UTC or +05:30 calendar are stored as-if they are local time, causing all events from external calendars to appear at the wrong time for anyone not in UTC.

**Bare `except:` in `_extract_action_items_from_summary`**
```python
for fmt in ['%Y-%m-%d', '%m/%d/%Y', ...]:
    try:
        due_date = datetime.strptime(due_date_str, fmt)
        break
    except:          # catches KeyboardInterrupt, SystemExit, …
        continue
```
Should be `except ValueError:`.

**`DBActionItem` constructor type annotation misleads on `task_id`**
The parameter is declared `task_id: int` but is stored as `NULL`-able in the database and `None` in many call sites. `from_dict` will `KeyError` if a plain dict without `task_id` is passed (SQLite `Row` objects return `None` for `NULL`, so production code is safe today, but tests or future code using dicts may not be).

---

## 2. Performance Improvements

**N+1 query pattern in `DashboardController.get_recordings_status`**
For every recording that has a DB record the method issues an additional `get_summaries(bare_name)` call just to count versions:
```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```
`get_latest_summaries_map` is already called. A single `COUNT(*) GROUP BY recording_id` query (or tagging the count into `get_latest_summaries_map`) would eliminate O(n) round trips.

**`_ensure_calendar_tables` and `_ensure_action_items_table` called on every DB method**
Both run `SELECT sql FROM sqlite_master WHERE type='table'` and often execute `CREATE TABLE IF NOT EXISTS` statements on every action-item read and every calendar operation. These checks should be performed once at `SqliteDBRepository.__init__`.

**Full `transcript` column loaded in every `get_recordings()` call**
The listing query fetches the entire transcript blob for all recordings even though the dashboard only needs whether a transcript exists:
```sql
SELECT id, name, label, duration, file_extension, recorded_at, created_at, transcript FROM recording
```
Replace with `LENGTH(transcript) > 0 AS has_transcript` or a separate `transcript IS NOT NULL` flag. Transcripts can be megabytes; loading all of them per dashboard refresh is wasteful.

**`_reconcile_orphan_files()` called on every dashboard load**
`get_recordings_status` calls `_reconcile_orphan_files` synchronously before serving the recordings list. This scans the filesystem and DB on every page load. It should run at startup or as a background task rather than blocking the API response.

**New `SqliteDBRepository` instance per request, no connection reuse**
`depends.py` creates a fresh `SqliteDBRepository` (and all other services) on every FastAPI request. Adding `@lru_cache()` to the factory functions for stateless services, and using a module-level or startup-lifecycle singleton for the repository, would avoid repeated object construction and allow SQLite connection reuse.

**`DashboardController._hydrate_latest_summary_fields` (called from `get_recording_by_name`) makes an extra round trip**
Every `get_recording_by_name` call immediately fires a second query to fetch the latest summary. Consider joining on the summary in the main query, or caching.

---

## 3. Missing Error Handling

**Gemini uploaded file never deleted**
`TranscriptionService.transcribe` uploads an audio file to Gemini Files API but never deletes it:
```python
uploaded = self._client.files.upload(file=path, config=…)
response = self._client.models.generate_content(model=MODEL, contents=[uploaded, …])
# no cleanup of `uploaded`
```
If `generate_content` raises, the file leaks indefinitely. A `try/finally` with `self._client.files.delete(uploaded.name)` is needed.

**`migrate_action_items.py` leaves SQLite connection open on exception**
```python
conn = sqlite3.connect(os.path.join(…, "settings/agendino.db"))
rows = conn.execute("SELECT …").fetchall()
conn.close()  # skipped if execute() raises
```
Should use `with sqlite3.connect(…) as conn:` or a `try/finally`.

**`CalendarController.create_shared_calendar` silently ignores sync failure**
```python
try:
    self._do_sync_calendar(saved)
except Exception:
    pass  # Don't fail creation if initial sync fails
```
The failure is completely hidden from the caller. At minimum the caught error should be logged, and ideally the response should include a `sync_warning` field.

**No timeout on Anthropic Claude API calls**
`ClaudeSummarizationService.summarize`, `ClaudeTaskGenerationService.generate_tasks`, and `RAGService.ask` all call `self._client.messages.create(…)` without a `timeout` parameter. A hung API call will tie up a worker thread indefinitely.

**`ObsidianService.publish_summary` — auto-commit script failure ignored**
```python
result = subprocess.run(['/bin/bash', self.auto_commit_script], check=False, …)
```
`check=False` means a non-zero return code is silently ignored. At minimum the return code should be logged.

**`SqliteDBRepository.save_summarization_result` raises `ValueError` for missing recording**
A recording not found surfaces as an unhandled `ValueError` from inside the repository, which FastAPI would turn into an unstructured 500. The controller should receive a `None` or a typed exception so it can return a proper 404.

**Missing retry / back-off for iCal sync**
`ICalSyncService.fetch_and_parse` makes a single HTTP GET with no retry. Transient network errors cause the entire sync to fail and record a persistent `last_error`. A simple exponential back-off with 2-3 retries would improve reliability.

---

## 4. New Feature Ideas

**1. Asynchronous transcription queue with progress streaming**
Transcription via Gemini can take minutes. Currently, the HTTP request blocks until the response returns. A task queue backed by FastAPI `BackgroundTasks` (or a simple asyncio queue) would allow the endpoint to return a `job_id` immediately, with the client polling `/api/dashboard/transcription-status/{job_id}`. Server-Sent Events could stream progress to the UI without polling.

**2. Full-text search across transcripts and summaries**
The current semantic/RAG search requires loading into ChromaDB first and relies on embedding similarity. A SQLite FTS5 virtual table (`CREATE VIRTUAL TABLE transcript_fts USING fts5(…)`) would allow instant keyword search without requiring the vector store to be populated, giving users a fast fallback.

**3. Recording annotation and tagging timeline**
Allow users to mark specific timestamps in a transcript with notes (e.g., "key decision at 04:32"). These annotations would be stored per-recording and displayed as markers in the audio player, turning the audio playback into a navigable document.

**4. Configurable action item auto-extraction rules**
Currently action items are only extracted when the `DefaultSummary` prompt is used and the output matches a specific table format. Expose a settings page where users can define custom extraction patterns (e.g., regex or keyword rules like "ACTION:", "TODO:", "→") that work across all prompt outputs, making the feature prompt-agnostic.

**5. Recurring summary digest**
Beyond the existing daily email of action items, provide a weekly or monthly "digest" email (or Notion/Obsidian page) that aggregates all summaries from the period—grouped by tag or meeting type—with a high-level overview of recurring themes, decisions, and outstanding action items, generated by another Claude call over the summaries map.

---

## 5. Code Quality Improvements

**Duplicated `_parse_recording_datetime` static method**
An identical implementation appears in both `DashboardController` and `RAGController`. It should be extracted into a shared `src/utils/recording.py` helper and imported by both.

**`depends.py` creates a fresh service graph on every request**
All factory functions (`get_dashboard_controller`, `get_rag_controller`, etc.) instantiate new objects on every HTTP request. Heavy objects like `WhisperTranscriptionService` (lazy-loads a ~500 MB model), `VectorStoreRepository` (opens a ChromaDB client), and `SqliteDBRepository` should be singletons. Apply `@lru_cache()` to the stateless factories or use FastAPI's lifespan hooks.

**Mixing `print()` and `logging` throughout**
`DashboardController._create_action_item_from_task` uses `print(f"Warning: …")`. `SqliteDBRepository._ensure_action_items_table` uses `print("Updating action_items table schema…")`. These appear in a web-server process where `print()` goes to stdout and is invisible to structured log aggregators. All diagnostic output should use `logging`.

**Duplicate SQL schema definition**
The `calendar_event`, `shared_calendar`, `recording_event_link`, and `daily_recap` tables are defined in both `settings/db_init.sql` (authoritative) and inline in `SqliteDBRepository._ensure_calendar_tables`. Divergence between the two definitions would cause silent schema drift. `_ensure_calendar_tables` should be replaced by the same `CREATE TABLE IF NOT EXISTS` idiom driven from the SQL file, called once at init.

**`SystemPromptsRepository.get_all` silently skips all non-English languages**
The filter `if lang_dir.name != "en": continue` is buried inside the method body with no comment. Since Italian prompts do exist and are shown in the README, this appears to be an oversight rather than intended policy. Either the Italian directory should be removed or the filter should be removed.

**`ActionItemController.create_manual_action_item` logic is incorrect by design**
Beyond the hardcoded ID (see Bug section), the comment "Use the most recent recording_id" and subsequent fallback logic shows that manual action items were shoehorned into a schema that requires both `recording_id` and `summary_id` FK references. The table schema should allow these to be NULL for truly manual items, which requires removing or relaxing the NOT NULL constraint in the DB.

**Zero test coverage for action items, email, and calendar subsystems**
The test suite covers `DashboardController`, `LocalRecordingsRepository`, `SqliteDBRepository` (basic CRUD), `NotionService`, `SummarizationService`, and `ProactorService`. There are no tests for `ActionItemController`, `CalendarController`, `EmailService`, `ClaudeSummarizationService`, `ICalSyncService`, or the vector store. These are the most recently added and most likely to have regressions.

**`src/templates/dashboard/main_template.html` is an empty file**
The file exists in the template directory and would confuse Jinja2 template loading if referenced. It should be removed or filled in.

**`DashboardController.get_summary` has dead compatibility code**
```python
db_rec = self._sqlite_db_repository.get_recording_by_name(bare_name)
if db_rec and db_rec.summary:  # legacy path, summary now always lives in summary table
    …
```
`recording.summary` is a compatibility shim hydrated from the summary table anyway, so this branch never produces different results from the fallback `get_summaries()` call below it. The dead branch and the comment-free dual-path makes the method harder to reason about.

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
