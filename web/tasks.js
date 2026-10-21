import { html, useState, useEffect } from '/vendor/preact-htm.js';

const ACTIVE = new Set(['queued', 'running', 'reviewing', 'steering', 'stopping', 'pausing']);
const LABEL = {queued:'Queued', running:'Working', reviewing:'Checking work', steering:'Changing direction', stopping:'Stopping', pausing:'Pausing', paused:'Paused', stopped:'Stopped', interrupted:'Ready to resume', waiting:'Needs your input', needs_review:'Ready for review', done:'Verified', failed:'Needs attention', superseded:'Replaced by a new plan'};

export function TaskSummary({ tasks }) {
  const active = tasks.filter(t => ACTIVE.has(t.state));
  const waiting = tasks.filter(t => ['waiting', 'interrupted', 'needs_review', 'failed'].includes(t.state));
  if (!active.length && !waiting.length) return null;
  const focus = (active[0] || waiting[0]).id;
  return html`<button class="task-summary" onClick=${()=>document.getElementById('task-'+focus)?.scrollIntoView({behavior:'smooth',block:'center'})}>
    <span class=${'task-dot'+(active.length?' working':'')}></span><span>${active.length ? `${active.length} task${active.length===1?'':'s'} in progress` : `${waiting.length} task${waiting.length===1?' needs':'s need'} attention`}</span><span class="task-summary-hint">You can keep talking to Ken</span><span aria-hidden="true">↑</span>
  </button>`;
}

export function TaskCard({ task: t, api, toast, renderText }) {
  const [expanded, setExpanded] = useState(false), [details, setDetails] = useState(null);
  const [editing, setEditing] = useState(false), [instruction, setInstruction] = useState(''), [pending, setPending] = useState(false);
  useEffect(()=>{
    if (!expanded) return;
    let alive=true;
    api(`/api/tasks/${t.id}`).then(d=>{if(alive)setDetails(d);}).catch(e=>{if(alive)toast(e.message);});
    return ()=>{alive=false;};
  },[expanded,t.updated]);
  const control = async (action, text='') => {
    setPending(true);
    try {
      await api(`/api/tasks/${t.id}/control`, {json:{action,instruction:text,expected_revision:t.revision}});
      setEditing(false);setInstruction('');
    } catch(e) { toast(e.message); }
    finally { setPending(false); }
  };
  const active = ACTIVE.has(t.state), changing = ['steering','stopping','pausing'].includes(t.state);
  const status = t.state==='running' && t.applied_revision<t.revision ? 'Reading your instructions' : LABEL[t.state] || t.state;
  return html`<article class="task-card" id=${'task-'+t.id} aria-label=${t.title}>
    <div class="task-card-head"><span class=${'task-dot '+(active?'working':t.state==='done'?'complete':'')}></span><b>${t.title}</b><span class=${'task-state '+t.state} role="status">${status}</span></div>
    <p class="task-activity">${active ? t.activity || 'Waiting to start' : t.summary || t.activity || 'Partial work is saved.'}</p>
    ${t.state==='steering' && html`<p class="task-note">Your change is saved. Waiting for the worker to switch direction.</p>`}
    <div class="task-actions">
      <button class="text-action" aria-expanded=${expanded} onClick=${()=>setExpanded(v=>!v)}>${expanded?'Less':'Details'}</button>
      ${!changing && html`<button class="text-action" disabled=${pending} aria-expanded=${editing} onClick=${()=>setEditing(v=>!v)}>${t.state==='waiting'?'Answer Ken':'Change the plan'}</button>`}
      <span class="task-action-spacer"></span>
      ${active && !changing && html`<button class="btn sm ghost" disabled=${pending} onClick=${()=>control('pause')}>Pause</button><button class="btn sm" disabled=${pending} aria-label=${'Stop '+t.title} onClick=${()=>control('stop')}>Stop</button>`}
      ${['paused','stopped','interrupted','waiting'].includes(t.state) && html`<button class="btn sm" disabled=${pending} onClick=${()=>control('resume')}>Resume</button>`}
      ${['needs_review','done'].includes(t.state) && html`<button class="btn sm" disabled=${pending} onClick=${()=>control('review')}>${t.state==='done'?'Check again':'Check the work'}</button>`}
      ${['failed','needs_review'].includes(t.state) && html`<button class="btn sm" disabled=${pending} onClick=${()=>control('retry')}>Retry</button>`}
    </div>
    ${editing && html`<form class="task-edit" onSubmit=${e=>{e.preventDefault();if(instruction.trim())control('steer',instruction.trim());}}><label for=${'task-instruction-'+t.id}>${t.state==='waiting'?'Your answer':'What should change?'}</label><textarea id=${'task-instruction-'+t.id} value=${instruction} onInput=${e=>setInstruction(e.target.value)} placeholder="Tell Ken what to do differently…" rows="3"/><div class="row"><button class="btn sm primary" disabled=${pending||!instruction.trim()}>Send change</button><button type="button" class="btn sm ghost" onClick=${()=>setEditing(false)}>Cancel</button></div></form>`}
    ${expanded && html`<div class="task-detail"><p><b>The brief</b></p><div>${renderText(t.objective)}</div>${t.criteria && html`<p><b>Success looks like</b></p><div>${renderText(t.criteria)}</div>`}${t.evidence && html`<p><b>Evidence</b></p><div>${renderText(t.evidence)}</div>`}
      ${details?.attempts?.length>0 && html`<p><b>Work history</b></p><ol>${details.attempts.map(a=>html`<li>Attempt ${a.number}${a.mode==='review'?' · Review':''} · ${LABEL[a.state]||a.state}${a.summary && html`<p>${a.summary}</p>`}</li>`)}</ol>`}
    </div>`}
  </article>`;
}
