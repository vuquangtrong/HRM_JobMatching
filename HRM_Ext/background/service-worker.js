/**
 * HRM Extension Background Service Worker (Manifest V3)
 */

chrome.runtime.onInstalled.addListener((details) => {
  console.log(`[HRM Extension] Background service worker installed/updated: ${details.reason}`);
});

// Watch for SPA History API changes natively via webNavigation
if (chrome.webNavigation) {
  chrome.webNavigation.onHistoryStateUpdated.addListener(async (details) => {
    if (details.url && details.url.includes('hrm.ltsgroup.tech')) {
      console.log(`[HRM Extension] (webNavigation) Tab ${details.tabId} SPA navigated to: ${details.url}`);
      try {
        await chrome.tabs.sendMessage(details.tabId, {
          type: 'URL_UPDATED',
          url: details.url
        });
      } catch (err) {
        // Content script might not be injected yet
      }
    }
  });

  chrome.webNavigation.onCommitted.addListener(async (details) => {
    if (details.url && details.url.includes('hrm.ltsgroup.tech')) {
      try {
        await chrome.tabs.sendMessage(details.tabId, {
          type: 'URL_UPDATED',
          url: details.url
        });
      } catch (err) {
        // Content script might not be injected yet
      }
    }
  });
}

// Watch for tab URL updates (especially helpful for tab navigations or transitions)
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  const targetUrl = changeInfo.url || tab?.url;
  if (targetUrl && targetUrl.includes('hrm.ltsgroup.tech')) {
    console.log(`[HRM Extension] (tabs.onUpdated) Tab ${tabId} navigated to: ${targetUrl}`);

    try {
      await chrome.tabs.sendMessage(tabId, {
        type: 'URL_UPDATED',
        url: targetUrl
      });
    } catch (err) {
      // Content script might not be injected yet or tab is still loading
    }
  }
});

// Toggle in-page drawer when toolbar extension action icon is clicked
chrome.action.onClicked.addListener(async (tab) => {
  if (tab?.id) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: 'TOGGLE_DRAWER' });
    } catch (err) {
      console.log(`[HRM Extension] Could not toggle drawer on tab: ${err.message}`);
    }
  }
});
