const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {buildDownloadCode}=require('./playwright_cli_download.cjs');
const request={browser:'edge',session:'download-fixture',origin:'https://example.test',rwid:'123',projectNo:'99999999T209900001',unitName:'Fixture',expectedLeafCount:2,expectedLeafIds:['101','102']};
function element(value,{checked=true,visible=true,disabled=false}={}){
  return {value,checked,disabled,closest:()=>({getClientRects:()=>visible?[{}]:[]})};
}
function fixture({wrong=false,leaves=2,all=true,clickFail=false,elements}={}){
  let clicks=0;
  return {get clicks(){return clicks;},page:{
    url:()=>`https://example.test/#/xfjd/projectDetail?RWID=${wrong?'456':'123'}`,
    waitForEvent:async()=>{throw new Error('Native event unavailable');},
    locator:(s)=>({innerText:async()=>`项目编号：${request.projectNo} 单位名称Fixture`,evaluateAll:async(fn)=>fn(elements||request.expectedLeafIds.slice(0,leaves).map(id=>element(id,{checked:all}))),filter:()=>({click:async()=>{clicks++;if(clickFail)throw new Error('timeout');}})})
  }};
}
async function run(f){return vm.runInNewContext('('+buildDownloadCode(request)+')')(f.page);}
test('wrong identity never clicks',async()=>{const f=fixture({wrong:true});assert.equal((await run(f)).state,'IDENTITY_MISMATCH');assert.equal(f.clicks,0);});
test('incomplete or changed selection never clicks',async()=>{for(const args of [{all:false},{leaves:1}]){const f=fixture(args);assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);}});
test('missing download event still calls native file verification after one click',async()=>{const f=fixture();const r=await run(f);assert.equal(r.state,'NATIVE_FILE_CHECK_REQUIRED');assert.equal(r.downloadEvent,false);assert.equal(f.clicks,1);});
test('uncertain click is never replayed',async()=>{const f=fixture({clickFail:true});assert.equal((await run(f)).state,'CLICK_OUTCOME_UNKNOWN');assert.equal(f.clicks,1);});
test('same count with a parent or attachment ID never clicks',async()=>{
  const f=fixture({elements:[element('101'),element('900')]});
  assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);
});
test('duplicate visible columns are deduplicated, hidden copies ignored',async()=>{
  const f=fixture({elements:[element('101'),element('102'),element('101'),element('102',{checked:false,visible:false}),element('attachment')]});
  assert.equal((await run(f)).state,'NATIVE_FILE_CHECK_REQUIRED');assert.equal(f.clicks,1);
});
test('an additional numeric checkbox blocks rather than guessing its type',async()=>{
  const f=fixture({elements:[element('101'),element('102'),element('900')]});
  assert.equal((await run(f)).state,'SELECTION_MISMATCH');assert.equal(f.clicks,0);
});
test('missing or repeated expected document IDs are rejected before browser use',()=>{
  for(const expectedLeafIds of [undefined,['101','101'],['101'],['101',102]])assert.throws(()=>buildDownloadCode({...request,expectedLeafIds}));
});
