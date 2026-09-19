# HRM Recruitment Assistant (Chrome & Edge Extension)

A Manifest V3 browser extension built for Google Chrome and Microsoft Edge to extract recruitment data from the LTS Group HRM portal (`https://hrm.ltsgroup.tech/recruitment`).

This extension acts as the client-side data extractor and interactive recruitment intelligence workstation for the **HRM_JobMatching** platform, gathering job request specifications and candidate CV attachments to be processed and matched by `HRM_Backend`.

---

## Features

### 1. Robust Triple-Layer SPA Navigation (State Stuck Fix)

- Prevents UI state freeze across client-side Single Page Application (SPA) transitions via a 3-layer architecture:
  1. **Background Service Worker (`service-worker.js`)**: Listens to browser-level events via `chrome.webNavigation.onHistoryStateUpdated` and `chrome.tabs.onUpdated`.
  2. **Main-World Bridge (`bridge.js`)**: Intercepts `window.history.pushState` and `window.history.replaceState` in the page context, dispatching custom `hrm-spa-navigate` events.
  3. **Content Script Observer (`content.js`)**: Employs URL change polling and DOM mutation observation to guarantee state recovery during rapid route switching or back/forward navigation.
- Automatically triggers extraction when navigating to a Job Request candidate route:

  ```text
  https://hrm.ltsgroup.tech/recruitment/job-requests/candidate/<jobRequestsId>
  ```

### 2. Dual API Data Extraction & Multi-Job Candidate Applications

- Automatically reads the authentication Bearer token from `localStorage.auth_tconnect`.
- Concurrently queries both HRM backend endpoints:
  - **Job Request Endpoint**: `GET https://hrm.ltsgroup.tech/api/job-requests/<jobRequestsId>`
  - **Candidates Endpoint**: `GET https://hrm.ltsgroup.tech/api/candidate/candidates/<jobRequestsId>`
- Extracts strictly targeted fields:
  - **Job Request**: `id`, `title`, `code`, `request`, `jobDescription`
  - **Candidates**: `id`, `code`, `name`, `position`, `location`, `status` (e.g. `PM_ROUND`, `OPEN`, `OFFER`, `INTERVIEW`), `cvs` (PDF URLs), `cvInformation`, `experience`, `jobRequests` (the currently applied job for that candidate)
- **Multi-Job Application Support**: In HRM, each candidate returned from the candidate endpoint includes `jobRequests` representing their currently applied job; the candidate's `status` applies to this specific job. Candidates can apply to multiple jobs across the recruitment lifecycle.
- **Auto-Sync to Backend**: Transmits extracted job and candidate records (along with applied job and application status) to `HRM_Backend` on port `8765` for AI keyword parsing, dense embeddings, multi-job relationship persistence, and precalculated matching.

### 3. Direct CV Action Links Across All Tables

- Clickable PDF action links (e.g. `[CV 1]`, `[CV 2]`) are rendered inline directly next to the candidate's name across **all** views:
  - **Job Details Matching Candidates Table**
  - **Candidate Search Results Table**
- Clicking opens the candidate's CV directly in a new browser tab with HRM session authentication headers.

### 4. Shadow DOM Encapsulated In-Page Widget

- Injects a floating widget encapsulated in an Open Shadow Root (`attachShadow({ mode: 'open' })`) to prevent host page CSS pollution.
- Visual status indicators conform strictly to design rules (color-coded status dots without generated icons):
  - ⚪ **Gray (Idle)**: Injected and idle on non-candidate pages.
  - 🔵 **Blue (Active)**: Candidate job request route detected.
  - 🟡 **Amber (Loading)**: Actively querying HRM APIs.
  - 🟢 **Green (Success)**: Data loaded successfully; displays candidate count badge on the floating button.
  - 🔴 **Red (Error)**: Network error or authentication missing.

### 5. Multi-Page In-Page Drawer & Workspace

- **Header Actions**:
  - **Live Backend Sync Badge**: Shows real-time synchronization state with backend (`Synced (X new, Y upd)`, `Sync Error`, or `Backend Ready`).
  - **Resync Button**: Manually triggers synchronization of the current job request and candidates to backend.
  - **Expand Button (`⤢` / `🗗`)**: Toggles normal widget drawer and full-screen workspace view.
  - **Close Button (`×`)**: Closes drawer view.
- **Main Navigation Bar**:
  - **All Jobs (Matching & Semantic Search)**:
    - Lists all saved jobs from `HRM_Backend` with immediate auto-refresh upon ingestion.
    - **Calibrated Semantic Search**: Natural language search box (e.g. `C++ automotive`, `Python tester`) powered by dense cosine embeddings; unrelated or dump queries return 0 results.
    - Selecting a job displays pre-calculated matching candidates:
      - `Candidate Name` (with inline CV links)
      - `Applied Jobs` (shows all jobs the candidate has applied to with individual status badges, replacing standalone status column)
      - `Matched Experience`
      - `Matching %` (progress bar + percentage)
      - `Action` (`Review` button opening the Candidate Review Page, or disabled `Applied` badge if already applied)
  - **Search Candidates (with Match Score Badges & Multi-Job Application)**:
    - Free-text query prompt to search candidates across all saved records.
    - Candidate search table columns:
      - `Candidate Details` (name, position, location, relevance score badge `${c.query_relevance}% match`, inline CV links)
      - `Applied Jobs` (all applied jobs with individual status badges)
      - `Action` (`View Matches` button)
    - Dump/gibberish queries (`dump word`, `asdfghjkl`) return 0 results with helpful suggestions.
    - Selecting a candidate displays pre-calculated matching jobs:
      - `Job Title` & `Code`
      - `Matched Requests`
      - `Matching %` (progress bar + percentage)
      - `Action` (`Review` button placed next to `Matching %` to inspect the candidate against the matching job, or disabled `Applied` badge)
  - **Candidate Review Page (Pre-Assignment Inspection)**:
    - Triggered by any `Review` button; automatically expands the drawer to full page.
    - Four-frame layout:
      - **Job Details** (top-left, 50% height): title, code, level, request, full description and extracted job skills.
      - **Extracted CV Information** (bottom-left, 50% height): candidate contact details, level, years of experience, AI summary, extracted skills and matched experience.
      - **Candidate CV** (top-right, 90% height): authenticated fetch of the CV PDF rendered inline via an embedded viewer.
      - **Reviewer Comment** (bottom-right, 10% height): free-text notes preserved while the page is open.
    - **Assign Button** (footer): calls `POST /api/candidates/{candidate_id}/apply` to actually link the candidate to the job, then returns to the underlying table with the `Applied` state.
  - **Settings**:
    - **Backend Service URL**: Configure backend endpoint (default: `http://localhost:8765`) and test connection health.
    - **Backend AI Model Info (Read-only)**:
      - LLM extraction model currently configured on backend (for example `qwen2.5:3b`)
      - FastEmbed local semantic model status and active fixed model (`BAAI/bge-large-en-v1.5`)
    - **Database Management**: One-click confirmation to clear all stored database records (`/api/database/clear`).

---

## Extracted Data Schema

Sample responses:

- `HRM_Extension/sample_response_fetch_job-requests.json`
- `HRM_Extension/sample_response_fetch_candidate_candidates.json`

Extracted payload structure:

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
      "status": "PM_ROUND",
      "cvs": [
        "https://hrm.ltsgroup.tech/api/cvs/nguyen-van-cuong-test-embedded/cvs-19268c47-79a4-4e30-9ec1-bbed78b60359-1789356178960.pdf"
      ],
      "applied_job": {
        "id": "79d999b0-449c-4de2-8592-77b34c2191aa",
        "title": "BOSCH - Onsite HCM - Software Developer - Ngôn Ngữ C++",
        "code": "JR-BOSCH-CPP",
        "status": "PM_ROUND"
      }
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
5. Select the `HRM_Extension` directory:

   ```text
   /path/to/HRM_JobMatching/HRM_Extension
   ```

6. Navigate to `https://hrm.ltsgroup.tech/recruitment` and log in.
7. Open any job request candidate page (e.g. `/recruitment/job-requests/candidate/<jobRequestsId>`) to start automatic data extraction.
8. Click the floating widget (or browser toolbar icon) and click **`⤢`** to expand the drawer to full page and view the candidate table.

---

## Directory Structure

```text
HRM_Extension/
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
