// Single-action adapter for an existing official Playwright CLI extension session.
'use strict';
const fs = require('node:fs');
const path = require('node:path');
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
    if(!page.url().startsWith(r.origin+'/#/xfjd/projectDetail?') || page.url().match(/[?&]RWID=([^&]+)/)?.[1]!==r.rwid) return {state:'IDENTITY_MISMATCH',submitted:false};
    const text=(await page.locator('.avue-view:visible').innerText()).replace(/\\s+/g,'');
    if(!text.includes('项目编号：'+r.projectNo) || !text.includes('单位名称'+r.unitName.replace(/\\s+/g,''))) return {state:'IDENTITY_MISMATCH',submitted:false};
    const leaves=await page.locator('input[type="checkbox"]').evaluateAll(es=>{
      const selected=es.filter(e=>/^\\d+$/.test(e.value)&&e.closest('label')?.getClientRects().length&&!e.disabled);
      const ids=[...new Set(selected.map(e=>e.value))];
      return {count:ids.length,ids,all:selected.length>0&&selected.every(e=>e.checked)};
    });
    if(leaves.count!==r.expectedLeafCount||!leaves.all||!leaves.ids.every(id=>r.expectedLeafIds.includes(id))) return {state:'SELECTION_MISMATCH',submitted:false};
    const hint=page.waitForEvent('download',{timeout:3000}).then(()=>true).catch(()=>false);
    let state='NATIVE_FILE_CHECK_REQUIRED';
    try { await page.locator('button:visible').filter({hasText:/^开始打包$/}).press('Enter'); }
    catch { state='CLICK_OUTCOME_UNKNOWN'; }
    return {state,submitted:true,downloadEvent:await hint,selectedLeaves:leaves.count};
  }`;
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
  const version=JSON.parse(fs.readFileSync(path.join(path.dirname(runtime.cli_entry),'package.json'),'utf8')).version;
  if(version!=='0.1.21'||version!==runtime.expected_cli_version)throw new Error('CLI version drift');
  // Exclusive checkpoint makes rerunning this request fail before any browser action.
  fs.writeFileSync(checkpoint,JSON.stringify({state:'ACTION_INTENT_RECORDED',rwid:r.rwid,projectNo:r.projectNo,at:new Date().toISOString()}),{encoding:'utf8',flag:'wx'});
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
      if(['IDENTITY_MISMATCH','SELECTION_MISMATCH','NATIVE_FILE_CHECK_REQUIRED','CLICK_OUTCOME_UNKNOWN'].includes(parsed.state))
        result={state:parsed.state,submitted:parsed.submitted,downloadEvent:parsed.downloadEvent,selectedLeaves:parsed.selectedLeaves};
    }catch{}
    fs.writeFileSync(checkpoint,JSON.stringify({...result,rwid:r.rwid,projectNo:r.projectNo,at:new Date().toISOString()}),'utf8');
    write(JSON.stringify(result)+'\n');
    if(result.state!=='NATIVE_FILE_CHECK_REQUIRED')process.exitCode=1;
    output='';
  });
  process.env.NO_UPDATE_NOTIFIER='1';
  for(const key of Object.keys(process.env))if(/^(DEBUG|PWTEST_)/.test(key))delete process.env[key];
  process.argv=[process.execPath,runtime.cli_entry,'-s='+r.browser+'-'+r.session,'run-code',code];
  require(runtime.cli_entry);
}
module.exports={buildDownloadCode};
if(require.main===module)try{main();}catch(e){console.error('Download not started: '+e.message);process.exitCode=1;}
