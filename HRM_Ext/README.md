# HRM Recruitment Assistant (Chrome & Edge Extension)

A Manifest V3 browser extension built for Google Chrome and Microsoft Edge to extract recruitment data from the LTS Group HRM portal (`https://hrm.ltsgroup.tech/recruitment`).

This extension acts as the client-side data extractor for the **HRM_JobMatching** platform, gathering job request specifications and candidate CV attachments to be processed by `HRM_Backend`.

---

## Features

### 1. SPA Route Detection

- Monitors client-side Single Page Application (SPA) navigation.
- Automatically triggers when navigating to a Job Request candidate route:

  ```text
  https://hrm.ltsgroup.tech/recruitment/job-requests/candidate/<jobRequestsId>
  ```

  *(Note: `<jobRequestsId>` is the Job Request ID containing candidate applications).*

### 2. Dual API Data Extraction & Candidate Status

- Automatically reads the authentication Bearer token from `localStorage.auth_tconnect`.
- Concurrently queries both HRM backend endpoints:
  - **Job Request Endpoint**: `GET https://hrm.ltsgroup.tech/api/job-requests/<jobRequestsId>`
  - **Candidates Endpoint**: `GET https://hrm.ltsgroup.tech/api/candidate/candidates/<jobRequestsId>`
- Extracts strictly targeted fields:
  - **Job Request**: `id`, `title`, `code`, `request`, `jobDescription`
  - **Candidates**: `id`, `code`, `name`, `position`, `location`, `status` (e.g. `PM_ROUND`, `OPEN`), `cvs` (PDF URLs), `cvInformation`, `experience`
- **Auto-Sync to Backend**: Automatically transmits extracted job and candidate records to `HRM_Backend` on port `8765` for NLP analysis and precalculated matching.

### 3. Shadow DOM Encapsulated In-Page Widget

- Injects a floating widget encapsulated in an Open Shadow Root (`attachShadow({ mode: 'open' })`) to prevent host page CSS conflicts.
- Visual status indicators conform to design rules (color-coded status dots without generated icons):
  - ⚪ **Gray (Idle)**: Injected and idle on non-candidate pages.
  - 🔵 **Blue (Active)**: Candidate job request route detected.
  - 🟡 **Amber (Loading)**: Actively querying HRM APIs.
  - 🟢 **Green (Success)**: Data loaded successfully, displays candidate count badge on the floating button.
  - 🔴 **Red (Error)**: Network error or authentication missing.

### 4. Multi-Page In-Page Drawer

- **Header Actions**:
  - **Live Backend Sync Badge**: Shows real-time synchronization state with backend (`Synced (X new, Y upd)`, `Sync Error`, or `Backend Ready`).
  - **Resync Button**: Manually triggers synchronization of the current job request and candidates to backend.
  - **Expand Button (`⤢` / `🗗`)**: Toggles normal widget drawer and full-screen workspace view.
  - **Close Button (`×`)**: Closes drawer view.
- **Main Navigation Bar**:
  - **Active Page**: Current HRM job request details, candidate table with colored status badges (`PM_ROUND`, `OPEN`, etc.), spec viewer, and raw JSON inspection.
  - **All Jobs (Matching & Semantic Search)**:
    - Lists all saved jobs from `HRM_Backend` with immediate auto-refresh when a new job is ingested.
    - **Semantic Search**: Natural language search box (e.g. `C++ automotive`, `Python tester`) powered by dense cosine similarity embeddings.
    - Selecting a job displays pre-calculated matching candidates:
      - `Candidate Name` (with CV links)
      - `Status` (color-coded badge)
      - `Matched Experience`
      - `Matching Percentage` (progress bar + percentage)
  - **Search Candidates**: Free-text query prompt to search candidates across all saved records. Selecting a candidate displays pre-calculated matching jobs:
    - `Job Title` & `Code`
    - `Matched Requests`
    - `Matching Percentage` (progress bar + percentage)
  - **Settings**:
    - **Backend Service URL**: Configure backend endpoint (default: `http://localhost:8765`) and test connection health.
    - **Local AI Semantic Model Selection**: Switch between pre-configured local ONNX models (e.g., `BAAI/bge-small-en-v1.5`, `sentence-transformers/all-MiniLM-L6-v2`, `BAAI/bge-base-en-v1.5`).
    - **Database Management**: One-click confirmation to clear all stored database records (`/api/database/clear`).

---

## Extracted Data Schema

The sample reponses:

- HRM_Ext/sample_response_fetch_job-requests.json
- HRM_Ext/sample_response_fetch_candidate_candidates.json

Then the extracted data from all fetches:

```json
{
  "jobRequestId": "79d999b0-449c-4de2-8592-77b34c2191aa",
  "fetchedAt": "2026-09-16T15:30:00.000Z",
  "jobRequest": {
    "id": "79d999b0-449c-4de2-8592-77b34c2191aa",
    "title": "BOSCH - Onsite HCM - Software Developer - Ngôn Ngữ C++",
    "request": "We are looking for some C/C++ Senior Developers from 3+ experience...",
    "jobDescription": "<p><strong>Mandatory</strong></p><ul><li>C++ proficiency...</li></ul>"
  },
  "candidates": [
    {
      "id": "13cf9709-2e40-45ca-a8fa-8cd3f7f65b3f",
      "code": "C10455",
      "name": "Nguyễn Văn Cương",
      "position": "Tester",
      "location": "Ha Noi",
      "cvs": [
        "https://hrm.ltsgroup.tech/api/cvs/nguyen-van-cuong-test-embedded/cvs-19268c47-79a4-4e30-9ec1-bbed78b60359-1789356178960.pdf"
      ]
    }
  ]
}
```

---

## Installation & Setup

1. Open **Google Chrome** or **Microsoft Edge**.
2. Navigate to:
   - Chrome: `chrome://extensions`
   - Edge: `edge://extensions`
3. Enable **Developer mode** toggle in the top-right corner.
4. Click **Load unpacked**.
5. Select the `HRM_Ext` directory:

   ```text
   /path/to/HRM_JobMatching/HRM_Ext
   ```

6. Navigate to `https://hrm.ltsgroup.tech/recruitment` and log in.
7. Open any job request candidate page (e.g. `/recruitment/job-requests/candidate/<jobRequestsId>`) to start automatic data extraction.
8. Click the floating widget (or browser toolbar icon) and click **`⤢`** to expand the drawer to full page and view the candidate table.

---

## Directory Structure

```text
HRM_Ext/
├── README.md              # Extension documentation
├── manifest.json          # Manifest V3 configuration
├── background/
│   └── service-worker.js  # Service worker (navigation & toolbar action listener)
├── content/
│   ├── bridge.js          # Main-world SPA navigation bridge (intercepts pushState/replaceState)
│   ├── content.css        # Scoped styles for Shadow DOM widget & candidate table
│   └── content.js         # Content script (API fetching, full-page drawer & table)
└── icons/                 # Extension icons (16, 48, 64, 128 px)
```

---

## Permissions

- `activeTab`: Allows browser action clicks to communicate with the current active HRM tab.
- `webNavigation`: Directly detects browser-level SPA History state updates (`onHistoryStateUpdated`).
- `tabs`: Allows background service worker to monitor tab URLs across SPA navigations.
- `host_permissions`:
  - `https://hrm.ltsgroup.tech/*`: Authorizes the extension to make authenticated API requests to LTS Group HRM endpoints.
  - `http://localhost:8765/*` and `http://127.0.0.1:8765/*`: Authorizes direct communication with the local `HRM_Backend` service.
