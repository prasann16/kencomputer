const { spawn, execFile } = require('node:child_process');
class ClaudeAccount {
  constructor(executable, openExternal, deps = {}) { this.executable=executable;this.openExternal=openExternal;this.spawn=deps.spawn||spawn;this.execFile=deps.execFile||execFile;this.login=null;this.error='';this.url=''; }
  async status() {
    const data=await new Promise((resolve,reject)=>this.execFile(this.executable,['auth','status','--json'],{timeout:15000,windowsHide:true},(err,out)=>{
      try { const result=JSON.parse(out);if(typeof result.loggedIn!=='boolean')throw Error();resolve(result); }
      catch { reject(new Error('Could not check your Claude account. Try again.')); }
    }));
    return {connected:data.loggedIn,connecting:!!this.login,error:this.error,url:this.url};
  }
  async connect() {
    if(this.login)return this.status();
    this.error='';this.url='';
    const child=this.spawn(this.executable,['auth','login','--claudeai'],{windowsHide:true,stdio:['ignore','pipe','pipe']});this.login=child;
    let buffer='';
    const output=chunk=>{buffer=(buffer+chunk.toString()).slice(-16000);for(const match of buffer.matchAll(/https:\/\/[^\s<>"\x1b]+/g)){
      try{const url=new URL(match[0]);if(['claude.ai','platform.claude.com','console.anthropic.com'].includes(url.hostname)&&url.pathname.includes('oauth')&&this.url!==url.href){this.url=url.href;}}catch{}
    }};
    child.stdout.on('data',output);child.stderr.on('data',output);
    const timer=setTimeout(()=>{if(this.login===child){this.error='Sign-in timed out. Try connecting again.';child.kill();this.login=null;}},300000);
    const done=()=>{clearTimeout(timer);if(this.login===child)this.login=null;};
    child.on('error',()=>{this.error='Could not open Claude sign-in. Try again.';done();});
    child.on('exit',code=>{if(code && !this.error)this.error='Sign-in did not finish. Try again.';done();});
    return {connected:false,connecting:true,error:'',url:this.url};
  }
  close(){if(this.login)this.login.kill();this.login=null;}
}
module.exports={ClaudeAccount};
