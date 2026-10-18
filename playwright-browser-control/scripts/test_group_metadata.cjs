'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'group_metadata.js'), 'utf8');

function fixture({extraTab=false, connections=1, existing=false, duplicate=false}={}) {
  let title='Playwright · playwright-cli', closed=false, updates=0;
  const chrome={
    tabs:{getCurrent:async()=>({id:11,groupId:7}),
      query:async()=>[{id:11},{id:12},...(extraTab?[{id:99}]:[])]},
    runtime:{sendMessage:async()=>({connections:[
      ...Array.from({length:connections},(_,i)=>({id:i+1,clientName:'playwright-cli',connectedTabIds:[11,12]})),
      {id:77,clientName:'playwright-cli',connectedTabIds:[71,72]}
    ]})},
    tabGroups:{get:async id=>({id,title,windowId:3}),update:async(id,value)=>{
      assert.equal(id,7);title=value.title;updates++;
    }}
  };
  const sandbox={chrome};vm.createContext(sandbox);vm.runInContext(source+'\nthis.groupMetadata=groupMetadata;',sandbox);
  const status={url:()=> 'chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html?token=fixture',
    on:()=>{},goto:async()=>{assert.equal(existing,false);},evaluate:async(fn,value)=>fn(value),close:async()=>{closed=true;}};
  const context={pages:()=>existing?(duplicate?[status,status]:[status]):[],newPage:async()=>{assert.equal(existing,false);return status;}};
  const bootstrap={context:()=>context};
  const identity={schema:'BrowserGroupIdentityV1',title:'Playwright · TEST · Windows · project · task'};
  return {call:readOnly=>sandbox.groupMetadata(bootstrap,{identity,readOnly}),
    get title(){return title;},get closed(){return closed;},get updates(){return updates;}};
}

test('rename uses native membership, not shared generic client name',async()=>{
  const f=fixture(),r=await f.call(false);
  assert.equal(r.group_id,7);assert.equal(r.connection_id,1);assert.equal(r.naming_verified,true);
  assert.equal(r.actual_title,f.title);assert.equal(r.saved_group_status,'not_checked');
  assert.equal(f.updates,1);assert.equal(f.closed,true);
});
test('existing control page is included and never created, navigated or closed',async()=>{
  const f=fixture({existing:true}),r=await f.call(true);
  assert.deepEqual(Array.from(r.native_tab_ids),[11,12]);assert.equal(f.closed,false);assert.equal(f.updates,0);
});
test('ambiguous existing control page is refused',async()=>{
  const f=fixture({existing:true,duplicate:true});await assert.rejects(f.call(true),/not_unique/);
  assert.equal(f.closed,false);
});
test('read-only metadata leaves the old title and closes its auxiliary page',async()=>{
  const f=fixture(),r=await f.call(true);
  assert.equal(r.naming_verified,false);assert.equal(f.updates,0);assert.equal(f.closed,true);
});
test('foreign tabs or ambiguous connection never rename a group',async()=>{
  for(const options of [{extraTab:true},{connections:2}]) {
    const f=fixture(options);await assert.rejects(f.call(false),/unowned_tabs|not_unique/);
    assert.equal(f.updates,0);assert.equal(f.closed,true);
  }
});
