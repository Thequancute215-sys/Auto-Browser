// Loaded only in window mode (browser_manager._launch). Adds "Phân tích AI" to the native right-click menu
// on selected text. Clicks wait in a queue that the app drains through Playwright
// (BrowserManager._poll_ext_clicks): web pages cannot reach this service worker, so a page cannot start an
// analysis (and spend the user's AI quota) on its own.
const MENU_ID = 'aib-analyze';
const clicks = [];

self.__aibTake = () => clicks.splice(0);

// The worker restarts after being idle; recreating the item each time avoids a duplicate-id error.
chrome.contextMenus.removeAll(() => {
  chrome.contextMenus.create({ id: MENU_ID, title: 'Phân tích AI', contexts: ['selection'] });
});

chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === MENU_ID) clicks.push({ text: info.selectionText || '', frameId: info.frameId || 0 });
});
