'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'native_browser_audit.js'),'utf8');
function fixture({fail=false}={}) {
 let closed=false;const mutations=[];
 const chrome={tabs:{getCurrent:async()=>({id:99}),query:async()=>[
  {id:1,groupId:7,windowId:1,url:'https://user:pw@x.com/home?token=secret#fragment'},
  {id:2,groupId:-1,windowId:1,url:'chrome-extension://own/status.html'},
  {id:99,groupId:7,url:'chrome-extension://own/status.html'}],remove:async()=>mutations.push('remove')},
  tabGroups:{query:async()=>[{id:7,title:'own',windowId:1}],update:async()=>mutations.push('update')},
  runtime:{getURL:p=>'chrome-extension://own/'+p,sendMessage:async()=>{
   if(fail)throw Error('connection_status_failed');return {connections:[{id:3,connectedTabIds:[1,99,12345],token:'secret'}]};}}};
 const sandbox={chrome,URL,Set,Date};vm.createContext(sandbox);
 vm.runInContext(source+';this.capture=captureNativeBrowserInventory;this.inventory=nativeBrowserInventory;',sandbox);
 const status={goto:async()=>{},evaluate:async(fn,value)=>fn(value),close:async()=>{closed=true;}};
 return {call:()=>sandbox.capture({context:()=>({newPage:async()=>status})}),inventory:input=>sandbox.inventory(input),mutations,get closed(){return closed;}};
}
test('inventory excludes its helper and strips URL credentials and connection secrets',async()=>{
 const f=fixture(),r=await f.call();assert.equal(r.tabs.length,2);assert.equal(r.tabs[0].origin,'https://x.com');
 assert.equal(r.tabs[1].kind,'automation_control');assert.deepEqual(Array.from(r.connections[0].tab_ids),[1]);
 assert.equal(JSON.stringify(r).includes('secret'),false);assert.equal(f.closed,true);assert.equal(f.mutations.length,0);
});
test('failed native inspection closes its helper without touching other objects',async()=>{
 const f=fixture({fail:true});await assert.rejects(f.call(),/connection_status_failed/);
 assert.equal(f.closed,true);assert.equal(f.mutations.length,0);
});
test('an existing WSL execution anchor stays in the native inventory',async()=>{
 const f=fixture(),r=await f.inventory({include_self:true});
 assert.ok(r.tabs.some(t=>t.id===99));assert.deepEqual(Array.from(r.connections[0].tab_ids),[1,99]);
 assert.equal(f.closed,false);assert.equal(f.mutations.length,0);
});
