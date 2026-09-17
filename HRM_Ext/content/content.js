/**
 * HRM Extension Content Script
 * Injected into https://hrm.ltsgroup.tech/recruitment*
 */

(() => {
  'use strict';

  console.log(`[HRM Extension] Injected & Loaded successfully on: ${window.location.href}`);

  // Helper to extract Job Request ID from various route formats
  function extractJobRequestId(url = window.location.href) {
    if (!url) return null;

    // Pattern 1: /recruitment/job-requests/candidate/<id> or /recruitment/job-request/candidate/<id>
    const match1 = url.match(/\/recruitment\/job-requests?\/candidate\/([a-zA-Z0-9_-]+)/i);
    if (match1 && match1[1] && match1[1] !== 'undefined') return match1[1];

    // Pattern 2: /recruitment/job-requests/<id>/candidate
    const match2 = url.match(/\/recruitment\/job-requests?\/([a-zA-Z0-9_-]+)\/candidate/i);
    if (match2 && match2[1] && match2[1] !== 'undefined') return match2[1];

    // Pattern 3: Query parameter (e.g. ?jobRequestId=<id> or ?job_request_id=<id>)
    try {
      const parsed = new URL(url, window.location.origin);
      const qId = parsed.searchParams.get('jobRequestId') ||
                  parsed.searchParams.get('job_request_id') ||
                  parsed.searchParams.get('id');
      if (qId && /^[a-zA-Z0-9_-]{5,}$/.test(qId)) return qId;
    } catch (e) {}

    return null;
  }

  // State
  let currentJobRequestId = null;
  let fetchedJobRequestId = null;
  let extractedData = null;
  let previousUrl = window.location.href;
  let isFetching = false;
  let activeTabName = 'table'; // 'table' | 'spec' | 'json'

  // DOM Elements inside Shadow Root
  let shadowRoot = null;
  let floatingRoot = null;
  let statusDot = null;
  let badgeCountEl = null;
  let tooltipEl = null;
  let drawerEl = null;
  let expandBtn = null;
  let headerFetchBtn = null;

  /**
   * Safe extraction of access token from localStorage.auth_tconnect
   */
  function getAccessToken() {
    try {
      const raw = localStorage.getItem('auth_tconnect');
      if (!raw) {
        console.warn('[HRM Extension] localStorage.auth_tconnect not found');
        return null;
      }
      const auth = JSON.parse(raw);
      return auth?.accessToken || auth?.access_token || auth?.token || null;
    } catch (e) {
      console.error('[HRM Extension] Failed to parse auth_tconnect from localStorage:', e);
      return null;
    }
  }

  /**
   * Update the status dot, tooltip & candidate count badge
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
   * Toggle between normal drawer and full-page drawer
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

  /**
   * Update drawer content and render candidate table or json
   */
  function updateDrawerContent() {
    // Close the chatbot when updating the drawer content
    sessionStorage.setItem('chatbot_state', JSON.stringify({ isOpen: false }));

    // Ensure drawer element exists before updating content
    if (!drawerEl) return;

    const token = getAccessToken();
    const tokenStatus = token ? 'Valid (detected)' : 'Not found in localStorage';
    const candsCount = extractedData?.candidates?.length || 0;
    const reqTitle = extractedData?.jobRequest?.title || '';

    let contentHtml = `
      <div style="background: #f8fafc; padding: 10px 14px; border-radius: 8px; border: 1px solid #e2e8f0; display: flex; flex-direction: column; gap: 8px;">
        ${reqTitle ? `
        <div class="hrm-ext-field" style="margin-bottom: 0;">
          <div class="hrm-ext-field-label">Job Title</div>
          <div class="hrm-ext-field-value" style="font-size: 13px; font-weight: 700; color: #1e293b;">${escapeHtml(reqTitle)}</div>
        </div>` : ''}
      </div>
    `;

    if (extractedData) {
      // Tabs header
      contentHtml += `
        <div class="hrm-ext-tabs">
          <button class="hrm-ext-tab-btn ${activeTabName === 'table' ? 'active' : ''}" type="button" data-tab="table">Candidates Table (${candsCount})</button>
          <button class="hrm-ext-tab-btn ${activeTabName === 'spec' ? 'active' : ''}" type="button" data-tab="spec">Job Request Details</button>
          <button class="hrm-ext-tab-btn ${activeTabName === 'json' ? 'active' : ''}" type="button" data-tab="json">Raw JSON</button>
        </div>
      `;

      if (activeTabName === 'table') {
        // Table View: columns #, Candidate Name (with CV attachment button), Code, Position, Location
        const cands = extractedData.candidates || [];
        if (cands.length > 0) {
          let rowsHtml = '';
          cands.forEach((cand, idx) => {
            const cvButtonsHtml = Array.isArray(cand.cvs) && cand.cvs.length > 0
              ? cand.cvs.map((url, i) => {
                  const label = cand.cvs.length === 1 ? '📄' : `📄 ${i + 1}`;
                  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" class="hrm-ext-cv-btn" title="Open CV document (${escapeHtml(url)})">${label}</a>`;
                }).join(' ')
              : '';

            rowsHtml += `
              <tr>
                <td style="font-weight: 600; color: #64748b; width: 36px; text-align: center;">${idx + 1}</td>
                <td class="cand-name-col">
                  <div style="display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                    <span>${escapeHtml(cand.name || 'N/A')}</span>
                    ${cvButtonsHtml}
                  </div>
                </td>
                <td class="cand-code-col">${escapeHtml(cand.code || 'N/A')}</td>
                <td class="cand-text-col">${escapeHtml(cand.position || 'N/A')}</td>
                <td class="cand-text-col">${escapeHtml(cand.location || 'N/A')}</td>
              </tr>
            `;
          });

          contentHtml += `
            <div class="hrm-ext-table-container">
              <table class="hrm-ext-table">
                <thead>
                  <tr>
                    <th style="width: 36px; text-align: center;">#</th>
                    <th>Candidate Name</th>
                    <th>Code</th>
                    <th>Position</th>
                    <th>Location</th>
                  </tr>
                </thead>
                <tbody>
                  ${rowsHtml}
                </tbody>
              </table>
            </div>
          `;
        } else {
          contentHtml += `<div style="text-align: center; color: #64748b; padding: 20px;">No candidates attached to this job request yet.</div>`;
        }
      } else if (activeTabName === 'spec') {
        // Job Request Details View: id, title, request, jobDescription
        const req = extractedData.jobRequest || {};
        contentHtml += `
          <div style="display: flex; flex-direction: column; gap: 10px; background: #ffffff; padding: 14px; border: 1px solid #e2e8f0; border-radius: 8px;">
            <div class="hrm-ext-field">
              <div class="hrm-ext-field-label">Request Summary</div>
              <div class="hrm-ext-field-value" style="white-space: pre-line;">${escapeHtml(req.request || 'N/A')}</div>
            </div>
            ${req.jobDescription ? `
            <div class="hrm-ext-field">
              <div class="hrm-ext-field-label">Job Description (HTML)</div>
              <div class="hrm-ext-field-value" style="max-height: 280px; overflow-y: auto; border: 1px solid #f1f5f9; padding: 8px; border-radius: 4px;">${req.jobDescription}</div>
            </div>` : ''}
          </div>
        `;
      } else if (activeTabName === 'json') {
        // Raw JSON View
        contentHtml += `
          <div class="hrm-ext-json-box">${escapeHtml(JSON.stringify(extractedData, null, 2))}</div>
        `;
      }
    } else if (isFetching) {
      contentHtml += `
        <div style="text-align: center; color: #f59e0b; padding: 30px 10px;">
          <div style="font-weight: 600; margin-bottom: 6px;">Fetching Job Request & Candidate CVs...</div>
          <div style="font-size: 11px; color: #64748b;">Querying /api/job-requests and /api/candidate/candidates in parallel</div>
        </div>
      `;
    } else if (currentJobRequestId) {
      contentHtml += `
        <div style="text-align: center; color: #64748b; padding: 30px 10px;">
          <div>Job Request #${currentJobRequestId} detected.</div>
          <div style="font-size: 12px; margin-top: 6px;">Click <strong>⚡ Fetch Data</strong> in the header to extract and populate candidate table.</div>
        </div>
      `;
    } else {
      contentHtml += `
        <div style="text-align: center; color: #64748b; padding: 30px 10px;">
          Navigate to a Job Request candidate page:
          <div style="margin-top: 6px; font-family: ui-monospace, monospace; font-size: 11px; color: #2563eb;">/recruitment/job-requests/candidate/&lt;jobRequestsId&gt;</div>
        </div>
      `;
    }

    const bodyEl = drawerEl.querySelector('.hrm-ext-body');
    if (bodyEl) {
      bodyEl.innerHTML = contentHtml;

      // Bind tab buttons
      const tabBtns = bodyEl.querySelectorAll('.hrm-ext-tab-btn');
      tabBtns.forEach((btn) => {
        btn.addEventListener('click', (e) => {
          e.stopPropagation();
          activeTabName = btn.dataset.tab;
          updateDrawerContent();
        });
      });
    }
  }

  function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  /**
   * Create and mount the in-page floating widget inside Shadow DOM
   */
  function mountFloatingWidget() {
    if (document.getElementById('hrm-ext-host')) return;

    // Create Host Container
    const hostEl = document.createElement('div');
    hostEl.id = 'hrm-ext-host';
    hostEl.style.all = 'initial';

    // Attach Open Shadow Root for complete CSS isolation
    shadowRoot = hostEl.attachShadow({ mode: 'open' });

    // Link external stylesheet inside Shadow Root
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
            <button class="hrm-ext-header-btn hrm-ext-btn-fetch" type="button" id="hrm-ext-header-fetch-btn" title="Fetch / Refresh Data">⚡ Fetch Data</button>
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
    headerFetchBtn = shadowRoot.querySelector('#hrm-ext-header-fetch-btn');

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

    headerFetchBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (currentJobRequestId) {
        await fetchJobRequestAndCandidates(currentJobRequestId, true);
      } else {
        alert('Not currently on a job request candidate page (/recruitment/job-requests/candidate/<jobRequestsId>)');
      }
    });

    // Close drawer when clicking outside (only if not in full-page mode)
    document.addEventListener('click', (e) => {
      if (drawerEl && drawerEl.classList.contains('open') && !drawerEl.classList.contains('full-page')) {
        const path = e.composedPath();
        if (!path.includes(floatingRoot)) {
          drawerEl.classList.remove('open');
        }
      }
    });

    updateIndicator('idle', 'HRM Extension: Idle (Navigate to a Job Request)');
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
   * Fetch both Job Request and Candidate CVs data, extracting target fields
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

      // Fetch both endpoints concurrently
      const [jobReqRaw, candListRaw] = await Promise.all([
        fetchHrmApi(jobRequestUrl, token),
        fetchHrmApi(candidatesUrl, token)
      ]);

      // 1. Extract specified Job Request fields (id, title, request, jobDescription)
      const jobRequest = {
        id: jobReqRaw?.id || jobRequestId,
        title: jobReqRaw?.title || jobReqRaw?.name || null,
        request: jobReqRaw?.request || null,
        jobDescription: jobReqRaw?.jobDescription || null,
      };

      // 2. Extract specified Candidate fields (id, code, name, position, location, cvs)
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
              id: item?.cv?.id || null,
              code: item?.cv?.code || null,
              name: item?.cv?.name ? item.cv.name.trim() : null,
              position: item?.cv?.position || null,
              location: item?.cv?.location || null,
              cvs: Array.isArray(cvFiles) ? cvFiles : [cvFiles],
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

      console.log(`[HRM Extension] Successfully fetched Job Request [${jobRequestId}] with ${candidates.length} candidate(s)`);

      updateIndicator('success', `Job Request #${jobRequestId} (${candidates.length} candidates) loaded`, candidates.length);
      updateDrawerContent();
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
   * Check current URL and trigger fetch if matching job request candidate route,
   * or auto-close drawer and reset state if navigated away.
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

        // Automatically fetch job request and candidate data
        try {
          await fetchJobRequestAndCandidates(detectedJobRequestId);
        } catch (e) {
          // Logged inside fetchJobRequestAndCandidates
        }
      }
    } else {
      // URL does not match candidate route
      if (currentJobRequestId !== null || extractedData !== null || (drawerEl && drawerEl.classList.contains('open'))) {
        console.log(`[HRM Extension] Route no longer matches candidate URL (${currentUrl}). Closing drawer and resetting.`);
        currentJobRequestId = null;
        fetchedJobRequestId = null;
        extractedData = null;

        // Auto-close drawer when navigating away to non-candidate pages
        if (drawerEl) {
          drawerEl.classList.remove('open');
          drawerEl.classList.remove('full-page');
          if (expandBtn) {
            expandBtn.textContent = '⤢';
            expandBtn.title = 'Expand to Full Page';
          }
        }

        updateIndicator('idle', 'HRM Extension: Idle (Navigate to a Job Request)');
        updateDrawerContent();
      }
    }
  }

  /**
   * Monitor SPA URL changes across multiple interception layers:
   * 1. Main-world postMessage & CustomEvent 'hrm_spa_nav' (from bridge.js)
   * 2. Background service worker webNavigation & tabs messages ('URL_UPDATED')
   * 3. Direct document-wide click capture with multi-interval polling
   * 4. Native popstate and hashchange events
   * 5. Document title / DOM mutation observer
   * 6. Fast 150ms interval polling fallback
   */
  function setupUrlMonitoring() {
    // 1a. Listen for postMessage from main-world bridge
    window.addEventListener('message', (event) => {
      if (event.data && event.data.source === 'hrm_bridge' && event.data.type === 'HRM_SPA_NAV') {
        checkUrlChange();
      }
    });

    // 1b. Listen for custom event dispatched on shared document
    document.addEventListener('hrm_spa_nav', () => {
      checkUrlChange();
    });

    // 2. Intercept ANY click in the document (links, table rows, menu items, tabs)
    document.addEventListener('click', () => {
      setTimeout(checkUrlChange, 30);
      setTimeout(checkUrlChange, 150);
      setTimeout(checkUrlChange, 350);
      setTimeout(checkUrlChange, 700);
    }, true);

    // 3. Native popstate & hashchange
    window.addEventListener('popstate', () => checkUrlChange());
    window.addEventListener('hashchange', () => checkUrlChange());

    // 4. Polling fallback for SPA changes that don't trigger events
    setInterval(() => {
      if (window.location.href !== previousUrl) {
        checkUrlChange();
      }
    }, 150);

    // 5. Watch document title mutations to catch router title changes
    try {
      const titleEl = document.querySelector('title');
      if (titleEl) {
        const titleObserver = new MutationObserver(() => {
          checkUrlChange();
        });
        titleObserver.observe(titleEl, { childList: true, characterData: true, subtree: true });
      }
    } catch (e) {}

    // Initial check
    checkUrlChange();
  }

  /**
   * Message listener for communication with background service worker
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
        hasToken: Boolean(getAccessToken()),
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

  // Mount UI when document is ready
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
