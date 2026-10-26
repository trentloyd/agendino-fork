# Weekly Code Review — Agendino

**Date:** 2026-10-26  
**Model:** `claude-sonnet-4-6`  
**Files reviewed:** 158

> **Note:** some files were omitted from this review:
>
> - `src/static/style.css (too large: 354 KB)`

---

## 1. Potential Bugs & Issues

### Critical

**`summary_id = 75` hardcoded in `ActionItemController.create_manual_action_item`** (`src/controllers/ActionItemController.py`, around line 95)

```python
# We know summary IDs 73, 74, 75 exist from our earlier check
summary_id = 75  # Use a known valid summary_id
```

This hardcoded foreign key will immediately cause an SQLite FK violation (`FOREIGN KEY constraint failed`) on any installation that isn't the developer's own. Manual action items are completely broken for new users.

**No authentication or authorization** — Every API endpoint is fully public. Any process on the same network can read all transcripts, summaries, and recordings, trigger transcription/summarization (incurring API costs), or delete data. There is no API key, session, or any other guard.

**Path traversal in the audio endpoint** (`src/app/api/endpoints/dashboard.py` → `DashboardController.get_audio_file_path`): `name` comes directly from the URL (`/api/dashboard/audio/{name}`) and is passed through `os.path.join(local_recordings_path, filename)` after only stripping a known audio extension. A request like `GET /api/dashboard/audio/../../etc/passwd` (with the `.hda` extension appended by `_resolve_local_filename`) could serve files outside the recordings directory.

### High

**`_ensure_action_items_table` drops and recreates the table without a wrapping transaction** (`src/repositories/SqliteDBRepository.py`). The sequence is `DROP TABLE action_items` → `CREATE TABLE action_items_new` → `INSERT … SELECT *` → `DROP TABLE action_items` → `RENAME`. If any step fails (or a concurrent request hits it), data can be permanently lost. FastAPI processes concurrent requests, making this a real risk.

**Wrong return type on `get_summarization_service()`** (`src/app/depends.py`):

```python
def get_summarization_service() -> SummarizationService:
    return ClaudeSummarizationService(api_key=os.getenv("ANTHROPIC_API_KEY"))
```

`ClaudeSummarizationService` is not a subclass of `SummarizationService`; they are entirely separate classes. The `DashboardController` constructor is typed to accept `SummarizationService`, so mypy and static analysis will incorrectly validate call sites.

**Hardcoded `/opt/agendino/` paths in `_sync_team_manager`** (`src/controllers/DashboardController.py`):

```python
exporter = "/opt/agendino/export_team_manager.py"
with open("/opt/agendino/team_manager_sync.log", "a") as logf:
```

This silently no-ops on every `summarize_recording` call outside the production server. The `os.path.exists(exporter)` guard hides the failure, but the log file open will throw `FileNotFoundError` on any other machine, crashing the silent background path.

**iCal timezone stripping** (`src/services/ICalSyncService.py`, `_to_datetime_str`):

```python
if isinstance(dt_val, datetime):
    return dt_val.strftime("%Y-%m-%d %H:%M:%S")  # strips tzinfo
```

Events in non-UTC timezones (e.g., a US Eastern calendar showing 2pm will be stored as `14:00:00` instead of the UTC-equivalent `19:00:00`). No warning is emitted.

**`DBActionItem.from_dict` treats `task_id` as required** (`src/models/DBActionItem.py`):

```python
task_id=data["task_id"],  # KeyError if absent
```

The DB schema allows `task_id NULL` (for manually created items), but a missing key raises `KeyError` when hydrating rows without a task.

### Medium

**Mutable global `config` dict in `depends.py`**:

```python
config = {}
def get_config():
    items = os.environ.items()
    for item in items:
        config[item[0]] = item[1]
    return config
```

This appends to a module-level dict on every request but the returned value is assigned to `_config` and never read. It's both dead code and an unintentional global accumulator.

**Obsidian publish destination missing from `_DESTINATION_META`** (`DashboardController`): The dict only has `"notion"`, so Obsidian shares appear with the generic `"bi-share"` icon and the key capitalized as the label, rather than a proper display name.

---

## 2. Performance Improvements

**N+1 query in `get_recordings_status`** (`src/controllers/DashboardController.py`): For every recording returned, the controller independently counts summary versions:

```python
"summary_count": len(self._sqlite_db_repository.get_summaries(bare_name)) if db_rec else 0,
```

With 100 recordings this fires 100 extra SQL queries. A single `GROUP BY recording_id` query to fetch version counts should be added to `get_latest_summaries_map` or a new helper.

**`_reconcile_orphan_files()` runs on every dashboard page load** — It scans the entire `local_recordings/` directory and fetches all DB recordings on every call to `get_recordings_status`. This should be a background job or triggered explicitly, not inline with every list request.

**`_ensure_action_items_table` and `_ensure_calendar_tables` query `sqlite_master` on every single CRUD operation** — Both methods run schema introspection SQL (`SELECT sql FROM sqlite_master`) before each `get_all_action_items`, `create_action_item`, etc. The `action_items` table should be created during `_initialize_db` (it is absent from `settings/db_init.sql`) so these guards are unnecessary at runtime.

**Per-request `SqliteDBRepository` instantiation** (`src/app/depends.py`) — `get_sqlite_db_repository()` creates a fresh instance (and implicitly a new SQLite connection) on every API request. SQLite connections are cheap but the migration-check logic in `_ensure_recording_columns` and `_ensure_action_items_table` multiplies across requests. A module-level singleton or FastAPI `lifespan` initialization would eliminate this.

**`_hydrate_latest_summary_fields` is called in a loop** (`get_recordings`): The method executes one SQL query per recording to hydrate title/tags/summary, making `get_recordings()` O(N) in DB round-trips. This should be a single JOIN query.

**Gemini embedding API call on every vector search** (`VectorStoreRepository.search`): `self._embed([query])` makes an outbound HTTPS call before each RAG query or semantic search. A small in-memory LRU cache keyed on the query string would eliminate redundant API calls for repeated or similar queries.

---

## 3. Missing Error Handling

**Uploaded Gemini file is never deleted on transcription failure** (`src/services/TranscriptionService.py`):

```python
uploaded = self._client.files.upload(...)
response = self._client.models.generate_content(...)
return response.text
```

If `generate_content` raises, `uploaded` remains on Gemini's file storage indefinitely. There should be a `try/finally` block calling `self._client.files.delete(uploaded.name)`.

**`NotionService._append_blocks` silently swallows all HTTP errors**:

```python
except httpx.HTTPError:
    pass
```

Summaries longer than 100 blocks are silently truncated in Notion with no indication to the caller or the user.

**`SqliteDBRepository._initialize_db` leaks the connection on exception**:

```python
conn = self._connect()
conn.executescript(sql)
conn.commit()
conn.close()  # never reached if executescript raises
```

Should use `try/finally` (or a context manager) to guarantee `conn.close()`.

**`EmailService.send_daily_action_items` and `test_connection` have no socket timeout** — `smtplib.SMTP(self.smtp_server, self.smtp_port)` uses the global default socket timeout (often `None` = infinite). A hung SMTP server will block the asyncio event loop indefinitely.

**Bare `except:` in `_extract_action_items_from_summary`** (`DashboardController.py`):

```python
try:
    due_date = datetime.strptime(due_date_str, fmt)
    break
except:
    continue
```

`except:` with no exception type catches `SystemExit`, `KeyboardInterrupt`, and `GeneratorExit`. Should be `except ValueError:`.

**`get_daily_recap_service()` silently discards all initialization errors** (`depends.py`):

```python
try:
    return DailyRecapService(api_key=key)
except Exception:
    return None
```

Any misconfiguration (wrong key format, import error) returns `None` with no log message, making the feature invisibly absent.

**`DailyNotificationService.start_scheduler` sleeps for potentially days** with a single `asyncio.sleep(sleep_seconds)`. If the process is long-running and the system clock is adjusted (NTP, DST), the next notification time will be wrong with no recovery.

**`WhisperTranscriptionService.transcribe` consumes a generator with no partial-failure handling** — `faster_whisper.WhisperModel.transcribe` returns a lazy generator. If the generator raises mid-stream (e.g., corrupted audio), any already-iterated segments are silently discarded and the error propagates up as if nothing was transcribed.

---

## 4. New Feature Ideas

**1. Server-Sent Events (SSE) for long-running operations** — Transcription and summarization can take 30–120 seconds. Currently the browser hangs on a blocking `fetch`. An SSE or WebSocket endpoint (`/api/dashboard/transcribe/{name}/stream`) that pushes progress updates ("uploaded", "transcribing", "done") would dramatically improve UX without requiring a full job queue.

**2. Full-text search with SQLite FTS5** — The project has semantic search via ChromaDB/Gemini embeddings, but keyword search (e.g., "find all recordings mentioning 'budget Q3'") is missing. SQLite's built-in FTS5 extension can index transcripts and summaries with zero additional dependencies and sub-millisecond query latency, complementing the vector store for exact-term lookups.

**3. Batch transcription and summarization queue** — Users with many recordings must click through each one individually. A queue UI that accepts a list of recording names and a target prompt, then processes them sequentially with per-item status feedback, would enable bulk processing of an entire archive. This is architecturally straightforward since the individual endpoints already exist.

**4. Speaker identification training / name mapping persistence** — The transcript editor already has speaker-rename functionality in the UI, but renames are not persisted to a speaker dictionary. A speaker alias table (`speaker_alias: raw_label → display_name, recording_id`) would allow the UI to pre-populate speaker names on future recordings from the same device (e.g., "Speaker 1" → "Alice" learned from previous sessions).

**5. Configurable action item schema** — The `action_items` table and the `_extract_action_items_from_summary` parser hardcode a specific table structure (`Owner | Due Date | Priority | Dependencies | Status | Source`). Exposing the column mapping as part of the system prompt configuration would let users define custom action item formats per prompt (e.g., engineering prompts with a "Story Points" column, HR prompts with a "Hiring Stage" column).

---

## 5. Code Quality Improvements

**`get_config()` and its global `config` dict are dead code** (`src/app/depends.py`): The function is called only once (`_config = get_config()`) and `_config` is never read. The module-level `config = {}` dict accumulates environment variables on every request with no consumer. Both should be removed.

**`SystemPromptsRepository.get_all` silently discards all non-English prompts** (`src/repositories/SystemPromptsRepository.py`):

```python
if lang_dir.name != "en":
    continue
```

The project ships a full `system_prompts/it/` directory tree (the largest in the repo) that is completely unreachable through the UI. This filter should be removed or made a configuration option.

**`_parse_recording_datetime` is duplicated** in `DashboardController` and `RAGController` (identical implementations). It should live in a shared utility module.

**`action_items` table is absent from `settings/db_init.sql`** — Unlike every other table, `action_items` is created lazily via `_ensure_action_items_table()` on first use, which includes a schema migration (`DROP TABLE / RENAME`). This means fresh installs start with no table, concurrent first requests race on creation, and the schema is split across two files. It should be added to `db_init.sql` with the correct nullable `task_id` column from the start.

**No tests for `ActionItemController`, `CalendarController`, `RAGController`, or any service integration** — `tests/unit/controllers/` contains only `test_DashboardController.py`. The `ActionItemController` in particular has a critical hardcoded-ID bug (see §1) that a simple unit test would have caught.

**`renameMeetingTitle` in `action_items.js` uses `window.prompt()`**:

```javascript
const newTitle = prompt('Enter new meeting title:', currentTitle);
```

`window.prompt()` is synchronous, blocks the tab, is unstyled, and is suppressed in cross-origin iframes and some browsers. This should be replaced with the existing `editModal` pattern already present in the same file.

**`export_team_manager.py` hardcodes personal roster data and specific recording IDs** — The `ROLE` dict (37 entries with real names, titles, and relationships), `ALIAS`, `NONPERSON`, `ME`, and the preview recording IDs `(235, 227, 230)` are all hardcoded. This means the file cannot be reused by anyone else and contains personally identifiable information in the source code. These should be driven by database records or a separate configuration file.

**`_DESTINATION_META` in `DashboardController` is missing Obsidian**:

```python
_DESTINATION_META: dict[str, dict] = {
    "notion": {"label": "Notion", "icon": "bi-journal-bookmark"},
    # "obsidian" is absent
}
```

`get_publish_destinations()` iterates `self._publish_services`, which includes Obsidian, but falls back to `key.capitalize()` and `"bi-share"` for unknown keys. The share modal will show "Obsidian" with a generic icon rather than the intended branding.

**Inconsistent API response style** — Some endpoints return `{"ok": True/False, ...}` dicts (dashboard, knowledge, calendar) while others raise `HTTPException` directly (action-items, notifications). A middleware or base response model would enforce consistency and simplify frontend error handling.

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
