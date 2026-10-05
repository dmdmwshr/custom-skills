// Official extension APIs; the timer survives debugger detachment, not browser closure.
async function executionCleanup(bootstrap, input) {
  const context = bootstrap.context();
  const pages = context.pages();
  const protectedPages = [];
  if(context.__codexExecutionDownloads?.pending.size) protectedPages.push('download_in_progress');
  for (const p of pages) {
    if (!/^https?:/.test(p.url())) continue;
    try {
      const dirty = await p.evaluate(() => [...document.querySelectorAll('input,textarea,select,[contenteditable="true"]')].some(e =>
        e.isContentEditable ? !!e.textContent : e.tagName === 'SELECT' ? [...e.options].some(o => o.selected !== o.defaultSelected) :
          ['checkbox','radio'].includes(e.type) ? e.checked !== e.defaultChecked :
            !['hidden','submit','button'].includes(e.type) && e.value !== e.defaultValue));
      if (dirty) protectedPages.push('unsaved_input');
    } catch { protectedPages.push('page_state_unverified'); }
  }
  if (protectedPages.length) return {schema:'BrowserCleanupV1',scheduled:false,protected:protectedPages};
  const status = await context.newPage();
  let scheduled = false;
  try {
    await status.goto('chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/status.html', {waitUntil:'domcontentloaded',timeout:15000});
    const result = await status.evaluate(async expected => {
      const own = await chrome.tabs.getCurrent();
      const group = await chrome.tabGroups.get(own.groupId);
      const connections = (await chrome.runtime.sendMessage({type:'getConnectionStatus'})).connections || [];
      const current = connections.filter(c => c.connectedTabIds.includes(own.id));
      if (current.length !== 1 || group.id !== expected.group_id || current[0].id !== expected.connection_id || group.title !== expected.actual_title)
        throw Error('cleanup_identity_changed');
      const tabs = await chrome.tabs.query({groupId:group.id});
      const ids = tabs.map(t=>t.id);
      const owned = new Set([...expected.owned_tab_ids,own.id]);
      if (ids.some(id=>!owned.has(id) || !current[0].connectedTabIds.includes(id)) || ids.length !== owned.size)
        throw Error('cleanup_membership_changed');
      // Acknowledge intent before removing the page that carries the response.
      // The next fresh execution verifies these exact native IDs, without reopening them.
      setTimeout(async () => {
        const fresh = await chrome.tabGroups.get(group.id).catch(()=>null);
        const members = await chrome.tabs.query({groupId:group.id});
        const connections=(await chrome.runtime.sendMessage({type:'getConnectionStatus'})).connections || [];
        const owner=connections.find(c=>c.id===expected.connection_id);
        if (!fresh || fresh.title !== expected.actual_title || !owner || ids.some(id=>!owner.connectedTabIds.includes(id)) || members.length !== ids.length || members.some(t=>!owned.has(t.id))) return;
        await chrome.tabs.ungroup(ids);
        await chrome.tabs.remove(ids);
      }, 2500);
      return {schema:'BrowserCleanupV1',scheduled:true,group_id:group.id,tab_ids:ids,protected:[],
        native_pages_status:'remove_requested',saved_group_status:'pending_readback'};
    }, input);
    scheduled = result.scheduled;
    return result;
  } finally {
    if (!scheduled) await status.close();
  }
}

async function verifyNativeCleanup(bootstrap, input) {
  const status = await bootstrap.context().newPage();
  try {
    await status.goto('chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/status.html', {waitUntil:'domcontentloaded',timeout:15000});
    return await status.evaluate(async ids => {
      const remaining = [];
      for (const id of ids) if (await chrome.tabs.get(id).catch(()=>null)) remaining.push(id);
      return {schema:'BrowserCleanupReadbackV1',remaining_tab_ids:remaining,verified:remaining.length===0};
    }, input);
  } finally { await status.close(); }
}
