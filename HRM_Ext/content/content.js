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
 *   1. Active Page (Current job request, candidate table with status badges, spec, json)
 *   2. All Jobs (Semantic search to filter jobs, lists saved jobs, clicking job displays matching candidates)
 *   3. Candidate Search (Query prompt to search candidates, clicking candidate displays matching jobs)
 *   4. Backend Settings (URL config, local model selector from pre-selected list, clear all database option)
 */

(() => {
  'use strict';

  console.log(`[HRM Extension] Injected & Loaded successfully on: ${window.location.href}`);

  // Config & Constants
  const DEFAULT_BACKEND_URL = 'http://localhost:8765';

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
  let mainPage = 'active'; // 'active' | 'jobs' | 'search' | 'settings'
  let activeTabName = 'table'; // 'table' | 'spec' | 'json' for active page

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


  // Settings view state
  let availableModels = [];
  let activeModel = '';
  let isModelLoaded = false;
  let isSwitchingModel = false;
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
   * Helper to format matching percentage with color-coded progress bar
   */
  function renderMatchPercent(pct) {
    const val = typeof pct === 'number' ? pct : parseFloat(pct) || 0;
    let barColor = '#94a3b8';
    let textColor = '#475569';

    if (val >= 75) {
      barColor = '#10b981';
      textColor = '#047857';
    } else if (val >= 50) {
      barColor = '#3b82f6';
      textColor = '#1d4ed8';
    } else if (val >= 30) {
      barColor = '#f59e0b';
      textColor = '#b45309';
    }

    return `
      <div class="hrm-ext-match-box">
        <span class="hrm-ext-match-text" style="color: ${textColor};">${val.toFixed(1)}%</span>
        <div class="hrm-ext-progress-bg">
          <div class="hrm-ext-progress-fill" style="width: ${Math.min(100, Math.max(5, val))}%; background: ${barColor};"></div>
        </div>
      </div>
    `;
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
   * Main Drawer View Renderers
   */

  // 1. Active Page Content
  function renderActivePageView() {
    const candsCount = extractedData?.candidates?.length || 0;
    const reqTitle = extractedData?.jobRequest?.title || '';

    let html = `
      <div style="background: #f8fafc; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0; display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px;">
        ${reqTitle ? `
        <div class="hrm-ext-field" style="margin-bottom: 0;">
          <div class="hrm-ext-field-label">Job Title</div>
          <div class="hrm-ext-field-value" style="font-size: 13px; font-weight: 700; color: #1e293b;">${escapeHtml(reqTitle)}</div>
        </div>` : ''}
      </div>
    `;

    if (extractedData) {
      html += `
        <div class="hrm-ext-tabs">
          <button class="hrm-ext-tab-btn ${activeTabName === 'table' ? 'active' : ''}" type="button" data-tab="table">Candidates Table (${candsCount})</button>
          <button class="hrm-ext-tab-btn ${activeTabName === 'spec' ? 'active' : ''}" type="button" data-tab="spec">Job Request Details</button>
          <button class="hrm-ext-tab-btn ${activeTabName === 'json' ? 'active' : ''}" type="button" data-tab="json">Raw JSON</button>
        </div>
      `;

      if (activeTabName === 'table') {
        const cands = extractedData.candidates || [];
        if (cands.length > 0) {
          let rowsHtml = '';
          cands.forEach((cand, idx) => {
            const cvButtonsHtml = Array.isArray(cand.cvs) && cand.cvs.length > 0
              ? cand.cvs.map((url, i) => {
                  const label = cand.cvs.length === 1 ? 'CV' : `CV ${i + 1}`;
                  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="hrm-ext-cv-btn" title="Open CV document">${label}</a>`;
                }).join(' ')
              : '';

            rowsHtml += `
              <tr>
                <td style="font-weight: 600; color: #64748b; width: 36px; text-align: center;">${idx + 1}</td>
                <td class="cand-name-col">
                  <div style="display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                    <span style="font-weight: 600;">${escapeHtml(cand.name || 'N/A')}</span>
                    ${cvButtonsHtml}
                  </div>
                </td>
                <td>${renderStatusBadge(cand.status)}</td>
                <td class="cand-code-col">${escapeHtml(cand.code || 'N/A')}</td>
                <td class="cand-text-col">${escapeHtml(cand.position || 'N/A')}</td>
                <td class="cand-text-col">${escapeHtml(cand.location || 'N/A')}</td>
              </tr>
            `;
          });

          html += `
            <div class="hrm-ext-table-container">
              <table class="hrm-ext-table">
                <thead>
                  <tr>
                    <th style="width: 36px; text-align: center;">#</th>
                    <th>Candidate Name</th>
                    <th>Status</th>
                    <th>Code</th>
                    <th>Position</th>
                    <th>Location</th>
                  </tr>
                </thead>
                <tbody>${rowsHtml}</tbody>
              </table>
            </div>
          `;
        } else {
          html += `<div style="text-align: center; color: #64748b; padding: 25px;">No candidates attached to this job request yet.</div>`;
        }
      } else if (activeTabName === 'spec') {
        const req = extractedData.jobRequest || {};
        html += `
          <div style="display: flex; flex-direction: column; gap: 10px; background: #ffffff; padding: 14px; border: 1px solid #e2e8f0; border-radius: 8px;">
            <div class="hrm-ext-field">
              <div class="hrm-ext-field-label">Request Summary</div>
              <div class="hrm-ext-field-value" style="white-space: pre-line;">${escapeHtml(req.request || 'N/A')}</div>
            </div>
            ${req.jobDescription ? `
            <div class="hrm-ext-field">
              <div class="hrm-ext-field-label">Job Description</div>
              <div class="hrm-ext-field-value" style="max-height: 280px; overflow-y: auto; border: 1px solid #f1f5f9; padding: 8px; border-radius: 4px;">${req.jobDescription}</div>
            </div>` : ''}
          </div>
        `;
      } else if (activeTabName === 'json') {
        html += `<div class="hrm-ext-json-box">${escapeHtml(JSON.stringify(extractedData, null, 2))}</div>`;
      }
    } else if (isFetching) {
      html += `
        <div style="text-align: center; color: #f59e0b; padding: 30px 10px;">
          <div style="font-weight: 600; margin-bottom: 6px;">Fetching Job Request & Candidates...</div>
          <div style="font-size: 11px; color: #64748b;">Querying HRM APIs concurrently</div>
        </div>
      `;
    } else if (currentJobRequestId) {
      html += `
        <div style="text-align: center; color: #64748b; padding: 30px 10px;">
          <div>Job Request #${currentJobRequestId} detected.</div>
          <div style="font-size: 12px; margin-top: 6px;">Click <strong>Resync</strong> in the header to extract data.</div>
        </div>
      `;
    } else {
      html += `
        <div style="text-align: center; color: #64748b; padding: 30px 10px;">
          Navigate to a Job Request candidate page:
          <div style="margin-top: 6px; font-family: monospace; font-size: 11px; color: #2563eb;">/recruitment/job-requests/candidate/&lt;jobRequestsId&gt;</div>
        </div>
      `;
    }

    return html;
  }

  // 2. All Jobs View (with Semantic Search)
  function renderAllJobsView() {
    if (selectedJob) {
      // Selected Job Matching Candidates Table view
      let rowsHtml = '';
      if (isLoadingJobCandidates) {
        rowsHtml = `<tr><td colspan="5" style="text-align: center; padding: 25px; color: #f59e0b;">Loading matching candidates from backend...</td></tr>`;
      } else if (selectedJobCandidates.length === 0) {
        rowsHtml = `<tr><td colspan="5" style="text-align: center; padding: 25px; color: #64748b;">No candidates matched with this job yet.</td></tr>`;
      } else {
        selectedJobCandidates.forEach((c, idx) => {
          rowsHtml += `
            <tr>
              <td style="width: 36px; text-align: center; font-weight: 600; color: #64748b;">${idx + 1}</td>
              <td>
                <div style="font-weight: 600; color: #1e293b;">${escapeHtml(c.name || 'Unknown')}</div>
                <div style="font-size: 11px; color: #64748b;">${escapeHtml(c.code || '')} • ${escapeHtml(c.position || '')} • ${escapeHtml(c.location || '')}</div>
              </td>
              <td>${renderStatusBadge(c.status)}</td>
              <td style="font-size: 12px; color: #334155; max-width: 260px;">${escapeHtml(c.matched_experience || 'Experience aligns with job.')}</td>
              <td style="width: 110px;">${renderMatchPercent(c.matching_percentage)}</td>
            </tr>
          `;
        });
      }

      return `
        <div style="display: flex; flex-direction: column; gap: 10px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <button class="hrm-ext-sm-btn" id="hrm-ext-back-jobs-btn" type="button">‹ Back to Jobs List</button>
            <span style="font-size: 12px; color: #64748b;">${selectedJobCandidates.length} Matched Candidate(s)</span>
          </div>
          <div style="background: #f8fafc; padding: 12px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div style="font-size: 11px; font-weight: 600; color: #64748b;">JOB MATCHING DETAILS</div>
            <div style="font-size: 14px; font-weight: 700; color: #1e293b; margin-top: 2px;">${escapeHtml(selectedJob.title || 'Job Request')}</div>
            <div style="font-size: 12px; color: #475569; margin-top: 4px;">${escapeHtml(selectedJob.request || '')}</div>
          </div>
          <div class="hrm-ext-table-container">
            <table class="hrm-ext-table">
              <thead>
                <tr>
                  <th style="width: 36px; text-align: center;">#</th>
                  <th>Candidate Name</th>
                  <th>Status</th>
                  <th>Matched Experience</th>
                  <th>Matching %</th>
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
      <div class="hrm-ext-search-bar" style="margin-bottom: 8px;">
        <input type="text" id="hrm-ext-job-search-input" class="hrm-ext-input" style="flex: 1; min-width: 0;" placeholder="Semantic search jobs (e.g. C++ embedded, Python tester)..." value="${escapeHtml(jobSearchQuery)}" />
        <button type="button" id="hrm-ext-job-search-btn" class="hrm-ext-btn-primary">Search</button>
        ${jobSearchQuery ? `<button type="button" id="hrm-ext-job-search-clear-btn" class="hrm-ext-sm-btn">Clear</button>` : ''}
        <button type="button" id="hrm-ext-refresh-jobs-btn" class="hrm-ext-sm-btn">Refresh Jobs</button>
      </div>
    `;

    // List of All Jobs
    let jobsListHtml = '';
    if (isLoadingJobs) {
      jobsListHtml = `<div style="text-align: center; color: #f59e0b; padding: 30px;">Loading jobs from backend...</div>`;
    } else if (backendJobs.length === 0) {
      jobsListHtml = `
        <div style="text-align: center; color: #64748b; padding: 30px;">
          ${jobSearchQuery ? `<div>No jobs match "${escapeHtml(jobSearchQuery)}".</div>` : `<div>No jobs found in backend database yet.</div><div style="font-size: 11px; margin-top: 6px;">Navigate to HRM Job Request pages to auto-ingest jobs.</div>`}
        </div>
      `;
    } else {
      let rows = '';
      backendJobs.forEach((j, idx) => {
        const relevanceBadge = j.query_relevance !== undefined
          ? `<span class="hrm-ext-badge status-purple" style="margin-left: 6px;">${j.query_relevance}% match</span>`
          : '';

        rows += `
          <tr>
            <td style="width: 36px; text-align: center; font-weight: 600; color: #64748b;">${idx + 1}</td>
            <td>
              <div style="display: flex; align-items: center;">
                <span style="font-weight: 600; color: #1e293b;">${escapeHtml(j.title || 'Untitled')}</span>
                ${relevanceBadge}
              </div>
              <div style="font-size: 11px; color: #64748b; margin-top: 2px;">${escapeHtml(j.code || '')} • ${escapeHtml(j.request ? j.request.substring(0, 70) + '...' : '')}</div>
            </td>
            <td style="width: 100px; text-align: center;">
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
                <th style="width: 36px; text-align: center;">#</th>
                <th>Job Title</th>
                <th style="text-align: center;">Action</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    }

    return `
      <div style="display: flex; flex-direction: column; gap: 8px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 700; color: #1e293b; font-size: 13px;">All Saved Jobs (${backendJobs.length})</span>
        </div>
        ${searchBarHtml}
        ${jobsListHtml}
      </div>
    `;

  }

  // 3. Candidate Search View
  function renderCandidateSearchView() {
    if (selectedCandidate) {
      // Selected Candidate Matching Jobs Table view
      let rowsHtml = '';
      if (isLoadingCandidateJobs) {
        rowsHtml = `<tr><td colspan="4" style="text-align: center; padding: 25px; color: #f59e0b;">Loading matching jobs from backend...</td></tr>`;
      } else if (selectedCandidateJobs.length === 0) {
        rowsHtml = `<tr><td colspan="4" style="text-align: center; padding: 25px; color: #64748b;">No matching jobs found for this candidate.</td></tr>`;
      } else {
        selectedCandidateJobs.forEach((j, idx) => {
          rowsHtml += `
            <tr>
              <td style="width: 36px; text-align: center; font-weight: 600; color: #64748b;">${idx + 1}</td>
              <td>
                <div style="font-weight: 600; color: #1e293b;">${escapeHtml(j.job_title || j.title || 'Untitled Job')}</div>
                <div style="font-size: 11px; color: #64748b;">${escapeHtml(j.code || '')}</div>
              </td>
              <td style="font-size: 12px; color: #334155; max-width: 260px;">${escapeHtml(j.matched_requests || 'Fulfills job requirements')}</td>
              <td style="width: 110px;">${renderMatchPercent(j.matching_percentage)}</td>
            </tr>
          `;
        });
      }

      return `
        <div style="display: flex; flex-direction: column; gap: 10px;">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <button class="hrm-ext-sm-btn" id="hrm-ext-back-candidates-btn" type="button">‹ Back to Search</button>
            <span style="font-size: 12px; color: #64748b;">${selectedCandidateJobs.length} Matching Job(s)</span>
          </div>
          <div style="background: #f8fafc; padding: 12px; border-radius: 8px; border: 1px solid #e2e8f0;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <div>
                <div style="font-size: 14px; font-weight: 700; color: #1e293b;">${escapeHtml(selectedCandidate.name || 'Candidate')}</div>
                <div style="font-size: 12px; color: #475569; margin-top: 2px;">${escapeHtml(selectedCandidate.position || '')} • ${escapeHtml(selectedCandidate.location || '')}</div>
              </div>
              <div>${renderStatusBadge(selectedCandidate.status)}</div>
            </div>
          </div>
          <div class="hrm-ext-table-container">
            <table class="hrm-ext-table">
              <thead>
                <tr>
                  <th style="width: 36px; text-align: center;">#</th>
                  <th>Job Title</th>
                  <th>Matched Requests</th>
                  <th>Matching %</th>
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
      resultsHtml = `<div style="text-align: center; color: #f59e0b; padding: 25px;">Searching candidates in backend...</div>`;
    } else if (candidateSearchResults.length > 0) {
      let rows = '';
      candidateSearchResults.forEach((c, idx) => {
        const skillsList = Array.isArray(c.extracted_keywords)
          ? c.extracted_keywords.slice(0, 4).map(s => `<span class="hrm-ext-skill-tag">${escapeHtml(s)}</span>`).join(' ')
          : '';

        rows += `
          <tr>
            <td style="width: 36px; text-align: center; font-weight: 600; color: #64748b;">${idx + 1}</td>
            <td>
              <div style="font-weight: 600; color: #1e293b;">${escapeHtml(c.name || 'Candidate')}</div>
              <div style="font-size: 11px; color: #64748b;">${escapeHtml(c.code || '')} • ${escapeHtml(c.position || '')} • ${escapeHtml(c.location || '')}</div>
              <div style="margin-top: 4px;">${skillsList}</div>
            </td>
            <td>${renderStatusBadge(c.status)}</td>
            <td style="width: 120px; text-align: center;">
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
                <th style="width: 36px; text-align: center;">#</th>
                <th>Candidate Details</th>
                <th>Status</th>
                <th style="text-align: center;">Action</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      `;
    } else if (candidateSearchQuery) {
      resultsHtml = `<div style="text-align: center; color: #64748b; padding: 25px;">No candidates match query "${escapeHtml(candidateSearchQuery)}".</div>`;
    } else {
      resultsHtml = `
        <div style="text-align: center; color: #64748b; padding: 30px;">
          <div>Search candidates across all saved records.</div>
          <div style="font-size: 11px; margin-top: 6px;">Try queries like: <code>Python tester</code>, <code>C++ embedded</code>, or <code>PM_ROUND</code>.</div>
        </div>
      `;
    }

    const candCount = candidateSearchQuery ? candidateSearchResults.length : totalCandidatesCount;

    return `
      <div style="display: flex; flex-direction: column; gap: 8px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <span style="font-weight: 700; color: #1e293b; font-size: 13px;">
            ${candidateSearchQuery ? `Candidate Results (${candCount})` : `All Saved Candidates (${totalCandidatesCount})`}
          </span>
        </div>
        <div class="hrm-ext-search-bar" style="margin-bottom: 8px;">
          <input type="text" id="hrm-ext-candidate-search-input" class="hrm-ext-input" style="flex: 1; min-width: 0;" placeholder="Search candidate skills, title, status..." value="${escapeHtml(candidateSearchQuery)}" />
          <button type="button" id="hrm-ext-candidate-search-btn" class="hrm-ext-btn-primary">Search</button>
          ${candidateSearchQuery ? `<button type="button" id="hrm-ext-candidate-search-clear-btn" class="hrm-ext-sm-btn">Clear</button>` : ''}
          <button type="button" id="hrm-ext-refresh-candidates-btn" class="hrm-ext-sm-btn">Refresh Candidates</button>
        </div>
        ${resultsHtml}
      </div>
    `;
  }

  // 4. Backend Settings View
  function renderSettingsView() {
    const currentModelInfo = availableModels.find(m => m.id === activeModel) || {
      id: activeModel || 'BAAI/bge-small-en-v1.5',
      name: activeModel ? activeModel.split('/').pop() : 'BGE Small English (Default)',
      dim: 384,
      size: '~67MB'
    };

    const modelOptionsHtml = availableModels.map(m => `
      <option value="${escapeHtml(m.id)}" ${m.id === activeModel ? 'selected' : ''}>
        ${escapeHtml(m.name)} (${m.dim}d, ${m.size})
      </option>
    `).join('');

    return `
      <div style="display: flex; flex-direction: column; gap: 14px; width: 100%; box-sizing: border-box;">
        <!-- Backend Connection -->

        <div class="hrm-ext-card">
          <div class="hrm-ext-card-title">Backend Connection</div>
          <div class="hrm-ext-field">
            <div class="hrm-ext-field-label">Backend Service URL</div>
            <div style="display: flex; gap: 8px; margin-top: 4px;">
              <input type="text" id="hrm-ext-backend-url-input" class="hrm-ext-input" style="flex: 1;" value="${escapeHtml(getBackendUrl())}" />
              <button type="button" id="hrm-ext-save-url-btn" class="hrm-ext-sm-btn">Save</button>
            </div>
            <div style="font-size: 11px; color: #64748b; margin-top: 4px;">Default port: 8765 (http://localhost:8765)</div>
          </div>
          <div style="margin-top: 10px; display: flex; gap: 8px; align-items: center;">
            <button type="button" id="hrm-ext-test-health-btn" class="hrm-ext-btn-primary">Test Connection</button>
            <div id="hrm-ext-health-result" style="font-size: 12px; color: #475569;"></div>
          </div>
        </div>

        <!-- Local AI Model Selection -->
        <div class="hrm-ext-card">
          <div class="hrm-ext-card-title">Local AI Semantic Model</div>

          <!-- Current Selected Model Display -->
          <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px 12px;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
              <span style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.03em;">CURRENT SELECTED MODEL</span>
              <span class="hrm-ext-badge ${isModelLoaded ? 'status-green' : 'status-amber'}" style="font-size: 10px;">
                ${isModelLoaded ? 'Active & Loaded' : 'Fallback Vectorizer'}
              </span>
            </div>
            <div style="font-size: 14px; font-weight: 700; color: #1e293b; margin-top: 4px;">
              ${escapeHtml(currentModelInfo.name || currentModelInfo.id)}
            </div>
            <div style="display: flex; gap: 8px; align-items: center; margin-top: 4px; font-size: 11px; color: #475569; flex-wrap: wrap;">
              <span class="hrm-ext-badge status-purple">${currentModelInfo.dim} Dimensions</span>
              <span>Size: <strong>${currentModelInfo.size}</strong></span>
              <span style="color: #94a3b8;">•</span>
              <span style="font-family: monospace; color: #64748b;">${escapeHtml(currentModelInfo.id)}</span>
            </div>
          </div>

          <div class="hrm-ext-card-desc" style="margin-top: 6px;">
            Choose a local embedding model for semantic candidate-job matching:
          </div>
          <div style="display: flex; gap: 8px; margin-top: 4px;">
            <select id="hrm-ext-model-select" class="hrm-ext-select" style="flex: 1;">
              ${modelOptionsHtml || `<option value="${escapeHtml(activeModel)}">${escapeHtml(activeModel || 'Loading models...')}</option>`}
            </select>
            <button type="button" id="hrm-ext-apply-model-btn" class="hrm-ext-btn-primary" ${isSwitchingModel ? 'disabled' : ''}>${isSwitchingModel ? 'Applying...' : 'Apply Model'}</button>
          </div>
          <div id="hrm-ext-model-feedback" style="font-size: 11px; color: #64748b; margin-top: 6px;">
            Note: Changing models requires clearing the database first to prevent vector dimension mismatches.
          </div>
        </div>

        <!-- Database Management -->
        <div class="hrm-ext-card hrm-ext-danger-card">
          <div class="hrm-ext-card-title" style="color: #b91c1c;">Database Management</div>
          <div class="hrm-ext-card-desc">Clear all stored jobs, candidates, and pre-calculated matches from SQLite:</div>
          <div style="margin-top: 8px; display: flex; gap: 8px; align-items: center;">
            <button type="button" id="hrm-ext-clear-db-btn" class="hrm-ext-btn-danger" ${isClearingDb ? 'disabled' : ''}>${isClearingDb ? 'Clearing...' : 'Clear All Database'}</button>
            <div id="hrm-ext-clear-db-feedback" style="font-size: 12px; color: #64748b;"></div>
          </div>
        </div>
      </div>
    `;
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
      bodyEl.innerHTML = `
        <!-- Main Navigation Bar -->
        <div class="hrm-ext-main-nav">
          <button class="hrm-ext-nav-btn ${mainPage === 'active' ? 'active' : ''}" type="button" data-nav="active">Active Page</button>
          <button class="hrm-ext-nav-btn ${mainPage === 'jobs' ? 'active' : ''}" type="button" data-nav="jobs">All Jobs</button>
          <button class="hrm-ext-nav-btn ${mainPage === 'search' ? 'active' : ''}" type="button" data-nav="search">Search Candidates</button>
          <button class="hrm-ext-nav-btn ${mainPage === 'settings' ? 'active' : ''}" type="button" data-nav="settings">Settings</button>
        </div>
        <div class="hrm-ext-page-container">${pageContent}</div>
      `;

      bindDrawerEvents(bodyEl);
    }
    updateHeaderSyncStatus();
  }

  /**
   * Bind event listeners for drawer views
   */
  function bindDrawerEvents(container) {
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
          loadModels();
        }
        updateDrawerContent();
      });
    });

    // 2. Sub-tabs in active page
    const tabBtns = container.querySelectorAll('.hrm-ext-tab-btn');
    tabBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        activeTabName = btn.dataset.tab;
        updateDrawerContent();
      });
    });

    // 3. Jobs view events
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

    // 4. Candidate Search events
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

    // 5. Settings events
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
        healthResultEl.innerHTML = '<span style="color: #f59e0b;">Checking backend...</span>';
        try {
          const res = await fetch(`${getBackendUrl()}/api/health`);
          if (res.ok) {
            const data = await res.json();
            healthResultEl.innerHTML = `
              <span style="color: #10b981; font-weight: 600;">Connected</span>
              <div style="font-size: 11px; color: #64748b;">Jobs: ${data.stats?.jobs_count || 0} | Candidates: ${data.stats?.candidates_count || 0} | Matches: ${data.stats?.matches_count || 0}</div>
            `;
          } else {
            healthResultEl.innerHTML = `<span style="color: #ef4444;">HTTP ${res.status} error</span>`;
          }
        } catch (err) {
          healthResultEl.innerHTML = `<span style="color: #ef4444;">Error: ${escapeHtml(err.message)}</span>`;
        }
      });
    }

    const applyModelBtn = container.querySelector('#hrm-ext-apply-model-btn');
    const modelSelect = container.querySelector('#hrm-ext-model-select');
    const modelFeedbackEl = container.querySelector('#hrm-ext-model-feedback');
    if (modelSelect && modelFeedbackEl) {
      modelSelect.addEventListener('change', () => {
        const chosen = modelSelect.value;
        if (chosen !== activeModel) {
          const chosenInfo = availableModels.find(m => m.id === chosen);
          const currentInfo = availableModels.find(m => m.id === activeModel);
          const dimDiff = currentInfo && chosenInfo && currentInfo.dim !== chosenInfo.dim
            ? ` (${currentInfo.dim}d → ${chosenInfo.dim}d)`
            : '';
          modelFeedbackEl.innerHTML = `<span style="color: #b45309; font-weight: 600;">Selected: ${escapeHtml(chosenInfo?.name || chosen)}${dimDiff}. Note: Changing models requires clearing the database first.</span>`;
        } else {
          modelFeedbackEl.innerHTML = `<span style="color: #64748b;">Note: Changing models requires clearing the database first to prevent vector dimension mismatches.</span>`;
        }
      });
    }

    if (applyModelBtn && modelSelect) {
      applyModelBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const chosenModel = modelSelect.value;
        const currentModelObj = availableModels.find(m => m.id === activeModel) || { id: activeModel, name: activeModel || 'Current Model', dim: 384 };
        const newModelObj = availableModels.find(m => m.id === chosenModel) || { id: chosenModel, name: chosenModel, dim: 384 };

        if (chosenModel === activeModel) {
          if (modelFeedbackEl) {
            modelFeedbackEl.innerHTML = `<span style="color: #10b981; font-weight: 600;">Model '${escapeHtml(currentModelObj.name)}' is already currently selected.</span>`;
          }
          return;
        }

        // Query database stats to check if existing records exist
        let jobsCount = 0;
        let candsCount = 0;
        try {
          const healthRes = await fetch(`${getBackendUrl()}/api/health`);
          if (healthRes.ok) {
            const healthData = await healthRes.json();
            jobsCount = healthData.stats?.jobs_count || 0;
            candsCount = healthData.stats?.candidates_count || 0;
          }
        } catch (err) {
          console.warn('[HRM Extension] Failed to check db stats:', err);
        }

        const hasData = (jobsCount > 0 || candsCount > 0);

        const executeSwitch = async (clearDb) => {
          isSwitchingModel = true;
          if (modelFeedbackEl) {
            modelFeedbackEl.innerHTML = '<span style="color: #f59e0b;">Switching model in memory...</span>';
          }
          updateDrawerContent();

          try {
            if (clearDb) {
              await fetch(`${getBackendUrl()}/api/database/clear`, { method: 'POST' });
              backendJobs = [];
              candidateSearchResults = [];
              selectedJob = null;
              selectedCandidate = null;
              backendSyncStatus = { synced: false, time: null, count: 0, newCount: 0, updatedCount: 0 };
              totalCandidatesCount = 0;
            }


            const res = await fetch(`${getBackendUrl()}/api/models/select`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ model_name: chosenModel, clear_database: clearDb })
            });
            if (res.ok) {
              const data = await res.json();
              activeModel = data.active_model;
              isModelLoaded = data.is_loaded;
              alert(`Switched model to: ${data.active_model}${clearDb ? '\nDatabase was cleared to prevent vector dimension mismatch.' : ''}`);
            } else {
              alert(`Failed to switch model (HTTP ${res.status})`);
            }
          } catch (err) {
            alert(`Model selection error: ${err.message}`);
          } finally {
            isSwitchingModel = false;
            updateDrawerContent();
          }
        };

        if (hasData) {
          const dimensionNote = currentModelObj.dim !== newModelObj.dim
            ? `Embedding dimension changes from <strong>${currentModelObj.dim}d</strong> to <strong>${newModelObj.dim}d</strong>.`
            : `Different model architectures use incompatible latent coordinate spaces.`;

          // Show popup to tell user to clear database first
          showModal({
            title: 'Dimension Mismatch: Clear Database Required',
            bodyHtml: `
              <div style="background: #fef2f2; border: 1px solid #fecaca; border-radius: 6px; padding: 10px 12px; color: #991b1b;">
                <strong>Warning:</strong> The database currently contains <strong>${jobsCount} job(s)</strong> and <strong>${candsCount} candidate(s)</strong> embedded with the previous model.
              </div>
              <div style="color: #334155; font-size: 12px; line-height: 1.5;">
                Switching from <strong>${escapeHtml(currentModelObj.name)}</strong> (${currentModelObj.dim}d) to <strong>${escapeHtml(newModelObj.name)}</strong> (${newModelObj.dim}d) causes vector dimension and latent space mismatches with existing data. ${dimensionNote}
              </div>
              <div style="background: #fffbeb; border: 1px solid #fde68a; border-radius: 6px; padding: 8px 12px; font-size: 11px; color: #b45309;">
                <strong>Please clear the database first</strong> before changing the model so all jobs and candidates are re-embedded consistently.
              </div>
            `,
            confirmText: 'Clear Database & Switch',
            confirmClass: 'hrm-ext-btn-danger',
            cancelText: 'Cancel',
            onConfirm: async () => {
              await executeSwitch(true);
            },
            onCancel: () => {
              if (modelSelect) modelSelect.value = activeModel;
              if (modelFeedbackEl) {
                modelFeedbackEl.innerHTML = `<span style="color: #64748b;">Model switch cancelled. Cleared database required first.</span>`;
              }
            }
          });
        } else {
          // Database is already empty, switch directly
          await executeSwitch(false);
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

  async function loadModels() {
    try {
      const res = await fetch(`${getBackendUrl()}/api/models`);
      if (res.ok) {
        const data = await res.json();
        availableModels = data.models || [];
        activeModel = data.active_model;
        isModelLoaded = data.is_loaded;
        updateDrawerContent();
      }
    } catch (e) {
      console.warn('[HRM Extension] Failed to load models:', e);
    }
  }

  /**
   * Helper to display a clean in-drawer modal dialog (for dimension warnings, confirmations, etc.)
   */
  function showModal({ title, bodyHtml, confirmText, confirmClass, cancelText, onConfirm, onCancel }) {
    if (!shadowRoot) return;
    const container = shadowRoot.querySelector('#hrm-ext-modal-container');
    if (!container) return;

    container.innerHTML = `
      <div class="hrm-ext-modal-overlay">
        <div class="hrm-ext-modal-card">
          <div class="hrm-ext-modal-header">
            <div class="hrm-ext-modal-title">${escapeHtml(title)}</div>
            <button type="button" class="hrm-ext-header-btn modal-close-btn" style="font-size: 16px; padding: 2px 6px;">&times;</button>
          </div>
          <div class="hrm-ext-modal-body">${bodyHtml}</div>
          <div class="hrm-ext-modal-actions">
            ${cancelText ? `<button type="button" class="hrm-ext-sm-btn modal-cancel-btn">${escapeHtml(cancelText)}</button>` : ''}
            ${confirmText ? `<button type="button" class="${confirmClass || 'hrm-ext-btn-primary'} modal-confirm-btn">${escapeHtml(confirmText)}</button>` : ''}
          </div>
        </div>
      </div>
    `;

    const close = () => {
      container.innerHTML = '';
    };

    const closeBtn = container.querySelector('.modal-close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        close();
        if (onCancel) onCancel();
      });
    }

    const cancelBtn = container.querySelector('.modal-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        close();
        if (onCancel) onCancel();
      });
    }

    const confirmBtn = container.querySelector('.modal-confirm-btn');
    if (confirmBtn) {
      confirmBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        close();
        if (onConfirm) await onConfirm();
      });
    }

    const overlay = container.querySelector('.hrm-ext-modal-overlay');
    if (overlay) {
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) {
          e.stopPropagation();
          close();
          if (onCancel) onCancel();
        }
      });
    }
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
      <button id="hrm-ext-floating-btn" type="button" aria-label="HRM Extension" title="Open HRM Assistant">
        <img src="${iconUrl}" alt="HRM" />
        <span id="hrm-ext-status-dot" class="idle"></span>
        <span id="hrm-ext-badge-count"></span>
      </button>
      <div id="hrm-ext-drawer">
        <div class="hrm-ext-header">
          <div class="hrm-ext-title">
            <span>⚡ HRM Assistant</span>
          </div>
          <div class="hrm-ext-header-actions">
            <div id="hrm-ext-header-sync-status"></div>
            <button class="hrm-ext-header-btn hrm-ext-header-resync-btn" type="button" id="hrm-ext-header-resync-btn" title="Resync to Backend">Resync</button>
            <button class="hrm-ext-header-btn hrm-ext-expand-btn" type="button" id="hrm-ext-expand-btn" title="Expand to Full Page">⤢</button>
            <button class="hrm-ext-header-btn hrm-ext-close-btn" type="button" id="hrm-ext-close-btn" title="Close">&times;</button>
          </div>
        </div>
        <div class="hrm-ext-body"></div>
        <div id="hrm-ext-modal-container"></div>
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
            return {
              id: item?.cv?.id || item?.id || null,
              application_id: item?.id || null,
              code: item?.cv?.code || null,
              name: item?.cv?.name ? item.cv.name.trim() : null,
              position: item?.cv?.position || null,
              location: item?.cv?.location || null,
              status: item?.status || item?.cv?.status || 'OPEN',
              cvs: Array.isArray(cvFiles) ? cvFiles : [cvFiles],
              cvInformation: item?.cv?.cvInformation || null,
              experience: item?.cv?.experience || null,
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
