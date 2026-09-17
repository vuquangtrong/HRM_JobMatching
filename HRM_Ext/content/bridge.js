/**
 * HRM Extension - Main World Navigation Bridge
 * Injected into host page's main execution context to intercept SPA router pushState & replaceState
 */
(() => {
  'use strict';

  if (window.__hrm_bridge_installed) return;
  window.__hrm_bridge_installed = true;

  function notifyNavigation() {
    const currentUrl = window.location.href;

    // 1. Cross-world message via window.postMessage (guaranteed across worlds in Chromium)
    try {
      window.postMessage({
        source: 'hrm_bridge',
        type: 'HRM_SPA_NAV',
        url: currentUrl
      }, '*');
    } catch (e) {}

    // 2. Cross-world DOM event on the shared document object
    try {
      document.dispatchEvent(new CustomEvent('hrm_spa_nav', {
        detail: { url: currentUrl }
      }));
    } catch (e) {}

    // 3. Fallback on window in case same-world listener exists
    try {
      window.dispatchEvent(new CustomEvent('hrm_spa_nav', {
        detail: { url: currentUrl }
      }));
    } catch (e) {}
  }

  function triggerMultiTickNotify() {
    notifyNavigation();
    setTimeout(notifyNavigation, 20);
    setTimeout(notifyNavigation, 100);
  }

  // Intercept on History prototype
  if (window.History && window.History.prototype) {
    const origProtoPushState = window.History.prototype.pushState;
    if (typeof origProtoPushState === 'function') {
      window.History.prototype.pushState = function (...args) {
        const res = origProtoPushState.apply(this, args);
        triggerMultiTickNotify();
        return res;
      };
    }

    const origProtoReplaceState = window.History.prototype.replaceState;
    if (typeof origProtoReplaceState === 'function') {
      window.History.prototype.replaceState = function (...args) {
        const res = origProtoReplaceState.apply(this, args);
        triggerMultiTickNotify();
        return res;
      };
    }
  }

  // Intercept directly on window.history instance
  if (window.history) {
    const origPushState = window.history.pushState;
    if (typeof origPushState === 'function' && origPushState !== window.History?.prototype?.pushState) {
      window.history.pushState = function (...args) {
        const res = origPushState.apply(this, args);
        triggerMultiTickNotify();
        return res;
      };
    }

    const origReplaceState = window.history.replaceState;
    if (typeof origReplaceState === 'function' && origReplaceState !== window.History?.prototype?.replaceState) {
      window.history.replaceState = function (...args) {
        const res = origReplaceState.apply(this, args);
        triggerMultiTickNotify();
        return res;
      };
    }
  }

  window.addEventListener('popstate', triggerMultiTickNotify, true);
  window.addEventListener('hashchange', triggerMultiTickNotify, true);
})();
