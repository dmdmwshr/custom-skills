// Single-action adapter for an existing official Playwright CLI extension session.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
// 点击前选择读取器：只读可见 DOM(Element UI 树表行、复选框 value/checked、展开图标)，不读取框架内部数据。
// 护栏函数(身份四项/根边界/完整叶证明/选择集合)与 browser_source_capture.mjs 的 VISIBLE_GUARDS 同源，逐字内联。
// SELECTION_BODY_START
const SELECTION_BODY = String.raw`
if(document.visibilityState==='hidden')return {ready:false,reason:'SOURCE_DOCUMENT_HIDDEN'};
const vis=e=>!!e&&!!e.offsetWidth&&!!e.offsetHeight;
const txt=e=>(e?.innerText||'').replace(/\s+/g,' ').trim();
const busy=[...document.querySelectorAll('[class*=loading],#nprogress,.el-loading-mask')].some(e=>vis(e)&&
  getComputedStyle(e).visibility!=='hidden'&&Number(getComputedStyle(e).opacity)>0);
if(busy)return {ready:false,reason:'SOURCE_LOADING'};
const [route,query]=location.hash.split('?');
const urlRwid=new URLSearchParams(query??'').get('RWID')||'';
const pn=/项目编号[：:]\s*(\d{8}[A-Z]\d{9})/.exec(document.body.innerText)?.[1]||'';
const field=l=>{const n=[...document.querySelectorAll('main.el-main span')].filter(e=>e.textContent.trim()===l);
  return n.length===1?txt(n[0].parentElement.nextElementSibling):'';};
const gate=identityGate({expectedProjectNo:r.projectNo,detailProjectNo:pn,expectedRwid:r.rwid,urlRwid,
  expectedUnit:r.unitName,detailUnit:field('单位名称'),expectedOrg:r.org,detailOrg:field('消防管辖')});
if(route!=='#/xfjd/projectDetail'||!gate.ok)return {ready:false,reason:'IDENTITY_BLOCKED',identityMiss:gate.miss};
const tables=[...document.querySelectorAll('.el-table')].filter(t=>vis(t)&&
  t.querySelector('.el-table__header-wrapper')?.innerText.includes('审批日期'));
if(tables.length!==1)return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE',blockReasons:['SELECTION_TABLE_NOT_UNIQUE']};
const trs=[...tables[0].querySelectorAll('.el-table__body-wrapper tr.el-table__row')].filter(vis);
let collapsed=0,expandUnknown=0;const unknownIdx=[];
const rows=trs.map((tr,ti)=>{
  const cb=tr.querySelector('input[type="checkbox"]');
  const lv=/el-table__row--level-(\d+)/.exec(tr.className);
  const icon=tr.querySelector('.el-table__expand-icon'),ph=tr.querySelector('.el-table__placeholder');
  if(!icon&&!ph)unknownIdx.push(ti);
  if(icon&&!ph&&!icon.classList.contains('el-table__expand-icon--expanded'))collapsed++;
  return {id:cb&&/^\d+$/.test(cb.value)?cb.value:null,hasCheckbox:!!cb,checked:cb?!!cb.checked:false,
    disabled:cb?!!cb.disabled:false,level:lv?Number(lv[1]):0,
    title:[...tr.cells].map(txt).find(t=>t)||''};
});
// 无展开图标、无占位且其后没有子层级行的非本案根行(如空的外案[监]根)不含本案叶，不算展开状态未知；本案根与有子层级的行仍按未知阻断。
const emptyForeignRoot=(i)=>{const x=rows[i];return x.level===0&&!x.hasCheckbox&&!(rows[i+1]&&rows[i+1].level>0)&&!(String(x.title).includes('[产]')&&String(x.title).includes(String(r.projectNo)));};
expandUnknown=unknownIdx.filter(i=>!emptyForeignRoot(i)).length;
const wrap=tables[0].querySelector('.el-table__body-wrapper');
const overflow=wrap&&Number.isFinite(wrap.scrollHeight)&&Number.isFinite(wrap.clientHeight)?wrap.scrollHeight-wrap.clientHeight>1:null;
const coverageProven=overflow===false&&expandUnknown===0&&collapsed===0&&rows.length>0;
const m=assignLeafMembership(rows,r.projectNo);
const declared=r.declaredSourceVisible===true&&Number.isInteger(r.declaredLeafCount)?r.declaredLeafCount:null;
const comp=leafCompleteness({ownCount:m.ownIds.length,declaredLeafCount:declared,collapsedCount:collapsed,
  unreadableCount:m.unreadableCount,expandStateKnown:rows.length>0&&expandUnknown===0,coverageProven});
const blocks=comp.why.slice();
if(m.unknownState)blocks.push('UNKNOWN_STATE');
if(Number.isInteger(r.crossCheckLeafCount)&&r.crossCheckLeafCount!==m.ownIds.length)blocks.push('CROSS_CHECK_MISMATCH');
if(blocks.length)return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE',blockReasons:blocks,roots:m.roots,documentCount:rows.length};
const foreignSelectedCount=m.foreignSelected.length+m.orphanSelected.length;
if(foreignSelectedCount)return {ready:false,reason:'SELECTION_MISMATCH',foreignSelectedCount,documentCount:rows.length};
if(r.expectedLeafIds&&(r.expectedLeafIds.length!==m.ownIds.length||
  !m.ownIds.every(id=>r.expectedLeafIds.includes(id))))return {ready:false,reason:'SELECTION_MISMATCH',documentCount:rows.length};
const ownRows=rows.filter(x=>x.id&&m.ownIds.includes(x.id));
if(ownRows.some(x=>x.disabled))return {ready:false,reason:'COMPLETE_SELECTION_STATE_UNAVAILABLE',blockReasons:['OWN_LEAF_DISABLED']};
return {ready:true,leaves:ownRows.map(x=>({id:x.id,rwid:r.rwid,title:x.title})),documentCount:rows.length,
  selectedLeafIds:m.ownSelected,allCurrentSelected:m.ownIds.every(id=>m.ownSelected.includes(id)),foreignSelectedCount:0,
  foreignLeafCount:m.foreignIds.length,canSelectAll:m.foreignIds.length===0&&m.orphanCount===0};
`;
// SELECTION_BODY_END
function guardsSource() {
  const module = fs.readFileSync(path.join(__dirname, 'browser_source_capture.mjs'), 'utf8');
  return module.split('// VISIBLE_GUARDS_START\n')[1].split('// VISIBLE_GUARDS_END')[0];
}
function selectionReaderSource() {
  return 'function readPackageSelection(r){' + guardsSource() + SELECTION_BODY + '}';
}
// PACKAGE_SELECTION_READER_END
function buildDownloadCode(r) {
  if (!['edge','chrome'].includes(r.browser) || !/^[a-z0-9][a-z0-9-]{0,47}$/.test(r.session || '')) throw new Error('Invalid session');
  if (!/^\d+$/.test(r.rwid || '') || !/^\d{8}[A-Z]\d{9}$/.test(r.projectNo || '') || !r.unitName) throw new Error('Missing case identity');
  if (!String(r.org || '').trim()) throw new Error('Missing expected organization (消防管辖)');
  if (!Number.isInteger(r.expectedLeafCount) || r.expectedLeafCount < 1) throw new Error('Missing verified leaf count');
  if (!Array.isArray(r.expectedLeafIds) || r.expectedLeafIds.length !== r.expectedLeafCount ||
      r.expectedLeafIds.some(id => typeof id !== 'string' || !/^\d+$/.test(id)) ||
      new Set(r.expectedLeafIds).size !== r.expectedLeafCount) throw new Error('Missing verified document leaf IDs');
  const origin = new URL(r.origin);
  if (!['http:','https:'].includes(origin.protocol) || origin.origin !== r.origin) throw new Error('Invalid origin');
  return `async (page) => {
    const r=${JSON.stringify({origin:r.origin,rwid:r.rwid,projectNo:r.projectNo,unitName:r.unitName,expectedLeafCount:r.expectedLeafCount,expectedLeafIds:r.expectedLeafIds,org:r.org,crossCheckLeafCount:Number.isInteger(r.crossCheckLeafCount)?r.crossCheckLeafCount:null,declaredLeafCount:Number.isInteger(r.declaredLeafCount)?r.declaredLeafCount:null,declaredSourceVisible:r.declaredSourceVisible===true})};
    if(await page.evaluate(()=>document.visibilityState)==='hidden') return {state:'SOURCE_DOCUMENT_HIDDEN',submitted:false};
    if(!page.url().startsWith(r.origin+'/#/xfjd/projectDetail?') || page.url().match(/[?&]RWID=([^&]+)/)?.[1]!==r.rwid) return {state:'IDENTITY_MISMATCH',submitted:false};
    const text=(await page.locator('.avue-view:visible').innerText()).replace(/\\s+/g,'');
    if(!text.includes('项目编号：'+r.projectNo) || !text.includes('单位名称'+r.unitName.replace(/\\s+/g,''))) return {state:'IDENTITY_MISMATCH',submitted:false};
    const leaves=await page.evaluate(${selectionReaderSource()},r);
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
      if(['SOURCE_DOCUMENT_HIDDEN','SOURCE_LOADING','IDENTITY_BLOCKED','COMPLETE_SELECTION_STATE_UNAVAILABLE','IDENTITY_MISMATCH',
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
module.exports={buildDownloadCode,recordActionIntent,selectionReaderSource,guardsSource};
if(require.main===module)try{main();}catch(e){console.error('Download not started: '+e.message);process.exitCode=1;}
