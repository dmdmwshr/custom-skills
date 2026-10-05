'use strict';
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'group_cleanup.js'),'utf8');
function fixture({foreign=false,moved=false,dirty=false,downloading=false}={}) {
 const events=[], jobs=[], tabs=new Map([[1,{id:1,groupId:7}],[2,{id:2,groupId:7}],...(foreign?[[99,{id:99,groupId:7}]]:[])]);
 const chrome={tabs:{getCurrent:async()=>tabs.get(2),query:async()=>[...tabs.values()].filter(t=>t.groupId===7),get:async id=>{if(!tabs.has(id))throw Error('missing');return tabs.get(id);},
   ungroup:async ids=>{events.push('ungroup');for(const id of ids)tabs.get(id).groupId=-1;},remove:async ids=>{events.push('remove');for(const id of ids)tabs.delete(id);}},
   tabGroups:{get:async()=>({id:moved?8:7,title:'own'})},runtime:{sendMessage:async()=>({connections:[{id:3,connectedTabIds:[1,2]}]})}};
 const sandbox={chrome,setTimeout:fn=>jobs.push(fn),Set,Error}; vm.createContext(sandbox);vm.runInContext(source+';this.cleanup=executionCleanup;this.verify=verifyNativeCleanup;',sandbox);
 const page={url:()=>dirty?'https://test/':'about:blank',evaluate:async()=>dirty};
 const status={goto:async()=>{},evaluate:async(fn,value)=>fn(value),close:async()=>events.push('helper-close')};
 const bootstrap={context:()=>({pages:()=>[page],newPage:async()=>status,
   __codexExecutionDownloads:{pending:new Set(downloading?['active-download']:[])}})};
 return {call:()=>sandbox.cleanup(bootstrap,{group_id:7,connection_id:3,actual_title:'own',owned_tab_ids:[1]}),
   verify:ids=>sandbox.verify(bootstrap,ids),events,jobs,tabs};
}
test('cleanup acknowledges intent and deletes the group before native tabs',async()=>{
 const f=fixture();const r=await f.call();assert.equal(r.scheduled,true);assert.equal(f.events.length,0);
 await f.jobs[0]();assert.deepEqual(f.events,['ungroup','remove']);assert.equal(f.tabs.size,0);
 const readback=await f.verify(r.tab_ids);assert.equal(readback.verified,true);
});
test('foreign native tabs or changed group cannot be deleted',async()=>{
 for(const opts of [{foreign:true},{moved:true}]){const f=fixture(opts);await assert.rejects(f.call(),/membership_changed|identity_changed/);assert.equal(f.jobs.length,0);assert.equal(f.tabs.has(1),true);}
});
test('unsaved input keeps execution unfinished and schedules no close',async()=>{
 const f=fixture({dirty:true});const r=await f.call();assert.equal(r.scheduled,false);assert.equal(f.jobs.length,0);assert.equal(r.protected[0],'unsaved_input');
});
test('native readback distinguishes detached tabs from removed tabs',async()=>{
 const f=fixture();f.tabs.get(1).groupId=-1;const r=await f.verify([1]);assert.equal(r.verified,false);assert.deepEqual(Array.from(r.remaining_tab_ids),[1]);
});
test('unfinished download schedules no removal',async()=>{
 const f=fixture({downloading:true});const r=await f.call();assert.equal(r.scheduled,false);assert.equal(r.protected[0],'download_in_progress');assert.equal(f.jobs.length,0);
});
test('a foreign tab added after acknowledgement cancels the scheduled cleanup',async()=>{
 const f=fixture();await f.call();f.tabs.set(99,{id:99,groupId:7});await f.jobs[0]();assert.equal(f.events.length,0);assert.equal(f.tabs.has(1),true);
});
