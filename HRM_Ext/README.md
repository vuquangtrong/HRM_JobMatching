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

### 2. Dual API Data Extraction

- Automatically reads the authentication Bearer token from `localStorage.auth_tconnect`.
- Concurrently queries both HRM backend endpoints:
  - **Job Request Endpoint**: `GET https://hrm.ltsgroup.tech/api/job-requests/<jobRequestsId>`
  - **Candidates Endpoint**: `GET https://hrm.ltsgroup.tech/api/candidate/candidates/<jobRequestsId>`
- Extracts strictly targeted fields:
  - **Job Request**:
    - `id`: Job request unique ID
    - `title`: Job request title
    - `request`: Summary of requirements
    - `jobDescription`: Main job description (HTML format)
  - **Candidates**:
    - `id`: Unique CV ID
    - `code`: Candidate code (e.g., `C10455`)
    - `name`: Candidate full name
    - `position`: Applied position (e.g., `Tester`, `Developer`)
    - `location`: Location / city (e.g., `Ha Noi`)
    - `cvs`: Array of direct PDF file URLs

### 3. Shadow DOM Encapsulated In-Page Widget

- Injects a lightweight floating button and status dot onto the page.
- Encapsulated in an **Open Shadow Root** (`attachShadow({ mode: 'open' })`) to prevent host page CSS frameworks from conflicting with extension styles and vice versa.
- Visual status indicators conform to design rules (color-coded dots without generated icons):
  - ⚪ **Gray (Idle)**: Injected and idle on non-candidate pages.
  - 🔵 **Blue (Active)**: Candidate job request route detected.
  - 🟡 **Amber (Loading)**: Actively querying HRM APIs.
  - 🟢 **Green (Success)**: Data loaded successfully, displays candidate count badge on the floating button.
  - 🔴 **Red (Error)**: Network error or authentication missing.

### 4. In-Page Drawer

- **Interactive Drawer**: Clicking the floating button on the page (or clicking the extension icon in the browser toolbar) toggles the drawer. Shows **Job Title**, and candidates count.
- **Header Actions**:
  - **`Fetch Data`**: Small header button to fetch or refresh recruitment data.
  - **`⤢` (Full Page)**: Expands drawer to fill the entire viewport for viewing data in a full-screen layout.
  - **(Close)**: Closes the drawer.
- **Tabbed Views & Candidate Table**:
  - **Candidates Table**: Interactive table displaying:
    - `#` (Index)
    - `Candidate Name` with an inline **📄** button to view candidate documents
    - `Code`
    - `Position`
    - `Location`
  - **Job Request Details**: Structured overview of request summary, and job description.
  - **Raw JSON**: Syntax-styled JSON viewer.

---

## Extracted Data Schema

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
- `host_permissions` (`https://hrm.ltsgroup.tech/*`): Authorizes the extension to make authenticated API requests to LTS Group HRM endpoints.
