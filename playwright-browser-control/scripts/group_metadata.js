// Executed by the pinned official CLI in this connection's existing context.
async function groupMetadata(bootstrap, input) {
  const context = bootstrap.context();
  const status = await context.newPage();
  try {
    await status.goto('chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/status.html',
      {waitUntil: 'domcontentloaded', timeout: 15000});
    return await status.evaluate(async ({identity, readOnly}) => {
      const own = await chrome.tabs.getCurrent();
      if (!own || own.groupId < 0) throw Error('native_group_missing');
      const response = await chrome.runtime.sendMessage({type: 'getConnectionStatus'});
      const connections = (response.connections || []).filter(c => c.connectedTabIds?.includes(own.id));
      if (connections.length !== 1) throw Error('own_connection_not_unique');
      const group = await chrome.tabGroups.get(own.groupId);
      const tabs = await chrome.tabs.query({groupId: group.id});
      if (tabs.some(t => !connections[0].connectedTabIds.includes(t.id)))
        throw Error('group_contains_unowned_tabs');
      if (!readOnly && group.title !== identity.title)
        await chrome.tabGroups.update(group.id, {title: identity.title});
      const verified = await chrome.tabGroups.get(group.id);
      return {...identity, group_id: verified.id, window_id: verified.windowId,
        connection_id: connections[0].id, actual_title: verified.title,
        naming_verified: verified.title === identity.title,
        saved_group_status: 'not_checked'};
    }, input);
  } finally {
    await status.close();
  }
}
