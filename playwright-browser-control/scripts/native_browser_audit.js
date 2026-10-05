// Read native browser metadata only; never adopt objects from names or URLs.
async function nativeBrowserInventory(input = {}) {
  const self = await chrome.tabs.getCurrent();
  const excluded = new Set([...(input.include_self ? [] : [self?.id]), ...(input.exclude_tab_ids || [])]);
  const groups = await chrome.tabGroups.query({});
  const tabs = (await chrome.tabs.query({})).filter(t => !excluded.has(t.id));
  const live = new Set(tabs.map(t => t.id));
  const response = await chrome.runtime.sendMessage({type: 'getConnectionStatus'});
  const connections = (response.connections || []).map(c => ({
    id: c.id, tab_ids: (c.connectedTabIds || []).filter(id => live.has(id))
  })).filter(c => c.tab_ids.length);
  const statusUrl = chrome.runtime.getURL('status.html');
  return {
    schema: 'NativeBrowserInventoryV1', captured_at: new Date().toISOString(),
    groups: groups.map(g => ({id: g.id, window_id: g.windowId,
      title: String(g.title || '').replace(/[\x00-\x1f]/g, '').slice(0, 160),
      collapsed: !!g.collapsed})),
    tabs: tabs.map(t => {
      let origin = '', kind = 'browser_internal';
      try {
        const url = new URL(t.url || 'about:blank');
        if (['http:', 'https:'].includes(url.protocol)) { origin = url.origin; kind = 'web_page'; }
        else { origin = url.protocol; }
      } catch {}
      if (String(t.url || '').split(/[?#]/)[0] === statusUrl) kind = 'automation_control';
      return {id: t.id, group_id: t.groupId, window_id: t.windowId,
        active: !!t.active, discarded: !!t.discarded, origin, kind};
    }), connections
  };
}

async function captureNativeBrowserInventory(bootstrap, input = {}) {
  const status = await bootstrap.context().newPage();
  try {
    await status.goto('chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/status.html',
      {waitUntil: 'domcontentloaded', timeout: 15000});
    return await status.evaluate(nativeBrowserInventory, input);
  } finally { await status.close(); }
}
