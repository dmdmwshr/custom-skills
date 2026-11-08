'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {buildDownloadCode,recordActionIntent}=require('./playwright_cli_download.cjs');
const PN='99999999T209900001';
const request={browser:'edge',session:'download-fixture',origin:'https://example.test',
  batchId:'synthetic-fixture',rwid:'32100934444',projectNo:PN,unitName:'Fixture',org:'Brigade A',
  expectedLeafCount:2,expectedLeafIds:['101','102']};
assert.doesNotMatch(fs.readFileSync(path.join(__dirname,'playwright_cli_download.cjs'),'utf8'),/__vue__|tableFullData|store\??\.states/);
const shown={offsetWidth:500,offsetHeight:200};
const leafRow=(id,checked=true,extra={})=>({id,title:'Doc '+id,level:2,checked,placeholder:true,icon:false,...extra});
const ownRoot={title:'[产]日常监督-'+PN+'-Fixture',level:1,expanded:true,box:false};
function fixture({wrongUrl=false,rows,org='Brigade A',unit='Fixture',pn=PN,mask=false,hiddenAt=Infinity,clickFail=false,overflow=false,noWrap=false}={}) {
  let clicks=0,events=0,evaluations=0;
  const body=rows||[ownRoot,leafRow('101'),leafRow('102')];
  const trs=body.map(r=>({...shown,className:'el-table__row el-table__row--level-'+(r.level??0),cells:[{innerText:r.title}],
    querySelector:sel=>sel==='input[type="checkbox"]'?(r.box===false?null:{value:r.id??'x',checked:!!r.checked,disabled:!!r.disabled}):
      sel==='.el-table__expand-icon'?(r.icon===false?null:{classList:{contains:c=>!!r.expanded&&c==='el-table__expand-icon--expanded'}}):
      sel==='.el-table__placeholder'?(r.placeholder?{}:null):null}));
  const wrap=noWrap?null:{scrollHeight:overflow?900:100,clientHeight:100};
  const table={...shown,querySelector:sel=>sel==='.el-table__header-wrapper'?{innerText:'Name 审批日期'}:sel==='.el-table__body-wrapper'?wrap:null,
    querySelectorAll:sel=>sel==='.el-table__body-wrapper tr.el-table__row'?trs:[]};
  Object.defineProperty(table,'__vue__',{get(){throw new Error('INTERNAL_STATE_READ');},enumerable:false});
  const span=(l,v)=>({textContent:l,parentElement:{nextElementSibling:{innerText:v}}});
  const location={hash:'#/xfjd/projectDetail?RWID='+(wrongUrl?'32100000456':'32100934444')};
  const maskEl={...shown,style:{opacity:'1',visibility:'visible'}};
  const document={visibilityState:'visible',body:{innerText:'项目编号：'+pn},querySelectorAll:sel=>({
    '.el-table':[table],'main.el-main span':[span('单位名称',unit),span('消防管辖',org)],
    '[class*=loading],#nprogress,.el-loading-mask':mask?[maskEl]:[]}[sel]||[])};
  return {get clicks(){return clicks;},get events(){return events;},page:{
    url:()=>request.origin+'/'+location.hash,
    evaluate:async(fn,arg)=>{evaluations++;document.visibilityState=evaluations>=hiddenAt?'hidden':'visible';
      return vm.runInNewContext('('+fn.toString()+')',{document,location,URLSearchParams,getComputedStyle:e=>e.style||{visibility:'visible',opacity:'0'}})(arg);},
    waitForEvent:async()=>{events++;throw new Error('Native event unavailable');},
    locator:()=>({innerText:async()=>'项目编号：'+pn+' 单位名称'+unit,
      filter:()=>({press:async key=>{assert.equal(key,'Enter');clicks++;if(clickFail)throw new Error('timeout');}})})
  }};
}
async function run(f,req=request){return vm.runInNewContext('('+buildDownloadCode(req)+')')(f.page);}
const noClick=async(args,state,req)=>{const f=fixture(args);const r=await run(f,req);assert.equal(r.state,state,JSON.stringify(args).slice(0,80));assert.equal(f.clicks,0);assert.equal(r.submitted,false);};
test('wrong identity never clicks',async()=>{await noClick({wrongUrl:true},'IDENTITY_MISMATCH');});
test('exactly the proven-complete own leaf set is required before the single click',async()=>{
  const f=fixture(),r=await run(f);assert.equal(r.state,'NATIVE_FILE_CHECK_REQUIRED');assert.equal(r.selectedLeaves,2);assert.equal(f.clicks,1);
  await noClick({rows:[ownRoot,leafRow('101',false),leafRow('102',false)]},'SELECTION_MISMATCH');
  await noClick({rows:[ownRoot,leafRow('101'),leafRow('102',false)]},'SELECTION_MISMATCH');
});
test('missing or incomplete leaf evidence blocks: fewer leaves, collapsed, zero icons, scroll overflow, no scroll container',async()=>{
  await noClick({rows:[ownRoot,leafRow('101')]},'SELECTION_MISMATCH'); // 面板证明完整但与请求的叶集合不一致
  await noClick({rows:[{...ownRoot,expanded:false},leafRow('101'),leafRow('102')]},'COMPLETE_SELECTION_STATE_UNAVAILABLE');
  await noClick({rows:[ownRoot,leafRow('101',true,{placeholder:false}),leafRow('102',true,{placeholder:false})]},'COMPLETE_SELECTION_STATE_UNAVAILABLE');
  await noClick({overflow:true},'COMPLETE_SELECTION_STATE_UNAVAILABLE');
  await noClick({noWrap:true},'COMPLETE_SELECTION_STATE_UNAVAILABLE');
});
test('a foreign penalty or related root (even with a numeric checkbox value) never lends leaves to this case',async()=>{
  const rows=[ownRoot,leafRow('101'),leafRow('102'),{id:'99',title:'[罚]一般-32002201X202600075-Fixture',level:1,expanded:true,checked:true},leafRow('22')];
  await noClick({rows},'SELECTION_MISMATCH');
  const related=[ownRoot,leafRow('101'),leafRow('102'),{title:'[监]日常监督抽查-32002201J202600052-Fixture',level:1,expanded:true,box:false},leafRow('201',true)];
  await noClick({rows:related},'SELECTION_MISMATCH');
  // unselected foreign leaves present: still exact for the own set, but select-all would be unsafe
  const f=fixture({rows:[ownRoot,leafRow('101'),leafRow('102'),{title:'[监]日常监督抽查-32002201J202600052-Fixture',level:1,expanded:true,box:false},leafRow('201',false)]});
  assert.equal((await run(f)).state,'NATIVE_FILE_CHECK_REQUIRED');
});
test('unknown or ambiguous roots make ownership UNKNOWN',async()=>{
  await noClick({rows:[ownRoot,leafRow('101'),{id:'99',title:'一般-32002201X202600075-Fixture',level:1,expanded:true,checked:true},leafRow('102')]},'COMPLETE_SELECTION_STATE_UNAVAILABLE');
  await noClick({rows:[{title:'[罚]一般-'+PN+'-Fixture',level:1,expanded:true,box:false},leafRow('101'),leafRow('102')]},'COMPLETE_SELECTION_STATE_UNAVAILABLE');
});
test('expected organization is mandatory and must match the live page; busy pages block',async()=>{
  assert.throws(()=>buildDownloadCode({...request,org:undefined}),/Missing expected organization/);
  await noClick({org:'Brigade B'},'IDENTITY_BLOCKED');
  await noClick({unit:'Other'},'IDENTITY_MISMATCH');
  await noClick({mask:true},'SOURCE_LOADING');
});
test('a cross-check leaf count that disagrees blocks',async()=>{
  await noClick({},'COMPLETE_SELECTION_STATE_UNAVAILABLE',{...request,crossCheckLeafCount:3});
});
test('missing download event still verifies native files after one click',async()=>{
  const f=fixture(),r=await run(f);assert.equal(r.state,'NATIVE_FILE_CHECK_REQUIRED');
  assert.equal(r.downloadEvent,false);assert.equal(f.clicks,1);
});
test('an uncertain click is never classified as not submitted',async()=>{
  const f=fixture({clickFail:true}),r=await run(f);assert.equal(r.state,'CLICK_OUTCOME_UNKNOWN');
  assert.equal(r.submitted,true);assert.equal(f.clicks,1);
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
