const test=require('node:test'), assert=require('node:assert/strict'),{EventEmitter}=require('node:events');
const {ClaudeAccount}=require('../desktop/account.cjs');
test('Claude status handles signed-out nonzero exit without exposing credentials',async()=>{
  const account=new ClaudeAccount('claude',()=>{},{execFile:(exe,args,opts,cb)=>{assert.deepEqual(args,['auth','status','--json']);cb(Error('exit 1'),JSON.stringify({loggedIn:false,token:'secret'}));}});
  assert.deepEqual(await account.status(),{connected:false,connecting:false,error:'',url:''});
});
test('Claude login uses official CLI subscription flow and exposes only official auth URLs',async()=>{
  const opened=[],child=new EventEmitter();child.stdout=new EventEmitter();child.stderr=new EventEmitter();child.kill=()=>child.emit('exit',1);
  const account=new ClaudeAccount('claude',url=>opened.push(url),{spawn:(exe,args)=>{assert.deepEqual(args,['auth','login','--claudeai']);return child;}});
  await account.connect();
  child.stdout.emit('data','https://evil.test/oauth\nhttps://claude.ai/oauth/authorize?state=example\n');
  assert.equal(account.url,'https://claude.ai/oauth/authorize?state=example');assert.deepEqual(opened,[]);
  child.emit('exit',0);assert.equal(account.login,null);
});
