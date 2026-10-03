'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {buildDownloadCode,recordActionIntent}=require('./playwright_cli_download.cjs');
const request={browser:'edge',session:'download-fixture',origin:'https://example.test',
  batchId:'synthetic-fixture',rwid:'123',projectNo:'99999999T209900001',unitName:'Fixture',
  expectedLeafCount:2,expectedLeafIds:['101','102']};
function element(value,{checked=true,visible=true,disabled=false}={}) {
  return {value,checked,disabled,closest:()=>({getClientRects:()=>visible?[{}]:[]})};
}
function fixture({wrong=false,leaves=2,all=true,clickFail=false,elements,hiddenAt=Infinity,directory}={}) {
  let clicks=0,events=0,evaluations=0;
  const boxes=elements||[...request.expectedLeafIds.slice(0,leaves).map(id=>element(id,{checked:all})),
    element('201',{checked:false,visible:false})];
  const roots=directory||[{children:request.expectedLeafIds.map(id=>({ID:id,RWID:'123',TITLE:'Fixture'}))},
    {children:[{ID:'201',RWID:'456',TITLE:'Related fixture'}]}];
  const table={offsetWidth:500,offsetHeight:200,__vue__:{store:{states:{data:roots}}},
    querySelector:()=>({innerText:'审批日期'})};
  const location={hash:'#/xfjd/projectDetail?RWID='+(wrong?'456':'123')};
  const document={visibilityState:'visible',querySelectorAll:selector=>
    selector==='.el-table'?[table]:selector==='input[type="checkbox"]'?boxes:[]};
  return {get clicks(){return clicks;},get events(){return events;},page:{
    url:()=>request.origin+'/'+location.hash,
    evaluate:async(fn,arg)=>{evaluations++;document.visibilityState=evaluations>=hiddenAt?'hidden':'visible';
      return vm.runInNewContext('('+fn.toString()+')',{document,location,URLSearchParams})(arg);},
    waitForEvent:async()=>{events++;throw new Error('Native event unavailable');},
    locator:()=>({innerText:async()=>'项目编号：'+request.projectNo+' 单位名称Fixture',
      filter:()=>({press:async key=>{assert.equal(key,'Enter');clicks++;if(clickFail)throw new Error('timeout');}})})
  }};
}
async function run(f){return vm.runInNewContext('('+buildDownloadCode(request)+')')(f.page);}
test('wrong identity never clicks',async()=>{
  const f=fixture({wrong:true});assert.equal((await run(f)).state,'IDENTITY_MISMATCH');assert.equal(f.clicks,0);
});
test('unchecked current leaves never click',async()=>{
  const f=fixture({all:false});assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);
});
test('missing current or collapsed related leaves cannot prove complete state',async()=>{
  for(const args of [{leaves:1},{elements:[element('101'),element('102')]}]) {
    const f=fixture(args);assert.equal((await run(f)).state,'COMPLETE_SELECTION_STATE_UNAVAILABLE');assert.equal(f.clicks,0);
  }
});
test('missing download event still verifies native files after one click',async()=>{
  const f=fixture(),r=await run(f);assert.equal(r.state,'NATIVE_FILE_CHECK_REQUIRED');
  assert.equal(r.downloadEvent,false);assert.equal(r.selectedLeaves,2);assert.equal(f.clicks,1);
});
test('an uncertain click is never classified as not submitted',async()=>{
  const f=fixture({clickFail:true}),r=await run(f);assert.equal(r.state,'CLICK_OUTCOME_UNKNOWN');
  assert.equal(r.submitted,true);assert.equal(f.clicks,1);
});
test('a checked hidden related-case leaf blocks packaging',async()=>{
  const f=fixture({elements:[element('101'),element('102'),element('201',{visible:false})]});
  assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);
});
test('fixed-column duplicates must agree even when hidden',async()=>{
  const f=fixture({elements:[element('101'),element('102'),element('101',{visible:false}),
    element('102',{visible:false}),element('201',{checked:false,visible:false}),element('附件')]});
  assert.equal((await run(f)).state,'NATIVE_FILE_CHECK_REQUIRED');assert.equal(f.clicks,1);
  const other=fixture({elements:[element('101'),element('102'),element('102',{checked:false,visible:false}),
    element('201',{checked:false})]});
  assert.equal((await run(other)).state,'COMPLETE_SELECTION_STATE_UNAVAILABLE');assert.equal(other.clicks,0);
});
test('unknown selected numeric IDs block rather than guessing their type',async()=>{
  const f=fixture({elements:[element('101'),element('102'),element('201',{checked:false}),element('900')]});
  assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);
});
test('directory ownership must match the current RWID and expected IDs',async()=>{
  const f=fixture({directory:[{children:[{ID:'101',RWID:'123'},{ID:'102',RWID:'456'},{ID:'201',RWID:'456'}]}]});
  assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);
});
test('hidden entry or final check never registers a download action',async()=>{
  for(const hiddenAt of [1,3]) {const f=fixture({hiddenAt}),r=await run(f);
    assert.equal(r.state,'SOURCE_DOCUMENT_HIDDEN');assert.equal(r.submitted,false);
    assert.equal(f.clicks,0);assert.equal(f.events,0);}
});
test('invalid expected document IDs are rejected before browser use',()=>{
  for(const expectedLeafIds of [undefined,['101','101'],['101'],['101',102]])
    assert.throws(()=>buildDownloadCode({...request,expectedLeafIds}));
});
function checkpoint(t) {
  const folder=fs.mkdtempSync(path.join(os.tmpdir(),'registry-download-fixture-'));
  t.after(()=>fs.rmSync(folder,{recursive:true,force:true}));return path.join(folder,'action.json');
}
test('the first exclusive intent prevents replay of unknown results',t=>{
  const target=checkpoint(t);recordActionIntent(request,target);const before=fs.readFileSync(target);
  assert.throws(()=>recordActionIntent(request,target),/do not repeat/);assert.deepEqual(fs.readFileSync(target),before);
});
test('definite hidden refusal resumes one request with preserved refusal evidence',t=>{
  const target=checkpoint(t),prior={state:'SOURCE_DOCUMENT_HIDDEN',submitted:false,
    batchId:request.batchId,rwid:request.rwid,projectNo:request.projectNo,resumeIndex:0};
  fs.writeFileSync(target,JSON.stringify(prior));const intent=recordActionIntent(request,target);
  assert.equal(intent.resumeIndex,1);assert.deepEqual(JSON.parse(fs.readFileSync(intent.resumePath)).priorRefusal,prior);
  const current=JSON.parse(fs.readFileSync(target));assert.equal(current.state,'ACTION_INTENT_RECORDED');
  assert.equal(current.submitted,null);assert.equal(current.rwid,request.rwid);
  assert.throws(()=>recordActionIntent(request,target),/do not repeat/);
});
test('other refusals and identity changes cannot resume automatically',t=>{
  const target=checkpoint(t);
  for(const patch of [{state:'SELECTION_MISMATCH'},{rwid:'456'}]) {
    fs.writeFileSync(target,JSON.stringify({state:'SOURCE_DOCUMENT_HIDDEN',submitted:false,
      batchId:request.batchId,rwid:request.rwid,projectNo:request.projectNo,...patch}));
    const before=fs.readFileSync(target);assert.throws(()=>recordActionIntent(request,target));
    assert.deepEqual(fs.readFileSync(target),before);
  }
});
test('a competing exclusive resume intent prevents a second invocation',t=>{
  const target=checkpoint(t),prior={state:'SOURCE_DOCUMENT_HIDDEN',submitted:false,
    batchId:request.batchId,rwid:request.rwid,projectNo:request.projectNo,resumeIndex:0};
  fs.writeFileSync(target,JSON.stringify(prior));fs.writeFileSync(target+'.resume-1.json','{}');
  assert.throws(()=>recordActionIntent(request,target),/EEXIST/);assert.deepEqual(JSON.parse(fs.readFileSync(target)),prior);
});
