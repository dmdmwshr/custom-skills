const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {buildCode, validateRequest, parseResult} = require('./vault_login.cjs');
const request = {browser:'edge',session:'login-test',origin:'https://example.test',loginUrlPrefix:'https://example.test/#/login',username:'fixture-user',usernamePlaceholder:'User',passwordPlaceholder:'Password',submitText:'Login',successText:'Dashboard',captchaPlaceholder:'Code',captchaValue:'1234'};
const credential = {username:'fixture-user',password:'synthetic-secret-for-test'};
function fixture({fail=false,url=request.loginUrlPrefix,masked=true}={}) {
  const values = {};
  let clicks = 0;
  return {values,get clicks(){return clicks;},page:{
    url:()=>url,
    getByText:()=>({isVisible:async()=>!url.includes('/login'),waitFor:async()=>{if(fail)throw new Error('timeout');}}),
    getByPlaceholder:(name)=>({getAttribute:async()=>masked?'password':'text',fill:async(value)=>{values[name]=value;}}),
    locator:()=>({filter:()=>({press:async(key)=>{assert.equal(key,'Enter');clicks++;if(!fail)url='https://example.test/#/home';}})})
  }};
}
async function run(f,r=request){return vm.runInNewContext('('+buildCode(r,credential)+')')(f.page);}
test('reject mismatched credential account and incomplete captcha',()=>{
  assert.throws(()=>validateRequest(request,{...credential,username:'other'}));
  assert.throws(()=>validateRequest({...request,captchaValue:''},credential));
  assert.throws(()=>validateRequest({...request,password:credential.password},credential));
});
test('no submit or secret fill on an unrelated origin',async()=>{
  const f=fixture({url:'https://example.test.evil/#/login'});assert.equal((await run(f)).state,'TARGET_MISMATCH');assert.equal(f.clicks,0);assert.equal(f.values.Password,undefined);
});
test('already authenticated is read only',async()=>{
  const f=fixture({url:'https://example.test/#/home'});assert.equal((await run(f)).state,'ALREADY_LOGGED_IN');assert.equal(f.clicks,0);
});
test('single submit verifies landing without returning password',async()=>{
  const f=fixture();const result=await run(f);assert.equal(result.state,'LOGIN_VERIFIED');assert.equal(f.clicks,1);assert.ok(!JSON.stringify(result).includes(credential.password));
});
test('failed submit clears password and never retries',async()=>{
  const f=fixture({fail:true});const result=await run(f);assert.equal(result.state,'LOGIN_NOT_CONFIRMED');assert.equal(f.clicks,1);assert.equal(f.values.Password,'');
});
test('visible plaintext password field is rejected before fill',async()=>{
  const f=fixture({masked:false});assert.equal((await run(f)).state,'PASSWORD_NOT_MASKED');assert.equal(f.clicks,0);assert.equal(f.values.Password,'');
});
test('output parsing drops generated code and errors containing secrets',()=>{
  const output='### Result\n{"state":"LOGIN_VERIFIED","submitted":true}\n### Ran Playwright code\n'+credential.password;
  assert.deepEqual(parseResult(output),{state:'LOGIN_VERIFIED',submitted:true});
  assert.equal(parseResult('### Error\n'+credential.password).state,'RESULT_UNKNOWN');
});
