/**
 * HRM Extension Content Script
 * Injected into https://hrm.ltsgroup.tech/recruitment*
 *
 * Features:
 * - Dual API extraction (Job Request + Candidates)
 * - Extracts Candidate status (e.g. "status": "PM_ROUND")
 * - Auto-syncs to HRM_Backend on port 8765
 * - Header Actions: Backend Sync Status badge, Resync button, Full Page, Close
 * - Multi-page Drawer:
 *   1. All Jobs (Semantic search to filter jobs, lists saved jobs, clicking job displays matching candidates)
 *   2. Candidate Search (Query prompt to search candidates, clicking candidate displays matching jobs)
 *   3. Backend Settings (URL config, backend AI model info, clear all database option)
 */

(() => {
  'use strict';

  console.log(`[HRM Extension] Injected & Loaded successfully on: ${window.location.href}`);

  // Config & Constants
  const DEFAULT_BACKEND_URL = 'http://localhost:8765';

  // Curated model metadata for clearer Settings display.
  // Sources: official model cards/runtime pages (Hugging Face, Ollama, FastEmbed docs).
  const LLM_MODEL_METADATA = {
    'qwen2.5:3b': {
      name: 'Qwen2.5 3B Instruct',
      params: '3.09B',
      size: '~1.9 GB (Ollama tag)',
      context: '32K runtime window',
      engine: 'Ollama runtime (HTTP chat API)'
    },
    'qwen2.5:1.5b': {
      name: 'Qwen2.5 1.5B Instruct',
      params: '1.5B',
      size: '~986 MB (Ollama tag)',
      context: '32K runtime window',
      engine: 'Ollama runtime (HTTP chat API)'
    },
    'llama3.2:3b': {
      name: 'Llama 3.2 3B Instruct',
      params: '3B',
      size: '~2.0 GB class (quantized tag dependent)',
      context: 'Tag-dependent',
      engine: 'Ollama runtime (HTTP chat API)'
    }
  };

  const FASTEMBED_MODEL_METADATA = {
    'baai/bge-large-en-v1.5': {
      name: 'BGE Large EN v1.5',
      params: '0.3B',
      size: '~1.20 GB (ONNX cache)',
      dims: 1024,
      engine: 'FastEmbed ONNX Runtime (CPU)'
    }
  };

  function getBackendUrl() {
    try {
      return localStorage.getItem('hrm_backend_url') || DEFAULT_BACKEND_URL;
    } catch (e) {
      return DEFAULT_BACKEND_URL;
    }
  }

  function setBackendUrl(url) {
    try {
      localStorage.setItem('hrm_backend_url', url.trim().replace(/\/+$/, ''));
    } catch (e) {}
  }

  // Route extraction helper
  function extractJobRequestId(url = window.location.href) {
    if (!url) return null;

    const match1 = url.match(/\/recruitment\/job-requests?\/candidate\/([a-zA-Z0-9_-]+)/i);
    if (match1 && match1[1] && match1[1] !== 'undefined') return match1[1];

    const match2 = url.match(/\/recruitment\/job-requests?\/([a-zA-Z0-9_-]+)\/candidate/i);
    if (match2 && match2[1] && match2[1] !== 'undefined') return match2[1];

    try {
      const parsed = new URL(url, window.location.origin);
      const qId = parsed.searchParams.get('jobRequestId') ||
                  parsed.searchParams.get('job_request_id') ||
                  parsed.searchParams.get('id');
      if (qId && /^[a-zA-Z0-9_-]{5,}$/.test(qId)) return qId;
    } catch (e) {}

    return null;
  }

  // Application State
  let currentJobRequestId = null;
  let fetchedJobRequestId = null;
  let extractedData = null;
  let previousUrl = window.location.href;
  let isFetching = false;

  // Drawer Navigation State
  let mainPage = 'jobs'; // 'jobs' | 'search' | 'settings'

  // Backend Sync & Cache State
  let backendSyncStatus = { synced: false, time: null, error: null, count: 0, newCount: 0, updatedCount: 0 };

  // Jobs view state
  let backendJobs = [];
  let isLoadingJobs = false;
  let jobSearchQuery = '';
  let selectedJob = null;
  let selectedJobCandidates = [];
  let isLoadingJobCandidates = false;

  // Candidate Search view state
  let candidateSearchQuery = '';
  let candidateSearchResults = [];
  let totalCandidatesCount = 0;
  let isSearchingCandidates = false;
  let selectedCandidate = null;
  let selectedCandidateJobs = [];
  let isLoadingCandidateJobs = false;

  // Candidate Review Page state
  let reviewContext = null; // { candId, jobId, candidate, job, source, fullCandidate }
  let reviewAssigning = false;
  let reviewError = null;
  let reviewWasFullPage = false;
  let reviewCvObjectUrl = null;
  let reviewCvLoadedUrl = null;


  // Settings view state
  let backendModelInfo = null;
  let backendHealthInfo = null;
  let isClearingDb = false;

  // DOM Elements inside Shadow Root
  let shadowRoot = null;
  let floatingRoot = null;
  let statusDot = null;
  let badgeCountEl = null;
  let tooltipEl = null;
  let drawerEl = null;
  let expandBtn = null;
  let headerResyncBtn = null;
  let headerSyncStatusEl = null;

  /**
   * Safe extraction of access token from localStorage.auth_tconnect
   */
  function getAccessToken() {
    try {
      const raw = localStorage.getItem('auth_tconnect');
      if (!raw) return null;
      const auth = JSON.parse(raw);
      return auth?.accessToken || auth?.access_token || auth?.token || null;
    } catch (e) {
      return null;
    }
  }

  /**
   * Update status indicator dot and candidate count badge
   */
  function updateIndicator(state, message, count = null) {
    if (!statusDot || !tooltipEl) return;

    statusDot.className = '';
    if (state) statusDot.classList.add(state);

    tooltipEl.textContent = message || 'HRM Extension: Idle';

    if (badgeCountEl) {
      if (typeof count === 'number' && count >= 0) {
        badgeCountEl.textContent = count > 99 ? '99+' : String(count);
        badgeCountEl.classList.add('visible');
      } else {
        badgeCountEl.textContent = '';
        badgeCountEl.classList.remove('visible');
      }
    }
  }

  /**
   * Update the sync status pill in drawer header
   */
  function updateHeaderSyncStatus() {
    if (!headerSyncStatusEl) return;

    if (backendSyncStatus.synced) {
      let detail = `${backendSyncStatus.count} cands`;
      if (backendSyncStatus.newCount !== undefined && backendSyncStatus.updatedCount !== undefined) {
        detail = `${backendSyncStatus.newCount} new, ${backendSyncStatus.updatedCount} upd`;
      }
      headerSyncStatusEl.innerHTML = `<span class="hrm-ext-badge status-green" title="Synced to backend at ${backendSyncStatus.time}">Synced (${detail})</span>`;
    } else if (backendSyncStatus.error) {
      headerSyncStatusEl.innerHTML = `<span class="hrm-ext-badge status-red" title="${escapeHtml(backendSyncStatus.error)}">Sync Error</span>`;
    } else {
      headerSyncStatusEl.innerHTML = `<span class="hrm-ext-badge status-default">Backend Ready</span>`;
    }
  }

  /**
   * Toggle full page mode
   */
  function toggleDrawerFullPage(forceState) {
    if (!drawerEl) return;

    const isFullPage = typeof forceState === 'boolean'
      ? forceState
      : !drawerEl.classList.contains('full-page');

    if (isFullPage) {
      drawerEl.classList.add('full-page');
      if (expandBtn) {
        expandBtn.textContent = '🗗';
        expandBtn.title = 'Restore Normal Size';
      }
    } else {
      drawerEl.classList.remove('full-page');
      if (expandBtn) {
        expandBtn.textContent = '⤢';
        expandBtn.title = 'Expand to Full Page';
      }
    }
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function getLlmMeta(modelId) {
    const key = String(modelId || '').trim().toLowerCase();
    const known = LLM_MODEL_METADATA[key];
    if (known) return known;

    const guess = key.match(/:(\d+(?:\.\d+)?)b\b/i);
    const guessedParams = guess ? `${guess[1]}B` : 'N/A';
    return {
      name: modelId || 'Unknown LLM Model',
      params: guessedParams,
      size: 'N/A',
      context: 'N/A',
      engine: 'Ollama/OpenAI-compatible HTTP runtime'
    };
  }

  function getFastembedMeta(model) {
    const id = String(model?.id || '').trim();
    const key = id.toLowerCase();
    const known = FASTEMBED_MODEL_METADATA[key] || {};
    return {
      id,
      name: model?.name || known.name || id || 'Unknown FastEmbed Model',
      params: known.params || 'N/A',
      size: model?.size || known.size || 'N/A',
      dims: model?.dim || known.dims || 'N/A',
      engine: known.engine || 'FastEmbed ONNX Runtime (CPU)'
    };
  }

  /**
   * Helper to format status badges with distinct colors without generating icons
   */
  function renderStatusBadge(status) {
    const s = (status || 'OPEN').toUpperCase();
    let colorClass = 'status-default';

    if (s.includes('PM_ROUND') || s.includes('ROUND')) {
      colorClass = 'status-purple';
    } else if (s.includes('OPEN')) {
      colorClass = 'status-blue';
    } else if (s.includes('INTERVIEW')) {
      colorClass = 'status-amber';
    } else if (s.includes('OFFER') || s.includes('PASS') || s.includes('ACCEPT')) {
      colorClass = 'status-green';
    } else if (s.includes('REJECT') || s.includes('FAIL') || s.includes('CANCEL')) {
      colorClass = 'status-red';
    }

    return `<span class="hrm-ext-badge ${colorClass}">${escapeHtml(status || 'OPEN')}</span>`;
  }

  /**
   * Helper to format all applied jobs with their job status
   */
  function renderAppliedJobs(appliedJobs) {
    if (!Array.isArray(appliedJobs) || appliedJobs.length === 0) {
      return '<span class="hrm-ext-subtext">—</span>';
    }
    return `
      <div class="hrm-ext-applied-jobs-list">
        ${appliedJobs.map((j) => {
          const title = j.job_title || j.title || 'Untitled Job';
          return `
            <div class="hrm-ext-applied-job-item">
              <span class="hrm-ext-applied-job-title" title="${escapeHtml(title)}">${escapeHtml(title)}</span>
              ${renderStatusBadge(j.status)}
            </div>
          `;
        }).join('')}
      </div>
    `;
  }

  /**
   * Helper to format matching percentage with color-coded progress bar
   */
  function renderMatchPercent(pct) {
    const val = typeof pct === 'number' ? pct : parseFloat(pct) || 0;
    let toneClass = 'is-neutral';

    if (val >= 75) {
      toneClass = 'is-high';
    } else if (val >= 50) {
      toneClass = 'is-mid';
    } else if (val >= 30) {
      toneClass = 'is-low';
    }

    const width = Math.min(100, Math.max(5, val));

    return `
      <div class="hrm-ext-match-box">
        <span class="hrm-ext-match-text ${toneClass}">${val.toFixed(1)}%</span>
        <div class="hrm-ext-progress-bg">
          <div class="hrm-ext-progress-fill ${toneClass}" data-pct="${width.toFixed(2)}"></div>
        </div>
      </div>
    `;
  }

  function applyMatchProgressWidths(container) {
    const bars = container.querySelectorAll('.hrm-ext-progress-fill[data-pct]');
    bars.forEach((bar) => {
      const width = parseFloat(bar.dataset.pct || '0');
      bar.style.width = `${width}%`;
    });
  }

  /**
   * Sync extracted data to HRM_Backend
   */
  async function syncToBackend(payload) {
    if (!payload || !payload.jobRequest) return;

    const backendUrl = getBackendUrl();
    const token = getAccessToken();

    try {
      console.log(`[HRM Extension] Syncing Job Request #${payload.jobRequestId} to backend (${backendUrl})...`);

      // 1. Sync Job
      const jobRes = await fetch(`${backendUrl}/api/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload.jobRequest)
      });

      if (!jobRes.ok) {
        throw new Error(`Failed to sync Job Request (HTTP ${jobRes.status})`);
      }
      const jobData = await jobRes.json();

      // 2. Sync Candidates Batch
      let newCount = 0;
      let updatedCount = 0;
      if (payload.candidates && payload.candidates.length > 0) {
        const candsRes = await fetch(`${backendUrl}/api/candidates/batch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            jobRequestId: payload.jobRequestId,
            token: token,
            candidates: payload.candidates
          })
        });

        if (!candsRes.ok) {
          throw new Error(`Failed to sync Candidates (HTTP ${candsRes.status})`);
        }
        const candsData = await candsRes.json();
        newCount = candsData.new_count || 0;
        updatedCount = candsData.updated_count || 0;
      }

      backendSyncStatus = {
        synced: true,
        time: new Date().toLocaleTimeString(),
        error: null,
        count: payload.candidates?.length || 0,
        newCount: newCount,
        updatedCount: updatedCount,
        isNewJob: Boolean(jobData.is_new)
      };
      console.log(`[HRM Extension] Synced to backend. New: ${newCount}, Updated: ${updatedCount}.`);

      // Automatically refresh backend jobs list so new job appears immediately on All Jobs page
      loadBackendJobs(jobSearchQuery);
      fetch(`${backendUrl}/api/health`)
        .then(r => r.ok ? r.json() : null)
        .then(h => {
          if (h && h.stats) {
            totalCandidatesCount = h.stats.candidates_count || 0;
            updateDrawerContent();
          }
        })
        .catch(() => {});

    } catch (err) {
      console.warn('[HRM Extension] Backend sync error:', err.message);
      backendSyncStatus = {
        synced: false,
        time: new Date().toLocaleTimeString(),
        error: err.message,
        count: payload.candidates?.length || 0
      };
    }

    updateHeaderSyncStatus();
    updateDrawerContent();
  }

  /**
   * Render CV action links badge without icons
   */
  function renderCvActionLinks(cvUrls) {
    if (!Array.isArray(cvUrls) || cvUrls.length === 0) return '';
    return cvUrls.map((url, i) => {
      const label = cvUrls.length === 1 ? 'CV' : `CV ${i + 1}`;
      return `<a href="${escapeHtml(url)}" target="_blank" class="hrm-ext-cv-link" rel="noopener noreferrer" title="Open CV document">${label}</a>`;
    }).join(' ');
  }

  /**
   * Main Drawer View Renderers
   */

  // 1. All Jobs View (with Semantic Search)
  function renderAllJobsView() {
    if (selectedJob) {
      // Selected Job Matching Candidates Table view
      let rowsHtml = '';
      if (isLoadingJobCandidates) {
        rowsHtml = '<tr><td colspan="6" class="hrm-ext-cell-message hrm-ext-state-loading">Loading matching candidates from backend...</td></tr>';
      } else if (selectedJobCandidates.length === 0) {
        rowsHtml = '<tr><td colspan="6" class="hrm-ext-cell-message">No candidates matched with this job yet.</td></tr>';
      } else {
        selectedJobCandidates.forEach((c, idx) => {
          const isApplied = Boolean(c.is_applied || (Array.isArray(c.applied_jobs) && c.applied_jobs.some(j => j.job_id === selectedJob.id)));
          const actionBtn = isApplied
            ? `<button class="hrm-ext-sm-btn hrm-ext-btn-applied" disabled type="button">Applied</button>`
            : `<button class="hrm-ext-sm-btn hrm-ext-btn-primary review-candidate-btn" data-cand-id="${escapeHtml(c.id)}" data-job-id="${escapeHtml(selectedJob.id)}" type="button">Review</button>`;

          rowsHtml += `
            <tr>
              <td class="hrm-ext-cell-index">${idx + 1}</td>
              <td>
                <div class="hrm-ext-inline-wrap">
                  <span class="hrm-ext-title-strong">${escapeHtml(c.name || 'Unknown')}</span>
                  ${renderCvActionLinks(c.cv_urls)}
                </div>
                <div class="hrm-ext-meta-line">${escapeHtml(c.code || '')} • ${escapeHtml(c.position || '')} • ${escapeHtml(c.location || '')}</div>
              </td>
              <td>${renderAppliedJobs(c.applied_jobs)}</td>
              <td class="hrm-ext-body-note hrm-ext-note-clamp">${escapeHtml(c.matched_experience || 'Experience aligns with job.')}</td>
              <td class="hrm-ext-col-match">${renderMatchPercent(c.matching_percentage)}</td>
              <td class="hrm-ext-col-action">${actionBtn}</td>
            </tr>
          `;
        });
      }

      return `
        <div class="hrm-ext-stack-md">
          <div class="hrm-ext-row-between">
            <button class="hrm-ext-sm-btn" id="hrm-ext-back-jobs-btn" type="button">‹ Back to Jobs List</button>
            <span class="hrm-ext-count-label">${selectedJobCandidates.length} Matched Candidate(s)</span>
          </div>
          <div class="hrm-ext-panel-soft">
            <div class="hrm-ext-section-label">JOB MATCHING DETAILS</div>
            <div class="hrm-ext-title-lg hrm-ext-mt-2">${escapeHtml(selectedJob.title || 'Job Request')}</div>
            <div class="hrm-ext-subtext">${escapeHtml(selectedJob.request || '')}</div>
          </div>
          <div class="hrm-ext-table-container">
            <table class="hrm-ext-table">
              <thead>
                <tr>
                  <th class="hrm-ext-col-index">#</th>
                  <th>Candidate Name</th>
                  <th>Applied Jobs</th>
                  <th>Matched Experience</th>
                  <th>Matching %</th>
                  <th class="hrm-ext-table-head-center">Action</th>
                </tr>
              </thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // Semantic Search Bar for Jobs
    const searchBarHtml = `
      <div class="hrm-ext-search-bar hrm-ext-mb-8">
        <input type="text" id="hrm-ext-job-search-input" class="hrm-ext-input hrm-ext-input-grow" placeholder="Semantic search jobs (e.g. C++ embedded, Python tester)..." value="${escapeHtml(jobSearchQuery)}" />
        <button type="button" id="hrm-ext-job-search-btn" class="hrm-ext-btn-primary">Search</button>
        ${jobSearchQuery ? `<button type="button" id="hrm-ext-job-search-clear-btn" class="hrm-ext-sm-btn">Clear</button>` : ''}
        <button type="button" id="hrm-ext-refresh-jobs-btn" class="hrm-ext-sm-btn">Refresh Jobs</button>
      </div>
    `;

    // List of All Jobs
    let jobsListHtml = '';
    if (isLoadingJobs) {
      jobsListHtml = '<div class="hrm-ext-empty-state hrm-ext-empty-state-lg hrm-ext-state-loading">Loading jobs from backend...</div>';
    } else if (backendJobs.length === 0) {
      jobsListHtml = `
        <div class="hrm-ext-empty-state hrm-ext-empty-state-lg hrm-ext-empty-state-padded">
          ${jobSearchQuery ? `<div>No jobs match "${escapeHtml(jobSearchQuery)}".</div>` : `<div>No jobs found in backend database yet.</div><div class="hrm-ext-help-text">Navigate to HRM Job Request pages to auto-ingest jobs.</div>`}
        </div>
      `;
    } else {
      let rows = '';
      backendJobs.forEach((j, idx) => {
        const relevanceBadge = j.query_relevance !== undefined
          ? `<span class="hrm-ext-badge status-purple hrm-ext-badge-spaced">${j.query_relevance}% match</span>`
          : '';

        rows += `
          <tr>
            <td class="hrm-ext-cell-index">${idx + 1}</td>
            <td>
              <div class="hrm-ext-row-start">
                <span class="hrm-ext-title-strong">${escapeHtml(j.title || 'Untitled')}</span>
                ${relevanceBadge}
              </div>
              <div class="hrm-ext-meta-line">${escapeHtml(j.code || '')} • ${escapeHtml(j.request ? j.request.substring(0, 70) + '...' : '')}</div>
            </td>
            <td class="hrm-ext-col-action">
              <button class="hrm-ext-sm-btn select-job-btn" data-job-id="${escapeHtml(j.id)}" type="button">View Matches</button>
            </td>
          </tr>
        `;
      });

      jobsListHtml = `
        <div class="hrm-ext-table-container">
          <table class="hrm-ext-table">
            <thead>
              <tr>
                <th class="hrm-ext-col-index">#</th>
                <th>Job Title</th>
                <th class="hrm-ext-table-head-center">Action</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    return `
      <div class="hrm-ext-stack-sm">
        <span class="hrm-ext-title-md">All Saved Jobs (${backendJobs.length})</span>
        ${searchBarHtml}
        ${jobsListHtml}
      </div>
    `;

  }

  // 2. Candidate Search View
  function renderCandidateSearchView() {
    if (selectedCandidate) {
      // Selected Candidate Matching Jobs Table view
      let rowsHtml = '';
      if (isLoadingCandidateJobs) {
        rowsHtml = '<tr><td colspan="5" class="hrm-ext-cell-message hrm-ext-state-loading">Loading matching jobs from backend...</td></tr>';
      } else if (selectedCandidateJobs.length === 0) {
        rowsHtml = '<tr><td colspan="5" class="hrm-ext-cell-message">No matching jobs found for this candidate.</td></tr>';
      } else {
        selectedCandidateJobs.forEach((j, idx) => {
          const isApplied = Boolean(j.is_applied || (Array.isArray(selectedCandidate.applied_jobs) && selectedCandidate.applied_jobs.some(app => app.job_id === j.id)));
          const actionBtn = isApplied
            ? `<button class="hrm-ext-sm-btn hrm-ext-btn-applied" disabled type="button">Applied</button>`
            : `<button class="hrm-ext-sm-btn hrm-ext-btn-primary review-candidate-btn" data-cand-id="${escapeHtml(selectedCandidate.id)}" data-job-id="${escapeHtml(j.id)}" type="button">Review</button>`;

          rowsHtml += `
            <tr>
              <td class="hrm-ext-cell-index">${idx + 1}</td>
              <td>
                <div class="hrm-ext-title-strong">${escapeHtml(j.job_title || j.title || 'Untitled Job')}</div>
                <div class="hrm-ext-meta-line">${escapeHtml(j.code || '')}</div>
              </td>
              <td class="hrm-ext-body-note hrm-ext-note-clamp">${escapeHtml(j.matched_requests || 'Fulfills job requirements')}</td>
              <td class="hrm-ext-col-match">${renderMatchPercent(j.matching_percentage)}</td>
              <td class="hrm-ext-col-action">${actionBtn}</td>
            </tr>
          `;
        });
      }

      return `
        <div class="hrm-ext-stack-md">
          <div class="hrm-ext-row-between">
            <button class="hrm-ext-sm-btn" id="hrm-ext-back-candidates-btn" type="button">‹ Back to Search</button>
            <span class="hrm-ext-count-label">${selectedCandidateJobs.length} Matching Job(s)</span>
          </div>
          <div class="hrm-ext-panel-soft">
            <div class="hrm-ext-row-between">
              <div>
                <div class="hrm-ext-inline-wrap hrm-ext-inline-wrap-md">
                  <span class="hrm-ext-title-lg">${escapeHtml(selectedCandidate.name || 'Candidate')}</span>
                  ${renderCvActionLinks(selectedCandidate.cv_urls)}
                </div>
                <div class="hrm-ext-subtext">${escapeHtml(selectedCandidate.position || '')} • ${escapeHtml(selectedCandidate.location || '')}</div>
              </div>
              <div>${renderStatusBadge(selectedCandidate.status)}</div>
            </div>
          </div>
          <div class="hrm-ext-table-container">
            <table class="hrm-ext-table">
              <thead>
                <tr>
                  <th class="hrm-ext-col-index">#</th>
                  <th>Job Title</th>
                  <th>Matched Requests</th>
                  <th>Matching %</th>
                  <th class="hrm-ext-table-head-center">Action</th>
                </tr>
              </thead>
              <tbody>${rowsHtml}</tbody>
            </table>
          </div>
        </div>
      `;
    }

    // Search input and candidate search results
    let resultsHtml = '';
    if (isSearchingCandidates) {
      resultsHtml = '<div class="hrm-ext-empty-state hrm-ext-empty-state-sm hrm-ext-state-loading">Searching candidates in backend...</div>';
    } else if (candidateSearchResults.length > 0) {
      let rows = '';
      candidateSearchResults.forEach((c, idx) => {
        const skillsList = Array.isArray(c.extracted_keywords)
          ? c.extracted_keywords.slice(0, 4).map(s => `<span class="hrm-ext-skill-tag">${escapeHtml(s)}</span>`).join(' ')
          : '';

        const relevanceBadge = c.query_relevance !== undefined
          ? `<span class="hrm-ext-badge status-purple hrm-ext-badge-spaced">${c.query_relevance}% match</span>`
          : '';

        rows += `
          <tr>
            <td class="hrm-ext-cell-index">${idx + 1}</td>
            <td>
              <div class="hrm-ext-inline-wrap">
                <span class="hrm-ext-title-strong">${escapeHtml(c.name || 'Candidate')}</span>
                ${relevanceBadge}
                ${renderCvActionLinks(c.cv_urls)}
              </div>
              <div class="hrm-ext-meta-line">${escapeHtml(c.code || '')} • ${escapeHtml(c.position || '')} • ${escapeHtml(c.location || '')}</div>
              <div class="hrm-ext-mt-4">${skillsList}</div>
            </td>
            <td>${renderAppliedJobs(c.applied_jobs)}</td>
            <td class="hrm-ext-col-action">
              <button class="hrm-ext-sm-btn select-cand-btn" data-cand-id="${escapeHtml(c.id)}" type="button">Find Jobs</button>
            </td>
          </tr>
        `;
      });

      resultsHtml = `
        <div class="hrm-ext-table-container">
          <table class="hrm-ext-table">
            <thead>
              <tr>
                <th class="hrm-ext-col-index">#</th>
                <th>Candidate Details</th>
                <th>Applied Jobs</th>
                <th class="hrm-ext-table-head-center">Action</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    } else if (candidateSearchQuery) {
      resultsHtml = `<div class="hrm-ext-empty-state hrm-ext-empty-state-sm">No candidates match query "${escapeHtml(candidateSearchQuery)}".</div>`;
    } else {
      resultsHtml = `
        <div class="hrm-ext-empty-state hrm-ext-empty-state-lg hrm-ext-empty-state-padded">
          <div>Search candidates across all saved records.</div>
          <div class="hrm-ext-help-text">Try queries like: <code>Python tester</code>, <code>C++ embedded</code>, or <code>PM_ROUND</code>.</div>
        </div>
      `;
    }

    const candCount = candidateSearchQuery ? candidateSearchResults.length : totalCandidatesCount;

    return `
      <div class="hrm-ext-stack-sm">
        <span class="hrm-ext-title-md">
          ${candidateSearchQuery ? `Candidate Results (${candCount})` : `All Saved Candidates (${totalCandidatesCount})`}
        </span>
        <div class="hrm-ext-search-bar hrm-ext-mb-8">
          <input type="text" id="hrm-ext-candidate-search-input" class="hrm-ext-input hrm-ext-input-grow" placeholder="Search candidate skills, title, status..." value="${escapeHtml(candidateSearchQuery)}" />
          <button type="button" id="hrm-ext-candidate-search-btn" class="hrm-ext-btn-primary">Search</button>
          ${candidateSearchQuery ? `<button type="button" id="hrm-ext-candidate-search-clear-btn" class="hrm-ext-sm-btn">Clear</button>` : ''}
          <button type="button" id="hrm-ext-refresh-candidates-btn" class="hrm-ext-sm-btn">Refresh Candidates</button>
        </div>
        ${resultsHtml}
      </div>
    `;
  }

  // 3. Backend Settings View
  function renderSettingsView() {
    const llm = backendModelInfo?.llm || {};
    const llmRuntime = llm.runtime || {};
    const fastembed = backendModelInfo?.fastembed || {};
    const llmModelId = llm.model || 'qwen2.5:3b';
    const llmMeta = getLlmMeta(llmModelId);
    const llmProvider = String(llmRuntime.provider || 'ollama').toLowerCase();
    const llmDevice = llmRuntime.device || 'Unknown';
    const llmTransport = llmRuntime.transport || 'HTTP chat API';
    const llmEngine = llmProvider === 'ollama'
      ? `Ollama on ${llmDevice}, served via ${llmTransport}`
      : `${llmRuntime.provider || 'OpenAI-compatible runtime'} on ${llmDevice}, served via ${llmTransport}`;
    const activeFastembed = fastembed.active_model || 'BAAI/bge-large-en-v1.5';
    const isFastembedLoaded = !!fastembed.is_loaded;
    const fastembedEngine = backendHealthInfo?.local_model || 'FastEmbed (ONNX)';
    const fastembedModels = Array.isArray(fastembed.models) && fastembed.models.length
      ? fastembed.models
      : [{ id: 'BAAI/bge-large-en-v1.5', name: 'BGE Large EN v1.5', dim: 1024, size: '~1.20 GB' }];
    const fastembedMeta = getFastembedMeta(fastembedModels[0]);
    const fastembedResolvedEngine = isFastembedLoaded ? fastembedMeta.engine : (fastembed.fallback || fastembedEngine);

    return `
      <div class="hrm-ext-stack-lg hrm-ext-full-width">
        <!-- Backend Connection -->

        <div class="hrm-ext-card">
          <div class="hrm-ext-card-title">Backend Connection</div>
          <div class="hrm-ext-field">
            <div class="hrm-ext-row-input hrm-ext-mt-4">
              <input type="text" id="hrm-ext-backend-url-input" class="hrm-ext-input hrm-ext-input-grow" value="${escapeHtml(getBackendUrl())}" />
              <button type="button" id="hrm-ext-save-url-btn" class="hrm-ext-sm-btn">Save</button>
            </div>
            <div class="hrm-ext-help-text hrm-ext-mt-4">Default port: 8765 (http://localhost:8765)</div>
          </div>
          <div class="hrm-ext-row-input hrm-ext-mt-10">
            <button type="button" id="hrm-ext-test-health-btn" class="hrm-ext-btn-primary">Test Connection</button>
            <div id="hrm-ext-health-result" class="hrm-ext-body-note"></div>
          </div>
        </div>

        <!-- Backend AI Models (Read-only) -->
        <div class="hrm-ext-card">
          <div class="hrm-ext-card-title">Backend AI Models</div>

          <!-- LLM Model -->
          <div class="hrm-ext-panel-soft">
            <div class="hrm-ext-row-between">
              <span class="hrm-ext-section-label hrm-ext-section-label-caps">LLM MODEL</span>
              <span class="hrm-ext-badge hrm-ext-badge-xs ${llm.enabled ? 'status-green' : 'status-red'}">
                ${llm.enabled ? 'Enabled' : 'Disabled'}
              </span>
            </div>
            <div class="hrm-ext-title-md hrm-ext-mt-4">
              ${escapeHtml(llmMeta.name)}
            </div>
            <div class="hrm-ext-model-meta-row hrm-ext-mt-6">
              <span><span class="hrm-ext-model-id">${escapeHtml(llmModelId)}</span></span>
            </div>
            <div class="hrm-ext-help-text">Metadata: Params ${escapeHtml(llmMeta.params)} • Size ${escapeHtml(llmMeta.size)} • Context ${escapeHtml(llmMeta.context)}</div>
            <div class="hrm-ext-help-text">Inference Engine: ${escapeHtml(llmEngine)}</div>
          </div>

          <!-- FastEmbed Model -->
          <div class="hrm-ext-panel-soft hrm-ext-mt-10">
            <div class="hrm-ext-row-between">
              <span class="hrm-ext-section-label hrm-ext-section-label-caps">FASTEMBED MODEL</span>
              <span class="hrm-ext-badge hrm-ext-badge-xs ${isFastembedLoaded ? 'status-green' : 'status-amber'}">
                ${isFastembedLoaded ? 'Loaded' : 'Fallback Vectorizer'}
              </span>
            </div>
            <div class="hrm-ext-title-md hrm-ext-mt-4">
              ${escapeHtml(fastembedMeta.name)}
            </div>
            <div class="hrm-ext-model-meta-row hrm-ext-mt-6">
              <span><span class="hrm-ext-model-id">${escapeHtml(fastembedMeta.id || activeFastembed)}</span></span>
            </div>
            <div class="hrm-ext-help-text">Metadata: Params ${escapeHtml(fastembedMeta.params)} • Size ${escapeHtml(fastembedMeta.size)} • Dimensions ${escapeHtml(String(fastembedMeta.dims))}</div>
            <div class="hrm-ext-help-text">Inference Engine: ${escapeHtml(fastembedResolvedEngine)}</div>
          </div>

          <div class="hrm-ext-card-desc hrm-ext-mt-6">
            Model configuration is managed by backend; selection is disabled in extension.
          </div>
        </div>

        <!-- Database Management -->
        <div class="hrm-ext-card hrm-ext-danger-card">
          <div class="hrm-ext-card-title hrm-ext-text-danger">Database Management</div>
          <div class="hrm-ext-card-desc">Clear all stored jobs, candidates, and pre-calculated matches from SQLite:</div>
          <div class="hrm-ext-row-input hrm-ext-mt-8">
            <button type="button" id="hrm-ext-clear-db-btn" class="hrm-ext-btn-danger" ${isClearingDb ? 'disabled' : ''}>${isClearingDb ? 'Clearing...' : 'Clear All Database'}</button>
            <div id="hrm-ext-clear-db-feedback" class="hrm-ext-body-note"></div>
          </div>
        </div>
      </div>
    `;
  }

  /**
   * Render the Candidate Review Page with 4 frames:
   *  - top left:    job details (50% height)
   *  - bottom left: extracted info from candidate CV (50% height)
   *  - top right:   embedded candidate CV PDF (90% height)
   *  - bottom right: reviewer's comment (10% height)
   * Footer holds the Assign button that actually links candidate to job.
   */
  function renderReviewView() {
    const ctx = reviewContext;
    if (!ctx) return '';

    const job = ctx.job || {};
    const cand = ctx.candidate || {};
    const full = ctx.fullCandidate || {};

    const candName = cand.name || full.name || 'Candidate';
    const candStatus = cand.status || full.status || 'OPEN';
    const matchPct = (cand.matching_percentage ?? job.matching_percentage);
    const extractedSkills = (Array.isArray(full.extracted_keywords) && full.extracted_keywords.length)
      ? full.extracted_keywords
      : (Array.isArray(cand.matched_skills) ? cand.matched_skills : []);
    const exp = full.extracted_experiences || {};
    const cvUrl = (Array.isArray(cand.cv_urls) && cand.cv_urls.length) ? cand.cv_urls[0] : (Array.isArray(full.cv_urls) && full.cv_urls.length ? full.cv_urls[0] : '');

    const jobKeywords = (Array.isArray(job.extracted_keywords) && job.extracted_keywords.length)
      ? job.extracted_keywords.map(s => `<span class="hrm-ext-skill-tag">${escapeHtml(s)}</span>`).join(' ')
      : '';

    const skillsHtml = extractedSkills.length
      ? `<div class="hrm-ext-mt-6">${extractedSkills.slice(0, 20).map(s => `<span class="hrm-ext-skill-tag">${escapeHtml(s)}</span>`).join(' ')}</div>`
      : '<div class="hrm-ext-help-text hrm-ext-mt-6">No skills extracted yet.</div>';

    const matchedExpText = cand.matched_experience || job.matched_experience || '';
    const matchedRequestsText = cand.matched_requests || job.matched_requests || '';
    const jobDescriptionText = job.job_description || '';

    const matchPctHtml = (matchPct !== undefined && matchPct !== null)
      ? `<span class="hrm-ext-badge status-purple" title="Matching percentage">${Number(matchPct).toFixed(1)}% match</span>`
      : '';

    const footerNote = reviewError
      ? `<span class="hrm-ext-review-footer-note">Assign failed: ${escapeHtml(reviewError)}</span>`
      : '<span class="hrm-ext-review-footer-note"></span>';

    return `
      <div class="hrm-ext-review-page">
        <div class="hrm-ext-row-between hrm-ext-review-header">
          <div class="hrm-ext-row-start">
            <button class="hrm-ext-sm-btn hrm-ext-review-back-btn" type="button">‹ Back</button>
            <span class="hrm-ext-title-md">Review Candidate</span>
          </div>
          <div class="hrm-ext-inline-wrap">
            <span class="hrm-ext-title-strong">${escapeHtml(candName)}</span>
            ${renderStatusBadge(candStatus)}
            ${matchPctHtml}
          </div>
        </div>

        <div class="hrm-ext-review-body">
          <div class="hrm-ext-review-col">
            <div class="hrm-ext-review-panel hrm-ext-review-panel-top">
              <div class="hrm-ext-review-panel-label">Job Details</div>
              <div class="hrm-ext-review-scroll">
                <div class="hrm-ext-title-strong">${escapeHtml(job.title || 'Untitled Job')}</div>
                <div class="hrm-ext-meta-line">${escapeHtml(job.code || '')}${job.level ? ` • ${escapeHtml(job.level)}` : ''}</div>
                ${job.request ? `<div class="hrm-ext-preline hrm-ext-mt-6 hrm-ext-body-note">${escapeHtml(job.request)}</div>` : ''}
                ${jobDescriptionText ? `<div class="hrm-ext-preline hrm-ext-mt-6 hrm-ext-help-text">${escapeHtml(stripHtml(jobDescriptionText))}</div>` : ''}
                ${jobKeywords ? `<div class="hrm-ext-mt-6">${jobKeywords}</div>` : ''}
                ${matchedRequestsText ? `<div class="hrm-ext-panel-soft hrm-ext-panel-soft-compact hrm-ext-mt-6"><div class="hrm-ext-section-label">WHY THIS CANDIDATE MATCHES</div><div class="hrm-ext-mt-4 hrm-ext-body-note">${escapeHtml(matchedRequestsText)}</div></div>` : ''}
              </div>
            </div>

            <div class="hrm-ext-review-panel hrm-ext-review-panel-bottom">
              <div class="hrm-ext-review-panel-label">Extracted CV Information</div>
              <div class="hrm-ext-review-scroll">
                <div class="hrm-ext-title-strong">${escapeHtml(candName)}</div>
                <div class="hrm-ext-meta-line">
                  ${escapeHtml(full.position || cand.position || '')}${full.position || cand.position ? ' • ' : ''}${escapeHtml(full.location || cand.location || '')}${full.location || cand.location ? ' • ' : ''}${escapeHtml(full.code || cand.code || '')}
                  ${full.extracted_level || cand.level ? ` • ${escapeHtml(full.extracted_level || cand.level)}` : ''}
                </div>
                ${(exp.years_experience !== undefined && exp.years_experience !== null && exp.years_experience !== '')
                  ? `<div class="hrm-ext-mt-6 hrm-ext-row-start"><span class="hrm-ext-section-label">Experience:</span><span class="hrm-ext-body-note">${escapeHtml(String(exp.years_experience))}</span></div>`
                  : ''}
                ${exp.summary ? `<div class="hrm-ext-mt-6 hrm-ext-body-note">${escapeHtml(exp.summary)}</div>` : ''}
                ${skillsHtml}
                ${matchedExpText ? `<div class="hrm-ext-panel-soft hrm-ext-panel-soft-compact hrm-ext-mt-6"><div class="hrm-ext-section-label">MATCHED EXPERIENCE</div><div class="hrm-ext-mt-4 hrm-ext-body-note">${escapeHtml(matchedExpText)}</div></div>` : ''}
              </div>
            </div>
          </div>

          <div class="hrm-ext-review-col">
            <div class="hrm-ext-review-panel hrm-ext-review-cv">
              <div class="hrm-ext-review-panel-label">Candidate CV</div>
              <div class="hrm-ext-review-cv-body" id="hrm-ext-review-cv-frame"></div>
            </div>
            <div class="hrm-ext-review-panel hrm-ext-review-comment">
              <div class="hrm-ext-review-panel-label">Reviewer Comment</div>
              <textarea class="hrm-ext-review-comment-input" id="hrm-ext-review-comment-input" placeholder="Add notes before assigning...">${escapeHtml(ctx.comment || '')}</textarea>
            </div>
          </div>
        </div>

        <div class="hrm-ext-review-footer">
          ${footerNote}
          <button class="hrm-ext-btn-primary" type="button" id="hrm-ext-assign-btn"
            data-cand-id="${escapeHtml(ctx.candId)}" data-job-id="${escapeHtml(ctx.jobId)}"
            ${reviewAssigning ? 'disabled' : ''}>${reviewAssigning ? 'Assigning...' : 'Assign to Job'}</button>
        </div>
      </div>
    `;
  }

  function stripHtml(html) {
    const div = document.createElement('div');
    div.innerHTML = html || '';
    return div.textContent || '';
  }

  function closeReview() {
    if (reviewCvObjectUrl) {
      URL.revokeObjectURL(reviewCvObjectUrl);
      reviewCvObjectUrl = null;
    }
    reviewCvLoadedUrl = null;
    const wasFullPage = reviewWasFullPage;
    reviewContext = null;
    reviewAssigning = false;
    reviewError = null;
    toggleDrawerFullPage(wasFullPage);
    updateDrawerContent();
  }

  function openReview(cand, job, source) {
    reviewWasFullPage = drawerEl.classList.contains('full-page');
    toggleDrawerFullPage(true);
    reviewContext = {
      candId: cand.id,
      jobId: job.id,
      candidate: cand,
      job: job,
      source: source || 'job',
      fullCandidate: null
    };
    reviewAssigning = false;
    reviewError = null;
    updateDrawerContent();
    loadReviewDetails();
  }

  async function loadReviewDetails() {
    const ctx = reviewContext;
    if (!ctx) return;

    try {
      const [jobRes, candRes] = await Promise.all([
        fetch(`${getBackendUrl()}/api/jobs/${encodeURIComponent(ctx.jobId)}`),
        fetch(`${getBackendUrl()}/api/candidates/${encodeURIComponent(ctx.candId)}`)
      ]);
      const jobData = jobRes.ok ? await jobRes.json() : null;
      const candData = candRes.ok ? await candRes.json() : null;
      if (!reviewContext) return;
      reviewContext = {
        ...reviewContext,
        job: jobData || ctx.job,
        fullCandidate: candData || ctx.fullCandidate
      };
      updateDrawerContent();
    } catch (e) {
      console.warn('[HRM Extension] Failed to load review details:', e);
    }
  }

  async function loadReviewCv(container) {
    const frame = container.querySelector('#hrm-ext-review-cv-frame');
    if (!frame || frame.dataset.loading === '1') return;

    const cvUrl = (reviewContext?.candidate?.cv_urls && reviewContext.candidate.cv_urls[0])
      || (reviewContext?.fullCandidate?.cv_urls && reviewContext.fullCandidate.cv_urls[0]);
    if (!cvUrl) {
      frame.innerHTML = '<div class="hrm-ext-review-cv-note">No CV document available for this candidate.</div>';
      return;
    }

    // Re-embed already-fetched blob without refetching when re-rendering the same CV.
    if (reviewCvObjectUrl && reviewCvLoadedUrl === cvUrl) {
      if (!frame.querySelector('embed')) {
        const embed = document.createElement('embed');
        embed.type = 'application/pdf';
        embed.src = reviewCvObjectUrl;
        embed.className = 'hrm-ext-review-cv-embed';
        frame.innerHTML = '';
        frame.appendChild(embed);
      }
      return;
    }

    if (reviewCvObjectUrl) {
      URL.revokeObjectURL(reviewCvObjectUrl);
      reviewCvObjectUrl = null;
      reviewCvLoadedUrl = null;
    }

    frame.dataset.loading = '1';
    frame.innerHTML = '<div class="hrm-ext-review-cv-note hrm-ext-state-loading">Loading CV document...</div>';

    try {
      const token = getAccessToken();
      const headers = { 'Accept': 'application/pdf,*/*' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const res = await fetch(cvUrl, { headers, credentials: 'include' });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      reviewCvObjectUrl = url;
      reviewCvLoadedUrl = cvUrl;

      const embed = document.createElement('embed');
      embed.type = 'application/pdf';
      embed.src = url;
      embed.className = 'hrm-ext-review-cv-embed';

      frame.innerHTML = '';
      frame.appendChild(embed);
    } catch (e) {
      console.warn('[HRM Extension] Failed to load candidate CV:', e);
      frame.innerHTML = `<div class="hrm-ext-review-cv-note">Unable to load CV: ${escapeHtml(e.message)}. <a href="${escapeHtml(cvUrl)}" target="_blank" rel="noopener noreferrer" class="hrm-ext-cv-link">Open in new tab</a></div>`;
    } finally {
      delete frame.dataset.loading;
    }
  }

  async function applyCandidateToJob(candId, jobId) {
    const res = await fetch(`${getBackendUrl()}/api/candidates/${encodeURIComponent(candId)}/apply`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ job_id: jobId })
    });

    if (!res.ok) {
      throw new Error(`Failed to apply (HTTP ${res.status})`);
    }

    const data = await res.json();

    // 1. Update matching candidate in local selectedJobCandidates (if on selected job view)
    const cand = selectedJobCandidates.find(c => c.id === candId);
    if (cand) {
      cand.is_applied = true;
      cand.applied_jobs = data.applied_jobs || [];
    }

    // 2. Update matching job in local selectedCandidateJobs (if on selected candidate view)
    const candJob = selectedCandidateJobs.find(j => j.id === jobId);
    if (candJob) {
      candJob.is_applied = true;
    }
    if (selectedCandidate && selectedCandidate.id === candId) {
      selectedCandidate.applied_jobs = data.applied_jobs || [];
    }

    // 3. Also update candidate in search results if present
    const searchCand = candidateSearchResults.find(c => c.id === candId);
    if (searchCand) {
      searchCand.applied_jobs = data.applied_jobs || [];
    }

    return data;
  }

  async function assignCandidateFromReview(btn) {
    if (reviewAssigning) return;
    const candId = btn.dataset.candId;
    const jobId = btn.dataset.jobId;
    if (!candId || !jobId) return;

    reviewAssigning = true;
    reviewError = null;
    updateDrawerContent();

    try {
      await applyCandidateToJob(candId, jobId);
      reviewAssigning = false;
      const wasFullPage = reviewWasFullPage;
      reviewContext = null;
      reviewError = null;
      toggleDrawerFullPage(wasFullPage);
      updateDrawerContent();
    } catch (err) {
      console.error('[HRM Extension] Assign error:', err);
      reviewAssigning = false;
      reviewError = err.message;
      updateDrawerContent();
    }
  }

  /**
   * Update drawer content based on selected mainPage
   */
  function updateDrawerContent() {
    if (!drawerEl) return;

    let pageContent = '';
    if (mainPage === 'active') {
      pageContent = renderActivePageView();
    } else if (mainPage === 'jobs') {
      pageContent = renderAllJobsView();
    } else if (mainPage === 'search') {
      pageContent = renderCandidateSearchView();
    } else if (mainPage === 'settings') {
      pageContent = renderSettingsView();
    }

    const bodyEl = drawerEl.querySelector('.hrm-ext-body');
    if (bodyEl) {
      if (reviewContext) {
        bodyEl.innerHTML = renderReviewView();
        applyMatchProgressWidths(bodyEl);
        bindDrawerEvents(bodyEl);
      } else {
        bodyEl.innerHTML = `
          <!-- Main Navigation Bar -->
          <div class="hrm-ext-main-nav">
            <button class="hrm-ext-nav-btn ${mainPage === 'jobs' ? 'active' : ''}" type="button" data-nav="jobs">All Jobs</button>
            <button class="hrm-ext-nav-btn ${mainPage === 'search' ? 'active' : ''}" type="button" data-nav="search">Search Candidates</button>
            <button class="hrm-ext-nav-btn ${mainPage === 'settings' ? 'active' : ''}" type="button" data-nav="settings">Settings</button>
          </div>
          <div class="hrm-ext-page-container">${pageContent}</div>
        `;

        applyMatchProgressWidths(bodyEl);
        bindDrawerEvents(bodyEl);
      }
    }
    updateHeaderSyncStatus();
  }

  /**
   * Bind event listeners for drawer views
   */
  function bindDrawerEvents(container) {
    // 0. Candidate Review Page events
    const reviewBackBtn = container.querySelector('.hrm-ext-review-back-btn');
    if (reviewBackBtn) {
      reviewBackBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        closeReview();
      });
    }

    const assignBtn = container.querySelector('#hrm-ext-assign-btn');
    if (assignBtn) {
      assignBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        assignCandidateFromReview(assignBtn);
      });
    }

    const reviewCommentInput = container.querySelector('#hrm-ext-review-comment-input');
    if (reviewCommentInput) {
      reviewCommentInput.addEventListener('input', (e) => {
        e.stopPropagation();
        if (reviewContext) reviewContext.comment = reviewCommentInput.value;
      });
    }

    if (container.querySelector('#hrm-ext-review-cv-frame')) {
      loadReviewCv(container);
    }

    // 1. Navigation buttons
    const navBtns = container.querySelectorAll('.hrm-ext-nav-btn');
    navBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        mainPage = btn.dataset.nav;
        if (mainPage === 'jobs') {
          selectedJob = null;
          selectedJobCandidates = [];
          loadBackendJobs(jobSearchQuery);
        } else if (mainPage === 'search' && !candidateSearchResults.length) {
          searchCandidates('');
        } else if (mainPage === 'settings') {
          loadBackendModelInfo();
        }
        updateDrawerContent();
      });
    });

    // 2. Jobs view events
    const refreshJobsBtn = container.querySelector('#hrm-ext-refresh-jobs-btn');
    if (refreshJobsBtn) {
      refreshJobsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        loadBackendJobs(jobSearchQuery);
      });
    }

    const jobSearchInput = container.querySelector('#hrm-ext-job-search-input');
    const jobSearchBtn = container.querySelector('#hrm-ext-job-search-btn');
    if (jobSearchBtn && jobSearchInput) {
      jobSearchBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        loadBackendJobs(jobSearchInput.value.trim());
      });
      jobSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.stopPropagation();
          loadBackendJobs(jobSearchInput.value.trim());
        }
      });
    }

    const jobSearchClearBtn = container.querySelector('#hrm-ext-job-search-clear-btn');
    if (jobSearchClearBtn) {
      jobSearchClearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        loadBackendJobs('');
      });
    }

    const selectJobBtns = container.querySelectorAll('.select-job-btn');
    selectJobBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const jId = btn.dataset.jobId;
        const job = backendJobs.find(x => x.id === jId);
        if (job) {
          selectJobAndLoadCandidates(job);
        }
      });
    });

    const backJobsBtn = container.querySelector('#hrm-ext-back-jobs-btn');
    if (backJobsBtn) {
      backJobsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        selectedJob = null;
        selectedJobCandidates = [];
        updateDrawerContent();
      });
    }

    const reviewCandidateBtns = container.querySelectorAll('.review-candidate-btn');
    reviewCandidateBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const candId = btn.dataset.candId;
        const jobId = btn.dataset.jobId;
        if (!candId || !jobId) return;

        let cand = null;
        let job = null;
        let source = 'job';

        // Candidate-search flow: reviewing a matching job for the selected candidate.
        if (selectedCandidate && selectedCandidate.id === candId) {
          cand = selectedCandidate;
          job = selectedCandidateJobs.find(j => j.id === jobId) || null;
          source = 'candidate';
        }

        // Jobs flow: reviewing a matching candidate for the selected job.
        if (!cand && selectedJob && selectedJob.id === jobId) {
          cand = selectedJobCandidates.find(c => c.id === candId) || null;
          job = selectedJob;
          source = 'job';
        }

        // Fallbacks using locally cached records.
        if (!cand) {
          cand = selectedJobCandidates.find(c => c.id === candId)
            || candidateSearchResults.find(c => c.id === candId)
            || null;
        }
        if (!job) {
          job = selectedCandidateJobs.find(j => j.id === jobId)
            || selectedJob
            || backendJobs.find(j => j.id === jobId)
            || null;
        }

        if (cand && job) {
          openReview(cand, job, source);
        } else {
          console.warn('[HRM Extension] Could not resolve review context for candidate', candId, 'job', jobId);
        }
      });
    });

    // 3. Candidate Search events
    const candSearchInput = container.querySelector('#hrm-ext-candidate-search-input');
    const candSearchBtn = container.querySelector('#hrm-ext-candidate-search-btn');
    if (candSearchBtn && candSearchInput) {
      candSearchBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        searchCandidates(candSearchInput.value.trim());
      });
      candSearchInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
          e.stopPropagation();
          searchCandidates(candSearchInput.value.trim());
        }
      });
    }

    const candSearchClearBtn = container.querySelector('#hrm-ext-candidate-search-clear-btn');
    if (candSearchClearBtn) {
      candSearchClearBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        searchCandidates('');
      });
    }

    const refreshCandsBtn = container.querySelector('#hrm-ext-refresh-candidates-btn');
    if (refreshCandsBtn) {
      refreshCandsBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        searchCandidates(candidateSearchQuery);
      });
    }


    const selectCandBtns = container.querySelectorAll('.select-cand-btn');
    selectCandBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const cId = btn.dataset.candId;
        const cand = candidateSearchResults.find(x => x.id === cId);
        if (cand) {
          selectCandidateAndLoadJobs(cand);
        }
      });
    });

    const backCandBtn = container.querySelector('#hrm-ext-back-candidates-btn');
    if (backCandBtn) {
      backCandBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        selectedCandidate = null;
        selectedCandidateJobs = [];
        updateDrawerContent();
      });
    }

    // 4. Settings events
    const saveUrlBtn = container.querySelector('#hrm-ext-save-url-btn');
    const urlInput = container.querySelector('#hrm-ext-backend-url-input');
    if (saveUrlBtn && urlInput) {
      saveUrlBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        setBackendUrl(urlInput.value);
        alert(`Backend URL updated to: ${getBackendUrl()}`);
      });
    }

    const testHealthBtn = container.querySelector('#hrm-ext-test-health-btn');
    const healthResultEl = container.querySelector('#hrm-ext-health-result');
    if (testHealthBtn && healthResultEl) {
      testHealthBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        healthResultEl.innerHTML = '<span class="hrm-ext-health-checking">Checking backend...</span>';
        try {
          const res = await fetch(`${getBackendUrl()}/api/health`);
          if (res.ok) {
            const data = await res.json();
            healthResultEl.innerHTML = `
              <span class="hrm-ext-health-success">Connected</span>
              <div class="hrm-ext-health-meta">Jobs: ${data.stats?.jobs_count || 0} | Candidates: ${data.stats?.candidates_count || 0} | Matches: ${data.stats?.matches_count || 0}</div>
            `;
          } else {
            healthResultEl.innerHTML = `<span class="hrm-ext-health-error">HTTP ${res.status} error</span>`;
          }
        } catch (err) {
          healthResultEl.innerHTML = `<span class="hrm-ext-health-error">Error: ${escapeHtml(err.message)}</span>`;
        }
      });
    }

    const clearDbBtn = container.querySelector('#hrm-ext-clear-db-btn');
    const clearDbFeedbackEl = container.querySelector('#hrm-ext-clear-db-feedback');
    if (clearDbBtn) {
      clearDbBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const confirmed = confirm('Are you sure you want to delete all saved jobs, candidates, and matches from the database? This cannot be undone.');
        if (!confirmed) return;

        isClearingDb = true;
        updateDrawerContent();

        try {
          const res = await fetch(`${getBackendUrl()}/api/database/clear`, { method: 'POST' });
          if (res.ok) {
            backendJobs = [];
            candidateSearchResults = [];
            selectedJob = null;
            selectedCandidate = null;
            backendSyncStatus = { synced: false, time: null, count: 0, newCount: 0, updatedCount: 0 };
            totalCandidatesCount = 0;
            alert('Database cleared successfully.');

          } else {
            alert(`Failed to clear database (HTTP ${res.status})`);
          }
        } catch (err) {
          alert(`Error clearing database: ${err.message}`);
        } finally {
          isClearingDb = false;
          updateDrawerContent();
        }
      });
    }
  }

  /**
   * Backend API queries for Drawer Pages
   */
  async function loadBackendJobs(query = '') {
    jobSearchQuery = query;
    isLoadingJobs = true;
    updateDrawerContent();

    try {
      const url = query
        ? `${getBackendUrl()}/api/jobs?q=${encodeURIComponent(query)}`
        : `${getBackendUrl()}/api/jobs`;
      const res = await fetch(url);
      if (res.ok) {
        backendJobs = await res.json();
      } else {
        backendJobs = [];
      }
    } catch (e) {
      console.warn('[HRM Extension] Failed to load jobs from backend:', e);
      backendJobs = [];
    } finally {
      isLoadingJobs = false;
      updateDrawerContent();
    }
  }

  async function selectJobAndLoadCandidates(job) {
    selectedJob = job;
    selectedJobCandidates = [];
    isLoadingJobCandidates = true;
    updateDrawerContent();

    try {
      const res = await fetch(`${getBackendUrl()}/api/jobs/${encodeURIComponent(job.id)}/candidates`);
      if (res.ok) {
        const data = await res.json();
        selectedJobCandidates = data.candidates || [];
      }
    } catch (e) {
      console.warn('[HRM Extension] Failed to load job matching candidates:', e);
    } finally {
      isLoadingJobCandidates = false;
      updateDrawerContent();
    }
  }

  async function searchCandidates(query) {
    candidateSearchQuery = query;
    isSearchingCandidates = true;
    updateDrawerContent();

    try {
      const url = query
        ? `${getBackendUrl()}/api/candidates?q=${encodeURIComponent(query)}`
        : `${getBackendUrl()}/api/candidates`;
      const res = await fetch(url);
      if (res.ok) {
        candidateSearchResults = await res.json();
        if (!query) {
          totalCandidatesCount = candidateSearchResults.length;
        }
      } else {
        candidateSearchResults = [];
      }

      if (query && totalCandidatesCount === 0) {
        fetch(`${getBackendUrl()}/api/candidates`)
          .then(r => r.ok ? r.json() : [])
          .then(all => {
            totalCandidatesCount = all.length;
            updateDrawerContent();
          })
          .catch(() => {});
      }
    } catch (e) {
      console.warn('[HRM Extension] Candidate search error:', e);
      candidateSearchResults = [];
    } finally {
      isSearchingCandidates = false;
      updateDrawerContent();
    }
  }


  async function selectCandidateAndLoadJobs(candidate) {
    selectedCandidate = candidate;
    selectedCandidateJobs = [];
    isLoadingCandidateJobs = true;
    updateDrawerContent();

    try {
      const res = await fetch(`${getBackendUrl()}/api/candidates/${encodeURIComponent(candidate.id)}/jobs`);
      if (res.ok) {
        const data = await res.json();
        selectedCandidateJobs = data.jobs || [];
      }
    } catch (e) {
      console.warn('[HRM Extension] Failed to load candidate matching jobs:', e);
    } finally {
      isLoadingCandidateJobs = false;
      updateDrawerContent();
    }
  }

  async function loadBackendModelInfo() {
    try {
      const [modelsRes, healthRes] = await Promise.all([
        fetch(`${getBackendUrl()}/api/models`),
        fetch(`${getBackendUrl()}/api/health`)
      ]);

      backendModelInfo = modelsRes.ok ? await modelsRes.json() : null;
      backendHealthInfo = healthRes.ok ? await healthRes.json() : null;
    } catch (e) {
      console.warn('[HRM Extension] Failed to load backend model info:', e);
      backendModelInfo = null;
      backendHealthInfo = null;
    }
    updateDrawerContent();
  }

  /**
   * Create and mount floating widget inside Shadow DOM
   */
  function mountFloatingWidget() {
    if (document.getElementById('hrm-ext-host')) return;

    const hostEl = document.createElement('div');
    hostEl.id = 'hrm-ext-host';
    hostEl.style.all = 'initial';

    shadowRoot = hostEl.attachShadow({ mode: 'open' });

    const styleLink = document.createElement('link');
    styleLink.rel = 'stylesheet';
    styleLink.href = chrome.runtime.getURL('content/content.css');
    shadowRoot.appendChild(styleLink);

    floatingRoot = document.createElement('div');
    floatingRoot.id = 'hrm-ext-floating-root';

    const iconUrl = chrome.runtime.getURL('icons/icon-48.png');

    floatingRoot.innerHTML = `
      <div id="hrm-ext-tooltip">HRM Extension: Idle (Navigate to a Job Request)</div>
      <button id="hrm-ext-floating-btn" type="button" aria-label="HRM Extension" title="Open HRM Recruitment Assistant">
        <img src="${iconUrl}" alt="HRM" />
        <span id="hrm-ext-status-dot" class="idle"></span>
        <span id="hrm-ext-badge-count"></span>
      </button>
      <div id="hrm-ext-drawer">
        <div class="hrm-ext-header">
          <div class="hrm-ext-title">
            <span>HRM Recruitment Assistant</span>
          </div>
          <div class="hrm-ext-header-actions">
            <div id="hrm-ext-header-sync-status"></div>
            <button class="hrm-ext-header-btn hrm-ext-header-resync-btn" type="button" id="hrm-ext-header-resync-btn" title="Resync to Backend">Resync</button>
            <button class="hrm-ext-header-btn hrm-ext-expand-btn" type="button" id="hrm-ext-expand-btn" title="Expand to Full Page">⤢</button>
            <button class="hrm-ext-header-btn hrm-ext-close-btn" type="button" id="hrm-ext-close-btn" title="Close">&times;</button>
          </div>
        </div>
        <div class="hrm-ext-body"></div>
      </div>
    `;


    shadowRoot.appendChild(floatingRoot);
    document.body.appendChild(hostEl);

    statusDot = shadowRoot.querySelector('#hrm-ext-status-dot');
    badgeCountEl = shadowRoot.querySelector('#hrm-ext-badge-count');
    tooltipEl = shadowRoot.querySelector('#hrm-ext-tooltip');
    drawerEl = shadowRoot.querySelector('#hrm-ext-drawer');
    expandBtn = shadowRoot.querySelector('#hrm-ext-expand-btn');
    headerResyncBtn = shadowRoot.querySelector('#hrm-ext-header-resync-btn');
    headerSyncStatusEl = shadowRoot.querySelector('#hrm-ext-header-sync-status');

    const btn = shadowRoot.querySelector('#hrm-ext-floating-btn');
    const closeBtn = shadowRoot.querySelector('#hrm-ext-close-btn');

    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      drawerEl.classList.toggle('open');
      updateDrawerContent();
    });

    expandBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleDrawerFullPage();
    });

    closeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      drawerEl.classList.remove('open');
    });

    headerResyncBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (extractedData) {
        await syncToBackend(extractedData);
      } else if (currentJobRequestId) {
        await fetchJobRequestAndCandidates(currentJobRequestId, true);
      } else {
        alert('No active job request detected to resync. Navigate to a job request candidate page.');
      }
    });

    document.addEventListener('click', (e) => {
      if (drawerEl && drawerEl.classList.contains('open') && !drawerEl.classList.contains('full-page')) {
        const path = e.composedPath();
        if (!path.includes(floatingRoot)) {
          drawerEl.classList.remove('open');
        }
      }
    });

    updateIndicator('idle', 'HRM Extension: Idle (Navigate to a Job Request)');
    updateHeaderSyncStatus();
    updateDrawerContent();
  }

  /**
   * Helper to perform authenticated GET request to HRM APIs
   */
  async function fetchHrmApi(url, token) {
    const headers = {
      "accept": "*/*",
      "accept-language": "en-US,en;q=0.9",
      "content-type": "application/json",
      "priority": "u=1, i",
      "sec-fetch-dest": "empty",
      "sec-fetch-mode": "cors",
      "sec-fetch-site": "same-origin"
    };

    if (token) {
      headers["authorization"] = `Bearer ${token}`;
    }

    const response = await fetch(url, {
      method: "GET",
      headers: headers,
      referrer: window.location.href,
      mode: "cors",
      credentials: "include"
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status} (${response.statusText}) on ${url}`);
    }

    return await response.json();
  }

  /**
   * Fetch both Job Request and Candidate CVs data, extracting status & all target fields
   */
  async function fetchJobRequestAndCandidates(jobRequestId, force = false) {
    if (isFetching) return;
    if (!force && fetchedJobRequestId === jobRequestId && extractedData) {
      console.log(`[HRM Extension] Already fetched data for Job Request #${jobRequestId}`);
      return extractedData;
    }

    isFetching = true;
    updateIndicator('loading', `Fetching Job Request #${jobRequestId}...`);
    updateDrawerContent();

    const token = getAccessToken();
    const jobRequestUrl = `https://hrm.ltsgroup.tech/api/job-requests/${jobRequestId}`;
    const candidatesUrl = `https://hrm.ltsgroup.tech/api/candidate/candidates/${jobRequestId}`;

    try {
      console.log(`[HRM Extension] Fetching job request and candidates in parallel for [${jobRequestId}]`);

      const [jobReqRaw, candListRaw] = await Promise.all([
        fetchHrmApi(jobRequestUrl, token),
        fetchHrmApi(candidatesUrl, token)
      ]);

      // 1. Extract specified Job Request fields
      const jobRequest = {
        id: jobReqRaw?.id || jobRequestId,
        title: jobReqRaw?.title || jobReqRaw?.name || null,
        code: jobReqRaw?.code || null,
        level: jobReqRaw?.level || null,
        levelCandidate: jobReqRaw?.levelCandidate || null,
        request: jobReqRaw?.request || null,
        jobDescription: jobReqRaw?.jobDescription || null,
        raw_data: jobReqRaw
      };

      // 2. Extract Candidate fields including STATUS
      const candidates = Array.isArray(candListRaw)
        ? candListRaw.map((item) => {
            let cvFiles = [];
            if (item?.cv?.cvs) {
              try {
                cvFiles = typeof item.cv.cvs === 'string' ? JSON.parse(item.cv.cvs) : item.cv.cvs;
              } catch (e) {
                cvFiles = [item.cv.cvs];
              }
            }
            const itemJobReq = item?.jobRequests;
            const appliedJob = itemJobReq ? {
              id: itemJobReq.id,
              title: itemJobReq.title || itemJobReq.name || null,
              code: itemJobReq.code || null,
              status: item?.status || item?.cv?.status || 'OPEN'
            } : (jobRequestId ? {
              id: jobRequestId,
              title: jobReqRaw?.title || jobReqRaw?.name || null,
              code: jobReqRaw?.code || null,
              status: item?.status || item?.cv?.status || 'OPEN'
            } : null);

            return {
              id: item?.cv?.id || item?.id || null,
              application_id: item?.id || null,
              code: item?.cv?.code || null,
              name: item?.cv?.name ? item.cv.name.trim() : null,
              position: item?.cv?.position || null,
              location: item?.cv?.location || null,
              status: item?.status || item?.cv?.status || 'OPEN',
              level: item?.cv?.level || item?.level || null,
              cvs: Array.isArray(cvFiles) ? cvFiles : [cvFiles],
              cvInformation: item?.cv?.cvInformation || null,
              experience: item?.cv?.experience || null,
              applied_job: appliedJob,
              raw_data: item
            };
          })
        : [];

      const extractedPayload = {
        jobRequestId,
        fetchedAt: new Date().toISOString(),
        jobRequest,
        candidates
      };

      fetchedJobRequestId = jobRequestId;
      extractedData = extractedPayload;
      isFetching = false;

      console.log(`[HRM Extension] Loaded Job Request [${jobRequestId}] with ${candidates.length} candidate(s)`);

      updateIndicator('success', `Job Request #${jobRequestId} (${candidates.length} candidates) loaded`, candidates.length);
      updateDrawerContent();

      // Automatically sync with HRM_Backend
      syncToBackend(extractedPayload);

      return extractedPayload;
    } catch (err) {
      console.error(`[HRM Extension] Error fetching Job Request [${jobRequestId}]:`, err);
      isFetching = false;
      updateIndicator('error', `Fetch error: ${err.message}`);
      updateDrawerContent();
      throw err;
    }
  }

  /**
   * Check current URL and trigger fetch on candidate route
   */
  async function checkUrlChange() {
    const currentUrl = window.location.href;
    previousUrl = currentUrl;
    const detectedJobRequestId = extractJobRequestId(currentUrl);

    if (detectedJobRequestId) {
      if (detectedJobRequestId !== currentJobRequestId) {
        console.log(`[HRM Extension] Detected Job Request page: ID = ${detectedJobRequestId}`);
        currentJobRequestId = detectedJobRequestId;
        updateIndicator('active', `Job Request #${detectedJobRequestId} detected`);
        updateDrawerContent();

        try {
          await fetchJobRequestAndCandidates(detectedJobRequestId);
        } catch (e) {}
      }
    } else {
      if (currentJobRequestId !== null) {
        console.log(`[HRM Extension] Navigated away from Job Request page (was ${currentJobRequestId}). Resetting active state.`);
        currentJobRequestId = null;
        updateIndicator('idle', 'HRM Extension: Idle (Navigate to a Job Request)');
        updateDrawerContent();
      }
    }
  }

  /**
   * Multi-layer URL change monitoring
   */
  function setupUrlMonitoring() {
    window.addEventListener('message', (event) => {
      if (event.data && event.data.source === 'hrm_bridge' && event.data.type === 'HRM_SPA_NAV') {
        checkUrlChange();
      }
    });

    document.addEventListener('hrm_spa_nav', () => {
      checkUrlChange();
    });

    document.addEventListener('click', () => {
      setTimeout(checkUrlChange, 30);
      setTimeout(checkUrlChange, 150);
      setTimeout(checkUrlChange, 350);
      setTimeout(checkUrlChange, 700);
    }, true);

    window.addEventListener('popstate', () => checkUrlChange());
    window.addEventListener('hashchange', () => checkUrlChange());

    setInterval(() => {
      if (window.location.href !== previousUrl) {
        checkUrlChange();
      }
    }, 150);

    try {
      const titleEl = document.querySelector('title');
      if (titleEl) {
        const titleObserver = new MutationObserver(() => {
          checkUrlChange();
        });
        titleObserver.observe(titleEl, { childList: true, characterData: true, subtree: true });
      }
    } catch (e) {}

    checkUrlChange();
  }

  /**
   * Background Service Worker message listener
   */
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message.type === 'URL_UPDATED') {
      checkUrlChange();
    } else if (message.type === 'TOGGLE_DRAWER') {
      if (drawerEl) {
        drawerEl.classList.toggle('open');
        updateDrawerContent();
      }
    } else if (message.type === 'GET_STATUS') {
      sendResponse({
        isLoaded: true,
        url: window.location.href,
        jobRequestId: currentJobRequestId,
        backendUrl: getBackendUrl(),
        backendSyncStatus: backendSyncStatus,
        extractedData: extractedData
      });
    } else if (message.type === 'REFETCH') {
      if (currentJobRequestId) {
        fetchJobRequestAndCandidates(currentJobRequestId, true)
          .then((data) => sendResponse({ success: true, data }))
          .catch((err) => sendResponse({ success: false, error: err.message }));
        return true;
      } else {
        sendResponse({ success: false, error: 'Not on a job request page' });
      }
    }
  });

  // Mount UI
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      mountFloatingWidget();
      setupUrlMonitoring();
    });
  } else {
    mountFloatingWidget();
    setupUrlMonitoring();
  }
})();
