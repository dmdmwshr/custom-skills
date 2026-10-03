// Single-action adapter for an existing official Playwright CLI extension session.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
// PACKAGE_SELECTION_READER_START
function readPackageSelection(r) {
  if(document.visibilityState==='hidden')return {ready:false,reason:'SOURCE_DOCUMENT_HIDDEN'};
  const [route,query]=location.hash.split('?');
  if(route!=='#/xfjd/projectDetail'||new URLSearchParams(query??'').get('RWID')!==r.rwid)
    return {ready:false,reason:'IDENTITY_MISMATCH'};
  const tables=[...document.querySelectorAll('.el-table')].filter(t=>t.offsetWidth&&t.offsetHeight&&
    t.querySelector('.el-table__header-wrapper')?.innerText.includes('审批日期'));
  const roots=tables.length===1?tables[0].__vue__?.store?.states?.data:null;
  if(!Array.isArray(roots))return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE'};
  const directory=new Map(),visited=new Set();
  let invalid=false;
  const walk=(n,depth)=>{
    if(!n||typeof n!=='object'||visited.has(n)||depth>20){invalid=true;return;}
    visited.add(n);
    if(n.children!==undefined&&!Array.isArray(n.children)){invalid=true;return;}
    if((n.children??[]).length){for(const child of n.children)walk(child,depth+1);return;}
    if(!n.ID)return;
    const id=String(n.ID),rwid=String(n.RWID??'');
    if(!/^\d+$/.test(id)||!/^\d+$/.test(rwid)||directory.has(id)||directory.size>=500) {
      invalid=true;return;
    }
    directory.set(id,{id,rwid,title:String(n.TITLE??'')});
  };
  roots.forEach(n=>walk(n,0));
  const leaves=[...directory.values()].filter(n=>n.rwid===r.rwid);
  const ids=leaves.map(n=>n.id);
  if(invalid||!leaves.length)return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE'};
  if(r.expectedLeafIds&&(ids.length!==r.expectedLeafIds.length||
    !ids.every(id=>r.expectedLeafIds.includes(id))))return {ready:false,reason:'SELECTION_MISMATCH'};
  // Every downloadable leaf, including collapsed related cases, must have an
  // observable checkbox. Never infer an unrendered leaf's unchecked state.
  const boxes=new Map();
  let unknownSelected=0;
  for(const e of document.querySelectorAll('input[type="checkbox"]')) {
    if(!/^\d+$/.test(e.value))continue;
    if(!directory.has(e.value)){if(e.checked)unknownSelected++;continue;}
    if(!boxes.has(e.value))boxes.set(e.value,[]);
    boxes.get(e.value).push(e);
  }
  const missing=[...directory.keys()].filter(id=>!boxes.has(id));
  const disagreement=[...boxes.values()].some(es=>es.some(e=>e.checked!==es[0].checked));
  if(missing.length||disagreement)return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE',
    documentCount:directory.size,missingCheckboxCount:missing.length};
  const selectedLeafIds=[...boxes].filter(([,es])=>es[0].checked).map(([id])=>id);
  const foreignSelectedCount=selectedLeafIds.filter(id=>directory.get(id).rwid!==r.rwid).length;
  if(foreignSelectedCount||unknownSelected)return {ready:false,reason:'SELECTION_MISMATCH',
    documentCount:directory.size,foreignSelectedCount:foreignSelectedCount+unknownSelected};
  if(ids.some(id=>boxes.get(id).some(e=>e.disabled)))
    return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE',documentCount:directory.size};
  return {ready:true,leaves,documentCount:directory.size,selectedLeafIds,
    allCurrentSelected:ids.every(id=>selectedLeafIds.includes(id)),foreignSelectedCount:0};
}
// PACKAGE_SELECTION_READER_END
function buildDownloadCode(r) {
  if (!['edge','chrome'].includes(r.browser) || !/^[a-z0-9][a-z0-9-]{0,47}$/.test(r.session || '')) throw new Error('Invalid session');
  if (!/^\d+$/.test(r.rwid || '') || !/^\d{8}[A-Z]\d{9}$/.test(r.projectNo || '') || !r.unitName) throw new Error('Missing case identity');
  if (!Number.isInteger(r.expectedLeafCount) || r.expectedLeafCount < 1) throw new Error('Missing verified leaf count');
  if (!Array.isArray(r.expectedLeafIds) || r.expectedLeafIds.length !== r.expectedLeafCount ||
      r.expectedLeafIds.some(id => typeof id !== 'string' || !/^\d+$/.test(id)) ||
      new Set(r.expectedLeafIds).size !== r.expectedLeafCount) throw new Error('Missing verified document leaf IDs');
  const origin = new URL(r.origin);
  if (!['http:','https:'].includes(origin.protocol) || origin.origin !== r.origin) throw new Error('Invalid origin');
  return `async (page) => {
    const r=${JSON.stringify({origin:r.origin,rwid:r.rwid,projectNo:r.projectNo,unitName:r.unitName,expectedLeafCount:r.expectedLeafCount,expectedLeafIds:r.expectedLeafIds})};
    if(await page.evaluate(()=>document.visibilityState)==='hidden') return {state:'SOURCE_DOCUMENT_HIDDEN',submitted:false};
    if(!page.url().startsWith(r.origin+'/#/xfjd/projectDetail?') || page.url().match(/[?&]RWID=([^&]+)/)?.[1]!==r.rwid) return {state:'IDENTITY_MISMATCH',submitted:false};
    const text=(await page.locator('.avue-view:visible').innerText()).replace(/\\s+/g,'');
    if(!text.includes('项目编号：'+r.projectNo) || !text.includes('单位名称'+r.unitName.replace(/\\s+/g,''))) return {state:'IDENTITY_MISMATCH',submitted:false};
    const leaves=await page.evaluate(${readPackageSelection.toString()},r);
    if(!leaves.ready)return {state:leaves.reason,submitted:false};
    if(!leaves.allCurrentSelected||leaves.selectedLeafIds.length!==r.expectedLeafCount||
       !leaves.selectedLeafIds.every(id=>r.expectedLeafIds.includes(id)))
      return {state:'SELECTION_MISMATCH',submitted:false};
    if(await page.evaluate(()=>document.visibilityState)==='hidden') return {state:'SOURCE_DOCUMENT_HIDDEN',submitted:false};
    const hint=page.waitForEvent('download',{timeout:3000}).then(()=>true).catch(()=>false);
    let state='NATIVE_FILE_CHECK_REQUIRED';
    try { await page.locator('button:visible').filter({hasText:/^开始打包$/}).press('Enter'); }
    catch { state='CLICK_OUTCOME_UNKNOWN'; }
    return {state,submitted:true,downloadEvent:await hint,selectedLeaves:leaves.selectedLeafIds.length};
  }`;
}
function recordActionIntent(r, checkpoint) {
  let resumeIndex=0, resumePath;
  const intent={state:'ACTION_INTENT_RECORDED',submitted:null,batchId:r.batchId,
    rwid:r.rwid,projectNo:r.projectNo,at:new Date().toISOString()};
  if(fs.existsSync(checkpoint)) {
    const prior=JSON.parse(fs.readFileSync(checkpoint,'utf8'));
    if(prior.rwid!==r.rwid||prior.projectNo!==r.projectNo||
       (prior.batchId&&prior.batchId!==r.batchId))throw new Error('Action checkpoint binding mismatch');
    if(prior.submitted!==false||prior.state!=='SOURCE_DOCUMENT_HIDDEN')
      throw new Error('Original action may have been submitted; do not repeat');
    resumeIndex=(prior.resumeIndex??0)+1;
    if(!Number.isInteger(resumeIndex)||resumeIndex<1)throw new Error('Invalid resume intent');
    resumePath=checkpoint+'.resume-'+resumeIndex+'.json';
    // Preserve the definite non-submission. An exclusive intent serializes this
    // same request's resumption before invoking the CLI; no new baseline/attempt.
    fs.writeFileSync(resumePath,JSON.stringify({...intent,resumeIndex,priorRefusal:prior}),
      {encoding:'utf8',flag:'wx'});
    fs.writeFileSync(checkpoint,JSON.stringify({...intent,resumeIndex}),'utf8');
  } else {
    fs.writeFileSync(checkpoint,JSON.stringify(intent),{encoding:'utf8',flag:'wx'});
  }
  return {resumeIndex,resumePath};
}
function main() {
  const r=JSON.parse(fs.readFileSync(process.argv[2],'utf8').replace(/^\uFEFF/,''));
  const code=buildDownloadCode(r);
  const checkpoint=path.resolve(r.checkpointPath);
  const baseline=JSON.parse(fs.readFileSync(r.baselinePath,'utf8').replace(/^\uFEFF/,''));
  // The baseline remains validated/consumed by source await-download; require its exact binding here too.
  const binding=baseline.binding || baseline;
  if (binding.rwid!==r.rwid || binding.projectNo!==r.projectNo || binding.batchId!==r.batchId) throw new Error('Download baseline binding mismatch');
  if(baseline.consumedAt || fs.existsSync(r.baselinePath+'.consumed.json')) throw new Error('Download baseline already consumed; do not repeat');
  const runtime=JSON.parse(fs.readFileSync(path.join(process.env.LOCALAPPDATA,'CodexBrowser/playwright/runtime.json'),'utf8'));
  if(!path.isAbsolute(runtime.node_executable)||
     path.resolve(runtime.node_executable).toLowerCase()!==path.resolve(process.execPath).toLowerCase())
    throw new Error('Configured Node runtime mismatch');
  const version=JSON.parse(fs.readFileSync(path.join(path.dirname(runtime.cli_entry),'package.json'),'utf8')).version;
  if(version!=='0.1.21'||version!==runtime.expected_cli_version)throw new Error('CLI version drift');
  // Exclusive checkpoint makes rerunning this request fail before any browser action.
  const intent=recordActionIntent(r,checkpoint);
  let output='';
  const write=process.stdout.write.bind(process.stdout);
  const capture=(chunk,enc,cb)=>{output+=String(chunk);if(typeof enc==='function')enc();if(cb)cb();return true;};
  process.stdout.write=capture;
  process.stderr.write=capture;
  process.on('exit',()=>{
    let result={state:'RESULT_UNKNOWN',submitted:null};
    const match=output.match(/### Result\s*\r?\n(\{[^\r\n]+\})/);
    if(match&&!output.includes('### Error'))try{
      const parsed=JSON.parse(match[1]);
      if(['SOURCE_DOCUMENT_HIDDEN','COMPLETE_SELECTION_STATE_UNAVAILABLE','IDENTITY_MISMATCH',
          'SELECTION_MISMATCH','NATIVE_FILE_CHECK_REQUIRED','CLICK_OUTCOME_UNKNOWN'].includes(parsed.state))
        result={state:parsed.state,submitted:parsed.submitted,downloadEvent:parsed.downloadEvent,selectedLeaves:parsed.selectedLeaves};
    }catch{}
    const saved={...result,resumeIndex:intent.resumeIndex,batchId:r.batchId,
      rwid:r.rwid,projectNo:r.projectNo,at:new Date().toISOString()};
    fs.writeFileSync(checkpoint,JSON.stringify(saved),'utf8');
    if(intent.resumePath) {
      const resume=JSON.parse(fs.readFileSync(intent.resumePath,'utf8'));
      fs.writeFileSync(intent.resumePath,JSON.stringify({...resume,result:saved}),'utf8');
    }
    write(JSON.stringify(result)+'\n');
    if(result.state!=='NATIVE_FILE_CHECK_REQUIRED')process.exitCode=1;
    output='';
  });
  process.env.NO_UPDATE_NOTIFIER='1';
  for(const key of Object.keys(process.env))if(/^(DEBUG|PWTEST_)/.test(key))delete process.env[key];
  process.argv=[process.execPath,runtime.cli_entry,'-s='+r.browser+'-'+r.session,'run-code',code];
  require(runtime.cli_entry);
}
module.exports={buildDownloadCode,recordActionIntent,readPackageSelection};
if(require.main===module)try{main();}catch(e){console.error('Download not started: '+e.message);process.exitCode=1;}
