# Agendino

Agendino is a web-based dashboard for managing, transcribing, and summarizing audio recordings from [HiDock](https://www.hidock.com/) USB devices. It connects directly to HiDock H1, H1E, and P1 devices over USB, syncs recordings locally, transcribes them using Google Gemini or locally with Whisper, generates structured AI summaries with customizable system prompts, and optionally publishes results to Notion.

## Features

- **HiDock USB Integration** — Detects and communicates with HiDock H1 / H1E / P1 devices over USB. List, download, and delete recordings directly from the device. View device info and storage usage.
- **Local Recording Management** — Sync recordings from the device to local storage. Browse, play back, and manage `.hda` audio files from the web dashboard.
- **AI Transcription** — Two transcription engines available:
  - **Gemini** (`gemini-2.5-flash`) — Cloud-based transcription with automatic speaker diarization, timestamps, and speaker labels.
  - **Whisper** (local, via [faster-whisper](https://github.com/SYSTRAN/faster-whisper)) — Offline transcription running entirely on your machine. Best for long recordings where Gemini may truncate the output.
- **AI Summarization** — Generate structured summaries (title, tags, and full markdown summary) from transcripts using Gemini. Features include:
  - **Quick Summarize** — One-click summarization with the DefaultSummary prompt for consistent, structured output
  - **Custom Prompts** — Choose from multiple system prompts organized by language and category (e.g. General, Meetings, Education, IT & Engineering)
- **Action Items Management** — Convert meeting tasks into actionable items with:
  - Priority levels (high, medium, low)
  - Status tracking (pending, in_progress, completed)
  - Due dates and assigned persons
  - Batch operations and filtering
  - Archive/unarchive functionality
- **Knowledge Base & RAG** — AI-powered knowledge base with:
  - Vector store for semantic search across all summaries
  - Visual mind maps showing knowledge connections
  - RAG (Retrieval-Augmented Generation) queries using Claude
  - Interactive network visualization of insights
- **Publishing Integrations** — Multiple publishing options:
  - **Notion** — Publish summaries as rich sub-pages with metadata callouts, tags, and formatted markdown
  - **Obsidian** — Export summaries to Obsidian vault with task conversion and auto-commit support
- **Recording Metadata** — Edit titles and tags for any recording. Track transcription and summarization status across device, local, and database records.
- **Web Dashboard** — Multi-page web UI built with FastAPI, Jinja2 templates, and vanilla JavaScript.

## Requirements

- **Python 3.12+**
- A **HiDock** device (H1, H1E, or P1) connected via USB *(optional — local recordings can be managed without a device)*
- A **Google Gemini API key** for transcription and summarization
- An **Anthropic Claude API key** for RAG queries and knowledge base features
- *(Optional)* A **Notion API key** and parent page ID for Notion publishing
- *(Optional)* An **Obsidian vault path** for Obsidian integration

## Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/trentloyd/agendino-fork.git
   cd agendino-fork
   ```

2. **Create and activate a virtual environment:**

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Linux / macOS
   # .venv\Scripts\activate    # Windows
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

   For development (includes `pytest`):

   ```bash
   pip install -r requirements-dev.txt
   ```

4. **USB permissions (Linux only):**

   To access HiDock devices without `sudo`, add a udev rule:

   ```bash
   sudo tee /etc/udev/rules.d/99-hidock.rules <<EOF
   SUBSYSTEM=="usb", ATTR{idVendor}=="10d6", MODE="0666"
   EOF
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```

## Configuration

Create a `.env` file in the project root with the following variables:

```env
# Required — Google Gemini API key for transcription & summarization
GEMINI_API_KEY=your-gemini-api-key

# Required — Anthropic Claude API key for RAG and knowledge base
ANTHROPIC_API_KEY=your-anthropic-api-key

# Optional — Notion integration
NOTION_API_KEY=your-notion-integration-token
NOTION_PAGE_ID=your-notion-parent-page-id

# Optional — Obsidian integration
OBSIDIAN_VAULT_PATH=/path/to/your/obsidian/vault
OBSIDIAN_AUTO_COMMIT_SCRIPT=/path/to/auto-commit-script.sh

# Optional — SQLite database name (default: agendino.db)
DATABASE_NAME=agendino.db

# Optional — Local Whisper transcription settings
# Model size: tiny, base, small (default), medium, large-v3
WHISPER_MODEL_SIZE=small
# Device: cpu (default) or cuda (requires NVIDIA GPU + CUDA toolkit)
WHISPER_DEVICE=cpu
# Compute type: auto (default), int8, float16, float32
WHISPER_COMPUTE_TYPE=auto
```

## Getting Started

1. **Start the server:**

   ```bash
   cd src
   fastapi dev main.py
   ```

   The dashboard will be available at **http://127.0.0.1:8000**.

2. **Open the dashboard** in your browser and you will see the main page.

3. **Connect your HiDock** device via USB. The dashboard will detect it automatically when you interact with the device features.

## Usage

### Syncing Recordings

1. Connect your HiDock device via USB.
2. From the dashboard, click **Sync** to download new recordings from the device to local storage and register them in the database.
3. Recordings already present locally are skipped automatically.

### Transcribing a Recording

1. Select a recording that has been synced locally.
2. Click the **Transcribe** button (microphone icon) to transcribe with Gemini (default), or click the **dropdown arrow** next to it to choose between:
   - **Gemini** — Cloud-based, includes speaker diarization and labels. May truncate very long recordings.
   - **Whisper (local)** — Runs on your machine, no cloud upload. Handles long audio files without truncation. Requires downloading the model on first use (~500 MB for `small`).
3. The transcript is saved to the database and can be viewed or edited at any time.

### Summarizing a Recording

1. Make sure the recording has been transcribed first.
2. Click **Summarize** and choose a **system prompt** from the available categories (e.g. `Generale / SintesiAdattiva`, `IT&Engineering / ...`).
3. Gemini generates a structured JSON response containing a **title**, **tags**, and a **full markdown summary**.
4. The result is saved to the database. You can edit the title and tags afterwards.

### Publishing Summaries

#### Publishing to Notion

1. Ensure `NOTION_API_KEY` and `NOTION_PAGE_ID` are configured in your `.env` file.
2. After summarizing a recording, click **Publish** and select **Notion** as the destination.
3. A new sub-page is created under your configured Notion parent page with the summary content, tags, and recording metadata.
4. The Notion page URL is saved in the database for quick access.

#### Publishing to Obsidian

1. Configure `OBSIDIAN_VAULT_PATH` in your `.env` file to point to your Obsidian vault.
2. Optionally set `OBSIDIAN_AUTO_COMMIT_SCRIPT` for automatic git commits.
3. After summarizing a recording, click **Publish** and select **Obsidian** as the destination.
4. The summary is exported as a markdown file in the `Agendino/` folder within your vault.
5. Action items are automatically converted to Obsidian task checkboxes (`- [ ]`).

### Managing Action Items

1. Navigate to the **Action Items** page from the sidebar.
2. **Creating Action Items**:
   - Action items are automatically created from meeting tasks during summarization
   - You can also manually convert any task to an action item
   - **Create Manual Action Items**: Use the "Create Action Item" button to add action items directly without requiring a meeting or task
3. **Managing Items**:
   - Filter by status (pending, in progress, completed) or priority (low, medium, high)
   - Edit titles, descriptions, due dates, and assignments
   - Use batch operations to update multiple items at once
   - Archive completed items to keep your list organized
4. **Meeting Title Synchronization**:
   - **Sync from Source**: Use the sync button (↻) to update all action items with the current recording/summary title
   - **Manual Rename**: Use the pencil button to rename the meeting title across all related action items
5. **Status Tracking**: Items progress from `pending` → `in_progress` → `completed`

### Using the Knowledge Base

1. Navigate to the **Knowledge Base** page from the sidebar.
2. **Mind Map Visualization**:
   - View an interactive network diagram showing connections between meeting insights
   - Click on nodes to see related summaries and details
   - Use the controls to zoom and navigate the knowledge graph
3. **RAG Queries**:
   - Ask questions about your meetings in natural language
   - The system searches relevant summaries and provides context-aware answers
   - All responses cite their sources from your meeting summaries
4. **Search**: Use the search bar to find specific topics across all your transcripts and summaries

### Deleting Recordings

You can selectively delete a recording from:
- The **HiDock device**
- **Local storage**
- The **database**

Each target is independent — you can, for example, delete from the device while keeping the local file and database record.

### Custom System Prompts

System prompts are stored as `.txt` files under the `system_prompts/` directory, organized by language and category:

```
system_prompts/
  it/
    Generale/
      SintesiAdattiva.txt
      TLDRDirigenziale.txt
      DecisioniERischi.txt
    Riunione/
      SintesiOperativa.txt
      ActionTracker.txt
      RecapCliente.txt
    Istruzione/
      ...
    IT&Engineering/
      VerbaleIT.txt
      PostMortemLeggero.txt
      ...
```

To add a new prompt, create a `.txt` file in the appropriate category folder. It will appear automatically in the prompt selection dropdown.

Recommended prompt-writing guidelines:
- Keep prompts focused on one clear outcome (e.g. executive recap, action tracker, risk register).
- Define a strict output structure (sections and, when useful, table columns).
- Add anti-hallucination constraints (`use only transcript evidence`, `non specificato` for missing fields).
- Prefer concise, actionable language over generic prose.

## API Endpoints

API routes are organized by feature area:

### Core Dashboard (`/api/dashboard`)

| Method   | Endpoint                        | Description                          |
|----------|---------------------------------|--------------------------------------|
| `GET`    | `/api/dashboard/recordings`     | List all recordings with status      |
| `POST`   | `/api/dashboard/sync`           | Sync recordings from device          |
| `GET`    | `/api/dashboard/audio/{name}`   | Stream/download an audio file        |
| `POST`   | `/api/dashboard/transcribe/{name}` | Transcribe a recording            |
| `GET`    | `/api/dashboard/transcript/{name}` | Get stored transcript              |
| `GET`    | `/api/dashboard/prompts`        | List available system prompts        |
| `POST`   | `/api/dashboard/summarize/{name}` | Summarize a recording              |
| `GET`    | `/api/dashboard/summary/{name}` | Get stored summary                   |
| `PATCH`  | `/api/dashboard/recording/{name}` | Update title and tags              |
| `DELETE` | `/api/dashboard/recording/{name}` | Delete recording (device/local/db) |
| `GET`    | `/api/dashboard/share/destinations` | List configured publish targets  |
| `POST`   | `/api/dashboard/share/{name}`   | Publish summary to a destination     |

### Action Items (`/api/action-items`)

| Method   | Endpoint                        | Description                          |
|----------|---------------------------------|--------------------------------------|
| `GET`    | `/api/action-items/`            | List all action items                |
| `POST`   | `/api/action-items/`            | Create new action item from task     |
| `POST`   | `/api/action-items/manual`      | Create manual action item            |
| `GET`    | `/api/action-items/{id}`        | Get specific action item by ID       |
| `PUT`    | `/api/action-items/{id}`        | Update action item                   |
| `DELETE` | `/api/action-items/{id}`        | Delete action item                   |
| `POST`   | `/api/action-items/{id}/archive` | Archive/unarchive action item       |
| `POST`   | `/api/action-items/convert/{task_id}` | Convert task to action item    |
| `POST`   | `/api/recordings/{id}/sync-meeting-title` | Sync meeting title for action items |

### Knowledge Base (`/api/knowledge`)

| Method   | Endpoint                        | Description                          |
|----------|---------------------------------|--------------------------------------|
| `POST`   | `/api/knowledge/query`          | RAG query across knowledge base      |
| `GET`    | `/api/knowledge/mind-map`       | Generate knowledge mind map          |

Interactive API docs are available at **http://127.0.0.1:8000/docs** (Swagger UI).

## Project Structure

```
agendino/
├── src/
│   ├── main.py                          # FastAPI app entrypoint
│   ├── app/
│   │   ├── router.py                    # Top-level router (API + web)
│   │   ├── depends.py                   # Dependency injection / configuration
│   │   ├── api/
│   │   │   ├── api.py                   # API router
│   │   │   └── endpoints/
│   │   │       ├── dashboard.py         # Core recording management
│   │   │       ├── action_items.py      # Action items API
│   │   │       └── knowledge.py         # Knowledge base & RAG API
│   │   └── web/dashboard.py             # HTML dashboard routes
│   ├── controllers/
│   │   ├── DashboardController.py       # Core recording business logic
│   │   ├── ActionItemController.py      # Action items management
│   │   └── RAGController.py             # RAG and knowledge base
│   ├── models/
│   │   ├── DBRecording.py               # Database recording model
│   │   ├── DBActionItem.py              # Action item model
│   │   ├── DBTask.py                    # Task model
│   │   ├── HiDockDevice.py              # USB device communication
│   │   ├── HiDockDeviceInfo.py          # Device info model
│   │   ├── HiDockDevicePacket.py        # USB packet protocol
│   │   ├── HiDockRecording.py           # Device recording model
│   │   └── dto/                         # Data transfer objects
│   │       ├── CreateActionItemDTO.py   # Action item creation
│   │       ├── UpdateActionItemDTO.py   # Action item updates
│   │       └── RAGQueryRequestDTO.py    # RAG query requests
│   ├── repositories/
│   │   ├── LocalRecordingsRepository.py # Local .hda file management
│   │   ├── SqliteDBRepository.py        # SQLite database access
│   │   ├── SystemPromptsRepository.py   # System prompt file loader
│   │   └── VectorStoreRepository.py     # Vector database for RAG
│   ├── services/
│   │   ├── HiDockDeviceService.py       # Device discovery
│   │   ├── TranscriptionService.py      # Gemini transcription
│   │   ├── WhisperTranscriptionService.py # Local Whisper transcription
│   │   ├── SummarizationService.py      # Gemini summarization
│   │   ├── NotionService.py             # Notion API integration
│   │   ├── ObsidianService.py           # Obsidian vault integration
│   │   ├── RAGService.py                # RAG queries and mind mapping
│   │   ├── ClaudeSummarizationService.py # Claude-based summarization
│   │   └── ClaudeTaskGenerationService.py # Task extraction
│   ├── static/                          # CSS & JS assets
│   │   ├── dashboard.css                # Main dashboard styles
│   │   ├── knowledge.css                # Knowledge base styles
│   │   ├── dashboard.js                 # Recording management
│   │   ├── action_items.js              # Action items interface
│   │   └── knowledge.js                 # Knowledge base & RAG
│   └── templates/
│       └── dashboard/                   # Jinja2 HTML templates
│           ├── home.html                # Main recordings dashboard
│           ├── action_items.html        # Action items management
│           ├── calendar.html            # Calendar view
│           ├── proactor.html            # Proactive insights
│           └── knowledge/
│               └── home.html            # Knowledge base interface
├── settings/
│   ├── agendino.db                     # SQLite database
│   └── db_init.sql                      # Database schema
├── local_recordings/                    # Synced .hda audio files
├── system_prompts/                      # Summarization prompt templates
│   └── en/                              # English prompts
├── tests/                               # Unit & integration tests
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

## Running Tests

```bash
pytest
```

## License

This project is for personal use.
