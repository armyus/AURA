const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const state = {decisions:[], decision:null, tab:'model', evaluations:[], selectedEvaluationId:null, scenarios:[], robustnessRuns:[], lastComparison:null, passages:[], modelConfigured:false, modelDirty:false, authenticated:false};
const uid = () => crypto.randomUUID();
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path, options={}) {
  const response = await fetch(path,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(options.headers||{})}});
  const data = response.status===204?{}:await response.json().catch(()=>({error:'Unexpected server response.'}));
  if(!response.ok) throw new Error(data.error||`Request failed (${response.status})`);
  return data;
}
function toast(message){const el=$('#toast');el.textContent=message;el.classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.classList.remove('show'),3300)}
function errorToast(error){toast(error.message||String(error))}
function modelOf(){return state.decision?.currentRevision?.model}
function fmtDate(value){try{return new Intl.DateTimeFormat(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}).format(new Date(value))}catch{return ''}}
function setTitle(value){$('#crumbTitle').textContent=value}
async function refreshDecisions(selectId){
  const data=await api('/api/decisions');state.decisions=data.decisions;
  $('#decisionList').innerHTML=state.decisions.length?state.decisions.map(d=>`<button class="decision-link ${d.id===state.decision?.id?'active':''}" data-open="${esc(d.id)}" title="${esc(d.title)}"><span class="decision-icon" aria-hidden="true">◈</span><span class="decision-text">${esc(d.title)}</span></button>`).join(''):'<div class="hint" style="padding:9px 10px;color:#8799aa">Your decisions will appear here.</div>';
  $$('.decision-link').forEach(b=>b.onclick=()=>openDecision(b.dataset.open));
  if(selectId) await openDecision(selectId); else if(state.decision) await openDecision(state.decision.id); else renderWelcome();
}
function renderWelcome(){
  state.decision=null;state.tab='model';setTitle('Your workspace');
  $('#content').innerHTML=`<div class="hero"><div class="hero-copy"><div class="hero-kicker">A CLEARER WAY TO COMPARE YOUR OPTIONS</div><h1>Make a big choice easier to think through.</h1><p>AURA helps you compare choices using what matters to you. You provide the options, priorities, and ratings; AURA lays out the trade-offs, points out gaps, and lets you try “what if?” changes. You stay in control of the decision.</p></div><button class="button" id="heroCreate">Start a decision <span aria-hidden="true">→</span></button></div>
  <div class="callout"><strong>For example:</strong> compare a software internship, a research role, and a portfolio project. Rate each for learning, mentorship, workload, and pay. AURA shows how they compare, why one scores higher under your priorities, and which ratings are still missing. It does not choose for you.</div>
  <div class="welcome-grid"><article class="welcome-card"><div class="number">01</div><h3>List the choices</h3><p>Add the options you are genuinely considering, such as three summer opportunities.</p></article><article class="welcome-card"><div class="number">02</div><h3>Say what matters</h3><p>Choose priorities, set how much each matters, then rate each option from low to high.</p></article><article class="welcome-card"><div class="number">03</div><h3>Compare and try changes</h3><p>Review the result and missing details. Try a different priority or rating to see how the ranking changes.</p></article></div>
  <div class="section-head"><div><h2>Your decision workspace</h2><p>Private by default. Your decision data stays in this local workspace.</p></div></div>
  ${state.decisions.length?`<div class="welcome-grid">${state.decisions.map(d=>`<button class="welcome-card" style="text-align:left;cursor:pointer" data-open="${esc(d.id)}"><div class="number">◈</div><h3>${esc(d.title)}</h3><p>${esc(d.question)}</p><p style="margin-top:11px">Revision ${d.revision_no||1} · Updated ${esc(fmtDate(d.updated_at))}</p></button>`).join('')}</div>`:'<div class="empty-card">No decisions yet. Create one to get started.</div>'}`;
  $('#heroCreate').onclick=renderCreate;
  $$('[data-open]').forEach(b=>b.onclick=()=>openDecision(b.dataset.open));
}
function renderCreate(){
  setTitle('New decision');
  $('#content').innerHTML=`<div class="page-head"><div><h1>Start with the choice</h1><p>Define the question and the alternatives you are considering. You can refine criteria and ratings next.</p></div><button class="button secondary" id="cancelCreate">Cancel</button></div>
  <div class="layout-two"><section class="card"><div class="card-title"><div><h3>Decision setup</h3><p>AURA keeps your assumptions visible and editable.</p></div></div>
  <div class="field"><label for="decisionTitle">Decision name <span class="label-required">*</span></label><input id="decisionTitle" maxlength="160" placeholder="e.g. Choose an internship for summer"></div>
  <div class="field"><label for="decisionQuestion">What are you deciding? <span class="label-required">*</span></label><textarea id="decisionQuestion" maxlength="2000" placeholder="What choice do you need to make, and by when?"></textarea></div>
  <div class="field"><label for="optionNames">Options <span class="label-required">*</span></label><textarea id="optionNames" placeholder="One option per line\nProduct internship\nResearch assistantship"></textarea><small>Add 2–5 viable alternatives. Avoid scoring an imaginary perfect option.</small></div>
  <div class="field"><label for="criterionNames">What matters most? <span class="label-required">*</span></label><textarea id="criterionNames" placeholder="One criterion per line\nMentorship\nWeekly hours\nLearning opportunities"></textarea><small>Begin with 2–12 criteria. You set their direction, importance, and optional hard limits in the model.</small></div>
  <button class="button primary" id="createDecision">Create decision workspace</button>
  </section><aside class="card"><div class="card-title"><div><h3>AURA’s role</h3><p>A structured comparison supports your judgment; it does not make the choice for you.</p></div></div><div class="callout">Ratings and weights reflect your inputs. A small score difference is not proof that one option is better in the real world.</div><div class="field"><label for="assistPrompt">Start a draft from your description <span class="chip">Optional</span></label><textarea id="assistPrompt" placeholder="Describe your options and what matters to you"></textarea><button class="button secondary" id="assistButton">${state.modelConfigured?'Suggest a draft':'Create a starter draft'}</button><small>${state.modelConfigured?'Local AI may help; AURA can still create a basic starter draft if it is unavailable. Review every suggestion.':'AURA can create a simple editable starter draft from your description. Local AI is optional.'}</small></div><div id="assistResult"></div></aside></div>`;
  $('#cancelCreate').onclick=renderWelcome;
  $('#createDecision').onclick=async()=>{
    try{
      const options=$('#optionNames').value.split(/\n/).map(x=>x.trim()).filter(Boolean).slice(0,5);
      const names=$('#criterionNames').value.split(/\n/).map(x=>x.trim()).filter(Boolean).slice(0,12);
      const criteria=names.map(name=>({id:uid(),name,direction:'benefit',importance:3,valueType:'ordinal',scale:{min:1,max:5}}));
      const model={options:options.map(name=>({id:uid(),name})),criteria,assessments:{}};
      const created=await api('/api/decisions',{method:'POST',body:JSON.stringify({title:$('#decisionTitle').value,question:$('#decisionQuestion').value,model})});
      toast('Decision workspace created');await refreshDecisions(created.id);
    }catch(e){errorToast(e)}
  };
  $('#assistButton').onclick=async()=>{try{const proposal=await api(`/api/decisions/${state.decision?.id||'draft'}/assist`,{method:'POST',body:JSON.stringify({prompt:$('#assistPrompt').value})});showProposal(proposal)}catch(e){errorToast(e)}};
}
function showProposal(proposal){
  const localAi=proposal.source==='local-ai';
  $('#assistResult').innerHTML=`<div class="warning">${localAi?'Review this local AI draft before using it.':'Starter draft created from words in your description.'} No ratings are included; review and adjust every option and criterion.</div><div class="source-list"><strong>Options</strong>${proposal.options.map(x=>`<div class="source-row">${esc(x)}</div>`).join('')}<strong>Suggested criteria</strong>${proposal.criteria.map(x=>`<div class="source-row">${esc(x.name)} <span>${esc(x.direction)} · importance ${esc(x.importance)}</span></div>`).join('')}</div><button class="button secondary small-button" id="useProposal">Use as editable draft</button>`;
  $('#useProposal').onclick=()=>{$('#optionNames').value=proposal.options.join('\n');$('#criterionNames').value=proposal.criteria.map(x=>x.name).join('\n');toast('Draft copied into the setup form. Review and save it yourself.')};
}
async function openDecision(id){
  try{state.decision=await api(`/api/decisions/${encodeURIComponent(id)}`);const [ev,sc,docs,robustnessRuns]=await Promise.all([api(`/api/decisions/${id}/evaluations`),api(`/api/decisions/${id}/scenarios`),api(`/api/decisions/${id}/documents`),api(`/api/decisions/${id}/robustness-runs`)]);state.evaluations=ev.evaluations;state.selectedEvaluationId=null;state.scenarios=sc.scenarios;state.documents=docs.documents;state.robustnessRuns=robustnessRuns.runs;state.passages=[];state.tab='model';state.lastComparison=null;state.modelDirty=false;renderWorkspace();refreshSidebarActive()}catch(e){errorToast(e)}
}
function refreshSidebarActive(){ $$('.decision-link').forEach(b=>b.classList.toggle('active',b.dataset.open===state.decision?.id)); }
function renderWorkspace(){
  const d=state.decision,m=modelOf();setTitle(d.title);
  $('#content').innerHTML=`<div class="page-head"><div><div class="eyebrow">DECISION WORKSPACE</div><h1>${esc(d.title)}</h1><p>${esc(d.question)}</p><div class="meta-row"><span class="chip">Revision ${d.currentRevision.revisionNo}</span><span class="chip">${m.options.length} options</span><span class="chip">${m.criteria.length} criteria</span><span class="chip">${state.documents.length} sources</span><span class="chip">Updated ${esc(fmtDate(d.updated_at))}</span></div></div><div class="actions"><button class="button outline small-button" id="exportBrief">Decision brief</button><button class="button outline small-button" id="exportDecision">Export data</button><button class="button danger small-button" id="deleteDecision">Delete</button></div></div>
  <div class="tabs" role="tablist" aria-label="Decision workspace sections"><button class="tab active" role="tab" id="tab-model" aria-controls="panel-model" aria-selected="true" tabindex="0" data-tab="model">Compare options</button><button class="tab" role="tab" id="tab-results" aria-controls="panel-results" aria-selected="false" tabindex="-1" data-tab="results">Your results <span class="chip">${state.evaluations.length}</span></button><button class="tab" role="tab" id="tab-evidence" aria-controls="panel-evidence" aria-selected="false" tabindex="-1" data-tab="evidence">Sources <span class="chip">${state.documents.length}</span></button><button class="tab" role="tab" id="tab-scenarios" aria-controls="panel-scenarios" aria-selected="false" tabindex="-1" data-tab="scenarios">Try a change <span class="chip">${state.scenarios.length}</span></button></div>
  <section class="panel active" id="panel-model" role="tabpanel" tabindex="0" aria-labelledby="tab-model">${modelPanel(m)}</section><section class="panel" id="panel-results" role="tabpanel" tabindex="0" aria-labelledby="tab-results">${resultsPanel()}</section><section class="panel" id="panel-evidence" role="tabpanel" tabindex="0" aria-labelledby="tab-evidence">${evidencePanel()}</section><section class="panel" id="panel-scenarios" role="tabpanel" tabindex="0" aria-labelledby="tab-scenarios">${scenarioPanel(m)}</section>`;
  $$('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
  $('.tabs').onkeydown=e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=$$('.tab'),at=tabs.indexOf(document.activeElement),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(at+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();tabs[next].click()};
  $('#modelForm').onsubmit=saveModel;
  $('#saveAndCompare').onclick=saveAndCompare;
  $('#modelForm').oninput=markModelDirty;$('#modelForm').onchange=markModelDirty;
  $('#modelForm').onclick=e=>{const choice=e.target.closest('.rating-choice, .rating-choice-clear');if(!choice)return;const cell=choice.closest('.matrix-cell'),input=$('.assessment-value',cell),value=choice.dataset.rating;input.value=value==='0'?'':value;$$('.rating-choice, .rating-choice-clear',cell).forEach(button=>{const selected=button===choice;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected))});cell.querySelector('.rating-value-label').textContent=value==='0'?'Not rated':`${value} / 5`;markModelDirty()};
  $$('.criterion-type').forEach(el=>el.onchange=()=>el.closest('[data-criterion]').querySelector('.numeric-scale').classList.toggle('hidden',el.value!=='numeric'));
  $$('.criterion-direction').forEach(el=>el.onchange=()=>el.closest('[data-criterion]').querySelectorAll('.scale-target,.scale-tolerance').forEach(x=>x.classList.toggle('hidden',el.value!=='target')));
  $$('.hard-constraint').forEach(el=>el.onchange=()=>el.closest('[data-criterion]').querySelector('.constraint-editor').classList.toggle('hidden',!el.checked));
  $('#addOption').onclick=()=>updateDraft(m=>{if(m.options.length>=5)throw new Error('A decision supports up to five options.');const id=uid();m.options.push({id,name:`Alternative ${m.options.length+1}`});m.assessments[id]={}});
  $('#addCriterion').onclick=()=>updateDraft(m=>{if(m.criteria.length>=12)throw new Error('A decision supports up to twelve criteria.');m.criteria.push({id:uid(),name:`Criterion ${m.criteria.length+1}`,direction:'benefit',importance:3,valueType:'ordinal',scale:{min:1,max:5}})});
  $$('.remove-option').forEach(b=>b.onclick=()=>updateDraft(m=>{if(m.options.length<=2)throw new Error('Keep at least two alternatives.');m.options=m.options.filter(o=>o.id!==b.dataset.id);delete m.assessments[b.dataset.id]}));
  $$('.remove-criterion').forEach(b=>b.onclick=()=>updateDraft(m=>{if(m.criteria.length<=2)throw new Error('Keep at least two criteria.');m.criteria=m.criteria.filter(c=>c.id!==b.dataset.id);for(const values of Object.values(m.assessments))delete values[b.dataset.id]}));
  $$('#runEvaluation, #runEvaluationResults').forEach(b=>b.onclick=runEvaluation);
  $('#uploadButton').onclick=uploadDocument;
  $('#evidenceSearch').onclick=searchEvidence;
  $('#evidenceAssist').onclick=assistEvidence;
  $('#evidenceQuery').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();searchEvidence()}};
  $('#createScenario').onclick=createScenario;
  $('#ratingCriterion').onchange=()=>{const c=modelOf().criteria.find(x=>x.id===$('#ratingCriterion').value),input=$('#ratingValue');if(c?.valueType==='numeric'){input.min=c.scale.min;input.max=c.scale.max;input.step='any';input.placeholder=`${c.scale.min}–${c.scale.max} ${c.unit||''}`}else{input.min=1;input.max=5;input.step=1;input.placeholder='1–5'}};$('#ratingCriterion').onchange();
  $('#runSensitivity').onclick=runSensitivity;
  $('#runRobustness').onclick=runRobustness;
  ['#robustnessOption','#robustnessCriterion'].forEach(selector=>$(selector)?.addEventListener('change',updateRobustnessDomains));
  $('#robustnessWeightCriterion')?.addEventListener('change',updateRobustnessWeightDefaults);
  $$('.replay-robustness').forEach(b=>b.onclick=()=>replayRobustness(b.dataset.id));
  $('#explainResult')?.addEventListener('click',explainLatest);
  $('#exportDecision').onclick=exportDecision;
  $('#exportBrief').onclick=exportBrief;
  $('#deleteDecision').onclick=deleteDecision;
  $('#editComparison').onclick=()=>switchTab('model');
  $$('.history-item').forEach(b=>b.onclick=()=>selectEvaluation(b.dataset.id));
  $$('.delete-document').forEach(b=>b.onclick=async()=>{if(!confirm('Remove this document and all extracted passages?'))return;try{await api(`/api/documents/${b.dataset.id}`,{method:'DELETE'});toast('Source removed');await openDecision(state.decision.id);switchTab('evidence')}catch(e){errorToast(e)}});
  $$('.rerun-scenario').forEach(b=>b.onclick=()=>rerunScenario(b.dataset.id));
  $$('.apply-button').forEach(b=>b.onclick=()=>toggleApplyForm(b.dataset.id));
  $$('.status-select').forEach(s=>s.onchange=()=>setEvidenceStatus(s.dataset.id,s.value));
  if(state.lastComparison) renderComparison(state.lastComparison);
}
function switchTab(tab){state.tab=tab;$$('.tab').forEach(b=>{const active=b.dataset.tab===tab;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1});$$('.panel').forEach(p=>p.classList.toggle('active',p.id===`panel-${tab}`));}
function modelPanel(m){
  const options=m.options.map(o=>`<div class="option-edit"><input class="option-name" data-option-id="${esc(o.id)}" value="${esc(o.name)}" aria-label="Option name"><button class="remove-option" type="button" data-id="${esc(o.id)}" aria-label="Remove ${esc(o.name)}">×</button></div>`).join('');
  let cleanedCount=0;
  const criterionCards=m.criteria.map(c=>{
    const scale=c.scale||{},match=c.name.match(/\s+[—–-]\s*importance\s+(\d+(?:\.\d+)?),\s*(benefit|cost|target)\s*$/i);
    const cleanName=match?c.name.slice(0,match.index).trim():c.name;
    const direction=match?match[2].toLowerCase():c.direction;
    const importance=match?Number(match[1]):c.importance;
    if(match)cleanedCount++;
    const criterionSettings=`<details class="advanced-settings"><summary>Advanced settings</summary><div class="advanced-grid"><label>Direction<select class="criterion-direction"><option value="benefit" ${direction==='benefit'?'selected':''}>Higher is better</option><option value="cost" ${direction==='cost'?'selected':''}>Lower is better</option><option value="target" ${direction==='target'?'selected':''}>Closer to target is better</option></select></label><label>Rating type<select class="criterion-type"><option value="ordinal" ${(c.valueType||'ordinal')==='ordinal'?'selected':''}>Simple rating (1–5)</option><option value="numeric" ${c.valueType==='numeric'?'selected':''}>Numeric measurement</option></select></label><div class="numeric-scale ${c.valueType==='numeric'?'':'hidden'}"><input class="scale-min" type="number" step="any" placeholder="Minimum" value="${esc(scale.min??'')}"><input class="scale-max" type="number" step="any" placeholder="Maximum" value="${esc(scale.max??'')}"><input class="scale-target ${direction==='target'?'':'hidden'}" type="number" step="any" placeholder="Target" value="${esc(scale.target??'')}"><input class="scale-tolerance ${direction==='target'?'':'hidden'}" type="number" step="any" placeholder="Allowed distance" value="${esc(scale.tolerance??'')}"><input class="criterion-unit" type="text" maxlength="20" placeholder="Unit (e.g. hours)" value="${esc(c.unit||'')}"></div><label class="constraint-cell"><input type="checkbox" class="hard-constraint" ${c.constraint?'checked':''}> This is a must-have</label><div class="constraint-editor ${c.constraint?'':'hidden'}"><select class="constraint-op" aria-label="Must-have condition"><option value="lte" ${c.constraint?.operator==='lte'?'selected':''}>At most</option><option value="gte" ${c.constraint?.operator==='gte'?'selected':''}>At least</option><option value="eq" ${c.constraint?.operator==='eq'?'selected':''}>Exactly</option></select><input class="constraint-threshold" type="number" step="any" placeholder="Limit value" value="${esc(c.constraint?.threshold??'')}"></div></div></details>`;
    const ratings=m.options.map(o=>{const a=m.assessments?.[o.id]?.[c.id],v=typeof a==='object'?a.value:a,status=typeof a==='object'?a.status:'';const control=(c.valueType||'ordinal')==='ordinal'?`<input class="assessment-value hidden" data-option="${esc(o.id)}" type="number" min="1" max="5" step="1" value="${v??''}" aria-label="${esc(o.name)} · ${esc(cleanName)} rating"><div class="rating-control"><div class="rating-choices" role="group" aria-label="Rate ${esc(o.name)} for ${esc(cleanName)}">${[1,2,3,4,5].map(n=>`<button class="rating-choice ${Number(v)===n?'selected':''}" type="button" data-rating="${n}" aria-label="${n} out of 5" aria-pressed="${Number(v)===n}">${n}</button>`).join('')}</div><div class="rating-choice-meta"><button class="rating-choice-clear ${v===undefined?'selected':''}" type="button" data-rating="0" aria-pressed="${v===undefined}">Not rated</button><span class="rating-value-label">${v===undefined?'Not rated':`${v} / 5`}</span></div></div>`:`<input class="assessment-value numeric-assessment" data-option="${esc(o.id)}" type="number" step="any" value="${v??''}" placeholder="${c.unit||'Enter value'}" aria-label="${esc(o.name)} · ${esc(cleanName)}"><div class="hint">${status==='verified'?'Source verified':v===undefined?'Not provided':esc(c.unit||'')}</div>`;return `<div class="matrix-cell option-rating"><span class="option-rating-name">${esc(o.name)}</span>${control}</div>`}).join('');
    return `<article class="criterion-card" data-criterion="${esc(c.id)}"><div class="criterion-card-head"><div class="criterion-name-group"><input class="criterion-name-input" value="${esc(cleanName)}" aria-label="Criterion name"><button class="remove-criterion" type="button" data-id="${esc(c.id)}" aria-label="Remove ${esc(cleanName)}">×</button></div><label class="importance-field"><span>Importance</span><input class="criterion-importance" type="number" min="0.1" max="10" step="0.1" value="${esc(importance)}" aria-label="${esc(cleanName)} importance"><small>How much this matters to you</small></label></div>${criterionSettings}<div class="criterion-ratings"><div class="criterion-ratings-label">Rate each option <span>1 = low · 5 = high</span></div>${ratings}</div></article>`;
  }).join('');
  if(cleanedCount)state.modelDirty=true;
  return `<form id="modelForm"><div class="layout-two"><div><div class="card"><div class="card-title"><div><h3>1. Your options</h3><p>These are the choices you’re comparing.</p></div></div><div class="option-list">${options}</div><button class="button outline small-button" id="addOption" type="button" ${m.options.length>=5?'disabled':''}>＋ Add option (${m.options.length}/5)</button></div><div class="card criteria-section"><div class="card-title"><div><h3>2. What matters to you?</h3><p>Set each priority, then tap a rating for every option.</p></div></div><div class="callout" style="margin-bottom:14px">Use 1 for a low value and 5 for a high value. AURA interprets which is better using the direction: higher, lower, or closer to a target.</div>${cleanedCount?`<div class="warning">Some criteria had settings typed into their names. They’re now separated into the proper fields. Save changes to keep the cleaned-up names.</div>`:''}<div class="criteria-list">${criterionCards}</div><button class="button outline small-button" id="addCriterion" type="button" ${m.criteria.length>=12?'disabled':''}>＋ Add thing that matters (${m.criteria.length}/12)</button><div class="actions" style="margin-top:16px"><button class="button primary" type="submit">Save my changes</button><button class="button primary" id="saveAndCompare" type="button">Save &amp; compare</button><button class="button secondary" id="runEvaluation" type="button" ${state.modelDirty?'disabled':''}>Compare my options</button></div><div class="status-line">${state.modelDirty?'◉ Save your changes before comparing.':'◉ Your comparison is saved with these inputs, so you can revisit it later.'}</div></div></div><aside class="card"><div class="card-title"><div><h3>What AURA checks</h3><p>Helpful context for your comparison</p></div></div><div class="source-list"><div class="source-row"><span>Options to compare</span><strong>${m.options.length}</strong></div><div class="source-row"><span>Things that matter</span><strong>${m.criteria.length}</strong></div><div class="source-row"><span>Must-have rules</span><strong>${m.criteria.filter(c=>c.constraint).length}</strong></div><div class="source-row"><span>Saved version</span><strong>${state.decision.currentRevision.revisionNo}</strong></div></div><div class="callout" style="margin-top:15px">A must-have rule can disqualify an option. AURA also flags missing ratings, so an incomplete comparison is not presented as a firm answer.</div></aside></div></form>`;
}
async function saveModel(event){
  event.preventDefault();try{
    const m=collectModelForm();
    state.decision=await api(`/api/decisions/${state.decision.id}/revisions`,{method:'POST',body:JSON.stringify({model:m})});state.modelDirty=false;toast(`Saved revision ${state.decision.currentRevision.revisionNo}`);await refreshDecisions(state.decision.id);
  }catch(e){errorToast(e)}
}
async function saveAndCompare(){
  try{
    if(state.modelDirty){
      const model=collectModelForm();
      state.decision=await api(`/api/decisions/${state.decision.id}/revisions`,{method:'POST',body:JSON.stringify({model})});
      state.modelDirty=false;
    }
    const saved=await api(`/api/decisions/${state.decision.id}/evaluations`,{method:'POST',body:JSON.stringify({revisionId:state.decision.currentRevision.id})});
    state.evaluations.unshift(saved);state.evaluations=dedupe(state.evaluations);state.selectedEvaluationId=saved.id;
    toast('Saved inputs and updated comparison');renderWorkspace();switchTab('results');
  }catch(e){errorToast(e)}
}
function collectModelForm(){
    const m=structuredClone(modelOf());
    for(const input of $$('.option-name')){const option=m.options.find(x=>x.id===input.dataset.optionId);if(option)option.name=input.value.trim()}
    for(const row of $$('[data-criterion]')){
      const c=m.criteria.find(x=>x.id===row.dataset.criterion);c.direction=$('.criterion-direction',row).value;c.importance=Number($('.criterion-importance',row).value);
      c.name=$('.criterion-name-input',row).value.trim();if(!c.name)throw new Error('Every criterion needs a name.');
      c.valueType=$('.criterion-type',row).value;
      if(c.valueType==='numeric'){
        const lo=$('.scale-min',row).value,hi=$('.scale-max',row).value;c.unit=$('.criterion-unit',row).value.trim();
        if(lo===''||hi==='')throw new Error(`Set minimum and maximum for ${c.name}.`);
        c.scale={min:Number(lo),max:Number(hi)};
        if(c.direction==='target'){const target=$('.scale-target',row).value,tolerance=$('.scale-tolerance',row).value;if(target===''||tolerance==='')throw new Error(`Set target and tolerance for ${c.name}.`);c.scale.target=Number(target);c.scale.tolerance=Number(tolerance)}
      }else{c.scale={min:1,max:5};delete c.unit}
      const checked=$('.hard-constraint',row).checked;
      if(checked){const threshold=$('.constraint-threshold',row).value;if(threshold==='')throw new Error(`Enter a hard limit for ${c.name}.`);c.constraint={operator:$('.constraint-op',row).value,threshold:Number(threshold)}}else delete c.constraint;
      for(const cell of $$('.matrix-cell',row)){
        const input=$('.assessment-value',cell),option=input.dataset.option;m.assessments[option]??={};
        if(input.value===''||(input.classList.contains('rating-slider')&&Number(input.value)===0))delete m.assessments[option][c.id];else{const old=m.assessments[option][c.id];if(!(old&&typeof old==='object'&&Number(old.value)===Number(input.value)))m.assessments[option][c.id]={value:Number(input.value),status:'user'}}
      }
    }
    if(m.options.some(o=>!o.name))throw new Error('Every option needs a name.');
    return m;
}
function markModelDirty(){if(state.modelDirty)return;state.modelDirty=true;$$('#runEvaluation, #runEvaluationResults, #createScenario, #runSensitivity').forEach(button=>button.disabled=true);const status=$('#modelForm .status-line');if(status)status.textContent='◉ Unsaved edits: save a new revision before evaluating.'}
function updateDraft(mutator){
  try{const model=collectModelForm();mutator(model);state.decision.currentRevision.model=model;state.modelDirty=true;renderWorkspace();switchTab('model')}
  catch(e){errorToast(e)}
}
function latestEval(){return state.evaluations[0]}
function selectedEval(){return state.evaluations.find(e=>e.id===state.selectedEvaluationId)||latestEval()}
function resultsPanel(){
  const selected=selectedEval();return `<div class="layout-two"><div><div class="card"><div class="card-title"><div><h3>Your comparison</h3><p id="selectedResultLabel">${selected?`Showing saved comparison from ${esc(fmtDate(selected.created_at))}.`:'Add ratings, save them, and compare your options to see the result.'}</p><p class="result-edit-hint">${state.modelDirty?'Save your updated inputs first, then compare again.':'To change this result, update priorities or ratings and save them in Compare options.'}</p></div><div class="actions"><button class="button outline small-button" id="editComparison">Change options &amp; ratings</button><button class="button primary small-button" id="runEvaluationResults" ${state.modelDirty?'disabled':''}>${state.modelDirty?'Save inputs first':'Compare again'}</button></div></div><div id="evaluationContent">${selected?evaluationMarkup(selected):'<div class="empty-card">Go to Compare options, enter ratings, save your changes, then choose Compare my options.</div>'}</div></div><div id="explanationArea"></div></div><aside class="card"><div class="card-title"><div><h3>Past comparisons</h3><p>Select one to review it. To change a result, change its inputs and compare again.</p></div></div><div class="history-list">${state.evaluations.length?state.evaluations.map(ev=>`<button type="button" class="history-item ${selected?.id===ev.id?'selected':''}" data-id="${esc(ev.id)}" aria-pressed="${selected?.id===ev.id}"><span>Revision ${esc(ev.revision_id.slice(0,8))} · ${esc(ev.algorithm_version)}</span><strong>${esc(fmtDate(ev.created_at))}</strong></button>`).join(''):'<div class="hint">No comparisons yet.</div>'}</div><button class="button secondary small-button" style="margin-top:13px" id="explainResult" ${selected?'':'disabled'}>Explain this comparison</button><div class="hint" style="margin-top:9px">${state.modelConfigured?'Uses local AI when available; otherwise AURA explains the calculated scores directly.':'AURA explains the calculated scores directly. Optional local AI can add a narrative.'}</div></aside></div>`;
}
function selectEvaluation(id){
  const evaluation=state.evaluations.find(item=>item.id===id);if(!evaluation)return;
  state.selectedEvaluationId=id;$('#evaluationContent').innerHTML=evaluationMarkup(evaluation);$('#selectedResultLabel').textContent=`Showing saved comparison from ${fmtDate(evaluation.created_at)}.`;
  $$('.history-item').forEach(button=>{const selected=button.dataset.id===id;button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected))});
  $('#explainResult').disabled=!selectedEval();$('#explanationArea').innerHTML='';
}
function evaluationMarkup(evaluation){
  const result=evaluation.result;const sorted=[...result.options].sort((a,b)=>(a.rank??99)-(b.rank??99)||a.name.localeCompare(b.name));
  const statusText=result.status==='complete'?'Top option under these inputs':result.status==='tied'?'Leading options are tied':result.status==='incomplete'?'More information is needed':'No option meets all hard constraints';
  return `<div class="metric-row"><div class="metric"><small>Evaluation state</small><strong style="font-size:15px">${esc(statusText)}</strong></div><div class="metric"><small>Algorithm</small><strong style="font-size:13px">${esc(result.algorithmVersion)}</strong></div><div class="metric"><small>Input revision</small><strong style="font-size:14px">${esc(evaluation.input_hash.slice(0,12))}</strong></div></div>${result.warnings.map(w=>`<div class="warning">${esc(w)}</div>`).join('')}<div class="option-results">${sorted.map(o=>`<article class="result-card"><div class="result-top"><div><div class="result-name">${esc(o.name)} ${o.rank?`<span class="badge green">Rank ${o.rank}</span>`:''}</div><div class="meta-row">${o.constraintStatus==='pass'?'<span class="badge green">Constraints pass</span>':o.constraintStatus==='fail'?'<span class="badge red">Constraint failed</span>':'<span class="badge amber">Constraint unknown</span>'}${o.missing.length?`<span class="badge amber">${o.missing.length} missing</span>`:''}</div></div><div class="score">${o.displayScore===null?'—':Number(o.displayScore).toFixed(2)} <em>/ 1.00</em></div></div>${o.displayScore!==null?`<div class="progress"><span style="width:${Math.max(0,Math.min(100,o.displayScore*100))}%"></span></div>`:''}${o.violations.map(v=>`<div class="warning error">${esc(v.criterion)} is ${esc(v.value)}; required ${esc(v.operator)} ${esc(v.threshold)}.</div>`).join('')}${o.missing.length?`<div class="warning">Complete: ${o.missing.map(esc).join(', ')}.</div>`:''}<div class="contributions">${o.contributions.map(c=>`<div class="contrib"><strong>${esc(c.criterion)} · ${Math.round(c.weight*100)}%</strong>${c.rawValue} → ${c.contribution.toFixed(3)} contribution</div>`).join('')}</div>${o.warnings.map(w=>`<div class="hint">${esc(w)}</div>`).join('')}</article>`).join('')}</div><div class="hint" style="margin-top:12px">Scores are normalized weighted comparisons within this decision. They are not forecasts and do not establish real-world success probabilities.</div>`;
}
async function runEvaluation(){
  if(state.modelDirty)return toast('Save your model edits as a new revision before evaluating.');
  try{const saved=await api(`/api/decisions/${state.decision.id}/evaluations`,{method:'POST',body:JSON.stringify({revisionId:state.decision.currentRevision.id})});state.evaluations.unshift(saved);state.evaluations=dedupe(state.evaluations);state.selectedEvaluationId=saved.id;toast('Comparison saved');renderWorkspace();switchTab('results')}catch(e){errorToast(e)}
}
function dedupe(items){const seen=new Set();return items.filter(x=>!seen.has(x.id)&&seen.add(x.id))}
async function explainLatest(){const selected=selectedEval();if(!selected)return;const button=$('#explainResult');if(button){button.disabled=true;button.textContent='Preparing explanation…'}try{const answer=await api(`/api/decisions/${state.decision.id}/explain`,{method:'POST',body:JSON.stringify({evaluationId:selected.id})});const badge=answer.source==='local-ai'?'Local AI assisted':'Calculated explanation';$('#explanationArea').innerHTML=`<div class="card" style="margin-top:13px"><div class="card-title"><div><h3>Why the results look this way</h3><p>Calculated values are unchanged. Review the assumptions before acting.</p></div><span class="badge ${answer.source==='local-ai'?'amber':'green'}">${badge}</span></div><p style="line-height:1.65;color:#4d6175">${esc(answer.explanation)}</p></div>`}catch(e){errorToast(e)}finally{if(button){button.disabled=!selectedEval();button.textContent='Explain this comparison'}}}
function evidencePanel(){
  return `<div class="layout-two"><div class="card"><div class="card-title"><div><h3>Source material</h3><p>Uploaded documents are extracted locally in the background and split into source-linked passages.</p></div></div><div class="field"><label for="sourceFile">Add PDF, TXT, or Markdown</label><input type="file" id="sourceFile" accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown"><small>10 MB maximum. Scanned PDFs and images are not supported yet. Original uploads are discarded after text extraction; passages are stored locally.</small></div><button class="button primary" id="uploadButton">Upload and process</button><div class="section-head"><div><h2>Uploaded sources</h2><p>${state.documents.length} document(s)</p></div></div><div class="source-list">${state.documents.map(d=>`<div class="source-row"><span>▤ ${esc(d.filename)} <small>· ${d.parse_status==='ready'?`${d.chunkCount} passages`:esc(d.parse_status||'ready')}${d.parse_error?` · ${esc(d.parse_error)}`:''}</small></span><button class="button danger small-button delete-document" data-id="${esc(d.id)}">Remove</button></div>`).join('')||'<div class="empty-card">No sources attached yet.</div>'}</div></div>
  <div class="card"><div class="card-title"><div><h3>Find evidence</h3><p>Search only this decision’s uploaded material. Verify a passage before applying a value.</p></div></div><div class="actions"><input class="control" id="evidenceQuery" placeholder="Search documents…" style="flex:1"><button class="button secondary" id="evidenceSearch">Search</button></div><div class="actions" style="margin-top:9px"><button class="button outline small-button" id="evidenceAssist">Suggest facts and questions</button><span class="hint">Uses local AI when available; otherwise finds exact matching quotes. Review suggestions before use.</span></div><div id="evidenceAssistResult"></div><div class="hint" style="margin:9px 0 15px">AURA surfaces relevant excerpts. It does not treat retrieved text as a verified fact.</div><div id="evidenceResults" class="evidence-list">${state.passages.length?state.passages.map(p=>evidenceCard(p)).join(''):'<div class="empty-card">Search your uploaded sources to see relevant passages.</div>'}</div></div></div>`;
}
function evidenceCard(p){
  return `<article class="evidence-card"><div class="evidence-meta"><span>▤ ${esc(p.filename)} ${p.page_no?`· Page ${p.page_no}`:''} · Relevance ${esc(p.relevance??'—')}</span><span class="badge ${p.verified_status==='verified'?'green':p.verified_status==='rejected'?'red':'amber'}">${esc(p.verified_status||'unverified')}</span></div>${p.matchReasons?.length?`<div class="hint">Matched by: ${p.matchReasons.map(esc).join(' · ')}</div>`:''}<blockquote>${esc(p.text)}</blockquote><div class="evidence-tools"><select class="status-select" data-id="${esc(p.id)}" aria-label="Evidence status"><option value="unverified" ${p.verified_status==='unverified'?'selected':''}>Unverified</option><option value="verified" ${p.verified_status==='verified'?'selected':''}>Verified passage</option><option value="rejected" ${p.verified_status==='rejected'?'selected':''}>Reject passage</option></select><button class="button outline small-button apply-button" data-id="${esc(p.id)}" ${p.verified_status==='verified'?'':'disabled'}>Apply a value to the model</button></div><div class="apply-target hidden" id="apply-${esc(p.id)}"></div></article>`;
}
async function uploadDocument(){
  const file=$('#sourceFile')?.files?.[0];if(!file)return toast('Choose a supported file first.');const data=new FormData();data.append('file',file);
  try{
    const decisionId=state.decision.id;const queued=await api(`/api/decisions/${decisionId}/documents`,{method:'POST',body:data});
    toast('Upload received; extracting in the background');await openDecision(decisionId);switchTab('evidence');
    for(let attempt=0;attempt<60;attempt++){
      const current=await api(`/api/decisions/${decisionId}/documents`),doc=current.documents.find(d=>d.id===queued.id);
      if(!doc||['ready','failed'].includes(doc.parse_status)){await openDecision(decisionId);switchTab('evidence');toast(doc?.parse_status==='ready'?'Source extracted and ready to search':doc?.parse_error||'Document processing failed');return}
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    toast('Document is still processing. Its status will update when extraction finishes.');
  }catch(e){errorToast(e)}
}
async function searchEvidence(){
  const query=$('#evidenceQuery').value.trim();if(!query)return toast('Enter a search phrase.');
  try{const result=await api(`/api/decisions/${state.decision.id}/evidence?q=${encodeURIComponent(query)}`);state.passages=result.passages;$('#evidenceResults').innerHTML=state.passages.map(evidenceCard).join('')||'<div class="empty-card">No matching passages. Try different terms.</div>';$$('.verify-button').forEach(b=>b.onclick=()=>verifyEvidence(b.dataset.id));$$('.apply-button').forEach(b=>b.onclick=()=>toggleApplyForm(b.dataset.id));$$('.status-select').forEach(s=>s.onchange=()=>setEvidenceStatus(s.dataset.id,s.value))}catch(e){errorToast(e)}
}
async function assistEvidence(){
  const query=$('#evidenceQuery').value.trim();if(!query)return toast('Enter what you want to check in your sources first.');
  const button=$('#evidenceAssist');button.disabled=true;button.textContent='Reviewing source passages…';
  try{
    const result=await api(`/api/decisions/${state.decision.id}/evidence/assist`,{method:'POST',body:JSON.stringify({query})});
    const facts=result.facts.map(f=>`<article class="evidence-card"><div class="evidence-meta"><strong>Candidate fact</strong><span>${esc(f.filename)} ${f.pageNo?`· Page ${f.pageNo}`:''}</span></div><p>${esc(f.claim)}</p><blockquote>${esc(f.quote)}</blockquote><div class="hint">${f.optionHint?`Option to review: ${esc(f.optionHint)} · `:''}${f.criterionHint?`Possible criterion: ${esc(f.criterionHint)} · `:''}Check the source and confirm the meaning yourself before verifying or applying it.</div></article>`).join('');
    const questions=result.questions.map(q=>`<li>${esc(q)}</li>`).join('');
    const localAi=result.source==='local-ai';
    $('#evidenceAssistResult').innerHTML=`<div class="card" style="margin:12px 0"><div class="card-title"><div><h3>${localAi?'Local AI suggestions to review':'Matching source passages and review questions'}</h3><p>Quoted text is copied from retrieved passages. Suggestions are not verified facts and never update scores.</p></div><span class="badge amber">Needs review</span></div>${facts||'<div class="hint">No matching excerpts were found for this search. Try a different phrase or review the document manually.</div>'}${questions?`<h4>Questions that could clarify the decision</h4><ul>${questions}</ul>`:''}</div>`;
  }catch(e){errorToast(e)}finally{button.disabled=false;button.textContent='Suggest facts and questions'}
}
async function setEvidenceStatus(id,status){try{const updated=await api(`/api/evidence/${id}`,{method:'PATCH',body:JSON.stringify({status})});if(updated.decisionRevision){const query=$('#evidenceQuery')?.value;await openDecision(state.decision.id);switchTab('evidence');if(query){$('#evidenceQuery').value=query;await searchEvidence()}}else{state.passages=state.passages.map(p=>p.id===id?{...p,...updated}:p);searchEvidenceRefresh()}toast(`Passage marked ${status}`)}catch(e){errorToast(e)}}
function searchEvidenceRefresh(){const container=$('#evidenceResults');if(container&&state.passages.length)container.innerHTML=state.passages.map(evidenceCard).join('');$$('.apply-button').forEach(b=>b.onclick=()=>toggleApplyForm(b.dataset.id));$$('.status-select').forEach(s=>s.onchange=()=>setEvidenceStatus(s.dataset.id,s.value))}
function verifyEvidence(id){setEvidenceStatus(id,'verified')}
function toggleApplyForm(id){
  const box=$(`#apply-${CSS.escape(id)}`);if(!box)return;box.classList.toggle('hidden');if(box.innerHTML)return;
  const m=modelOf();box.innerHTML=`<div class="apply-form"><select class="apply-option">${m.options.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select><select class="apply-criterion">${m.criteria.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select><input class="apply-value" type="number" step="any" placeholder="Confirmed value" required><button class="button primary small-button apply-confirm">Confirm and save new revision</button></div><div class="hint">Confirming creates a new revision and retains this source citation.</div>`;
  $('.apply-confirm',box).onclick=async()=>{try{const value=$('.apply-value',box).value;if(value==='')throw new Error('Enter the value you confirm from this passage.');state.decision=await api(`/api/decisions/${state.decision.id}/evidence/apply`,{method:'POST',body:JSON.stringify({chunkId:id,optionId:$('.apply-option',box).value,criterionId:$('.apply-criterion',box).value,value:Number(value)})});toast('Verified value saved in a new revision');await refreshDecisions(state.decision.id);switchTab('evidence')}catch(e){errorToast(e)}};
}
function robustnessRunHistory(){
  return state.robustnessRuns.length?state.robustnessRuns.map(run=>`<div class="source-row"><div><strong>${esc(fmtDate(run.created_at))}</strong><small> · ${Number(run.settings.iterations).toLocaleString()} samples · seed ${esc(run.settings.seed)} · revision ${esc(run.revision_id.slice(0,8))}</small></div><button class="button outline small-button replay-robustness" data-id="${esc(run.id)}">Replay settings</button></div>`).join(''):'<div class="empty-card">Saved simulation runs will appear here.</div>';
}
function updateRobustnessDomains(){
  const optionId=$('#robustnessOption')?.value,criterionId=$('#robustnessCriterion')?.value;
  const criterion=modelOf()?.criteria.find(c=>c.id===criterionId),option=modelOf()?.options.find(o=>o.id===optionId);if(!criterion||!option)return;
  const ordinal=criterion.valueType!=='numeric',low=ordinal?1:Number(criterion.scale?.min),high=ordinal?5:Number(criterion.scale?.max);
  const raw=modelOf()?.assessments?.[optionId]?.[criterionId],current=raw==null?NaN:Number(typeof raw==='object'?raw?.value:raw),span=ordinal?1:Math.max(.1,(high-low)*.1);
  const from=$('#robustnessMin'),to=$('#robustnessMax');from.min=low;from.max=high;to.min=low;to.max=high;from.step=ordinal?1:'any';to.step=ordinal?1:'any';
  from.value=Number.isFinite(current)?Math.max(low,current-span):low;to.value=Number.isFinite(current)?Math.min(high,current+span):high;
}
function updateRobustnessWeightDefaults(){
  const criterion=modelOf()?.criteria.find(c=>c.id===$('#robustnessWeightCriterion')?.value);if(!criterion)return;
  const importance=Number(criterion.importance||3);$('#robustnessWeightMin').value=Math.max(.1,importance-1);$('#robustnessWeightMax').value=Math.min(10,importance+1);
}
function renderRobustnessResult(result){
  const ranked=[...result.options].sort((a,b)=>b.winShare-a.winShare);
  const resultBox=$('#robustnessResult');if(!resultBox)return;
  resultBox.innerHTML=`<div class="source-list" style="margin-top:14px"><strong>Ranking stability · ${result.iterations.toLocaleString()} samples · seed ${result.seed}</strong><small>Saved run ${esc((result.runId||'').slice(0,8))}</small>${ranked.map(o=>`<div class="source-row" style="display:block"><div style="display:flex;justify-content:space-between;gap:12px"><span>${esc(o.name)}</span><strong>${(o.winShare*100).toFixed(1)}% lead share</strong></div><div class="progress"><span style="width:${Math.max(0,Math.min(100,o.winShare*100))}%"></span></div><small>Mean score ${o.meanScore===null?'—':o.meanScore.toFixed(3)} · 5th–95th percentile ${o.scoreP05===null?'—':o.scoreP05.toFixed(3)}–${o.scoreP95===null?'—':o.scoreP95.toFixed(3)}</small></div>`).join('')}<div class="hint">No clear winner in ${(result.noClearWinnerShare*100).toFixed(1)}% of samples. Lead shares are conditional on the saved revision, ranges, and seed.</div></div>`;
}
async function replayRobustness(id){
  const run=state.robustnessRuns.find(x=>x.id===id);if(!run)return;
  try{const result=await api(`/api/decisions/${state.decision.id}/robustness`,{method:'POST',body:JSON.stringify(run.settings)});renderRobustnessResult(result);const saved=await api(`/api/decisions/${state.decision.id}/robustness-runs`);state.robustnessRuns=saved.runs;$('#robustnessRunHistory').innerHTML=robustnessRunHistory();$$('.replay-robustness').forEach(b=>b.onclick=()=>replayRobustness(b.dataset.id));toast('Simulation replayed with its saved settings')}catch(e){errorToast(e)}
}
function scenarioPanel(m){
  const baseline=state.evaluations.find(e=>!e.scenario_id);
  const criteriaOptions=m.criteria.map(c=>`<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('');
  const optionOptions=m.options.map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('');
  const firstCriterion=m.criteria[0], firstOption=m.options[0];
  const current=m.assessments?.[firstOption?.id]?.[firstCriterion?.id];
  const currentValue=Number(typeof current==='object'?current?.value:current);
  const ordinal=firstCriterion?.valueType!=='numeric';
  const domainMin=ordinal?1:firstCriterion?.scale?.min, domainMax=ordinal?5:firstCriterion?.scale?.max;
  const spread=ordinal?1:Math.max(0.1,(domainMax-domainMin)*0.1);
  const defaultLow=Number.isFinite(currentValue)?Math.max(domainMin,currentValue-spread):domainMin;
  const defaultHigh=Number.isFinite(currentValue)?Math.min(domainMax,currentValue+spread):domainMax;
  return `<div class="layout-two"><div><div class="card"><div class="card-title"><div><h3>Try a “what if?”</h3><p>See whether your result changes when your priorities or a rating changes.</p></div></div>${baseline?`<div class="callout">Starting comparison: ${esc(fmtDate(baseline.created_at))} · this original result stays unchanged.</div>`:'<div class="warning">Compare your options first. Then you can try changes here.</div>'}<div class="field"><label for="scenarioName">Name this change</label><input id="scenarioName" maxlength="160" placeholder="e.g. Learning matters more than pay"></div><div class="override-row"><div class="field"><label>Which priority do you want to change?</label><select id="weightCriterion">${criteriaOptions}</select></div><div class="field"><label>How important is it now?</label><input id="weightValue" type="number" min="0.1" max="10" step="0.1" value="4"></div><div class="field"><label>Which option rating changes?</label><select id="ratingOption">${optionOptions}</select></div><div class="field"><label>For which criterion?</label><select id="ratingCriterion">${criteriaOptions}</select></div></div><div class="form-grid"><div class="field"><label>New rating (optional, 1–5)</label><input id="ratingValue" type="number" min="1" max="5" step="1" placeholder="1–5"></div><div class="field"><label>&nbsp;</label><button class="button primary" id="createScenario" ${baseline?'':'disabled'}>Save and compare change</button></div></div><div class="hint">This comparison uses your changed inputs while keeping the original result untouched. You can compare how the ranking moves.</div><div id="scenarioComparison"></div></div>
  <div class="card" style="margin-top:15px"><div class="card-title"><div><h3>Weight sensitivity</h3><p>See whether the leader changes as one importance value moves across a range.</p></div></div><div class="scenario-controls"><div class="field"><label>Criterion</label><select id="sensitivityCriterion">${criteriaOptions}</select></div><div class="field"><label>Importance from</label><input id="sensitivityMin" type="number" min="0.1" step="0.1" value="1"></div><div class="field"><label>Importance to</label><input id="sensitivityMax" type="number" min="0.1" step="0.1" value="5"></div></div><button class="button secondary" style="margin-top:12px" id="runSensitivity" ${state.modelDirty?'disabled':''}>Run sensitivity sweep</button><div id="sensitivityResult"></div></div>
  <div class="card" style="margin-top:15px"><div class="card-title"><div><h3>Ranking robustness</h3><p>Sample a rating range, a priority range, or both to see how often each option leads.</p></div></div><div class="scenario-controls"><div class="field"><label>Option with uncertain rating</label><select id="robustnessOption">${optionOptions}</select></div><div class="field"><label>Rating criterion</label><select id="robustnessCriterion">${criteriaOptions}</select></div><div class="field"><label>Rating from</label><input id="robustnessMin" type="number" step="any" value="${defaultLow}" min="${domainMin}" max="${domainMax}"></div><div class="field"><label>Rating to</label><input id="robustnessMax" type="number" step="any" value="${defaultHigh}" min="${domainMin}" max="${domainMax}"></div><div class="field"><label>Priority to vary</label><select id="robustnessWeightCriterion">${criteriaOptions}</select></div><div class="field"><label>Importance from</label><input id="robustnessWeightMin" type="number" min="0.1" max="10" step="0.1" value="${Math.max(0.1,Number(firstCriterion?.importance||3)-1)}"></div><div class="field"><label>Importance to</label><input id="robustnessWeightMax" type="number" min="0.1" max="10" step="0.1" value="${Math.min(10,Number(firstCriterion?.importance||3)+1)}"></div></div><div class="actions" style="margin-top:12px"><button class="button secondary" id="runRobustness" ${state.modelDirty?'disabled':''}>Run 1,000 reproducible samples</button></div><div class="hint">Ranges use independent uniform samples; ordinal ratings round to whole numbers. Results depend on these assumptions, not real-world probabilities. Exact settings and results are saved for replay.</div><div id="robustnessResult"></div><div class="section-head"><div><h3>Saved simulation runs</h3><p>Re-run a saved range with its original revision, settings, and seed.</p></div></div><div id="robustnessRunHistory">${robustnessRunHistory()}</div></div></div>
  <aside class="card"><div class="card-title"><div><h3>Saved scenarios</h3><p>Each run remains reproducible and tied to its baseline.</p></div></div><div class="source-list">${state.scenarios.map(s=>`<div class="scenario-card"><strong>${esc(s.name)}</strong><div class="hint" style="margin:5px 0 10px">${esc(fmtDate(s.created_at))} · ${Object.keys(s.overrides.weights||{}).length+Object.keys(s.overrides.assessments||{}).length} override(s)</div><button class="button outline small-button rerun-scenario" data-id="${esc(s.id)}">Run comparison</button></div>`).join('')||'<div class="empty-card">Your saved what-if scenarios will appear here.</div>'}</div></aside></div>`;
}
async function createScenario(){
  if(state.modelDirty)return toast('Save your model edits as a new revision before running a scenario.');
  const baseline=state.evaluations.find(e=>!e.scenario_id);if(!baseline)return toast('Run a baseline evaluation first.');
  try{
    const name=$('#scenarioName').value.trim()||'What-if scenario',weightId=$('#weightCriterion').value,weightValue=$('#weightValue').value;
    const overrides={weights:{[weightId]:Number(weightValue)},assessments:{}};
    const rating=$('#ratingValue').value;if(rating!=='')overrides.assessments[$('#ratingOption').value]={[$('#ratingCriterion').value]:Number(rating)};
    const scenario=await api(`/api/decisions/${state.decision.id}/scenarios`,{method:'POST',body:JSON.stringify({name,baselineEvaluationId:baseline.id,overrides})});
    const compared=await api(`/api/scenarios/${scenario.id}/evaluate`,{method:'POST',body:'{}'});toast('Scenario compared with the saved baseline');await openDecision(state.decision.id);state.lastComparison=compared.comparison;switchTab('scenarios');renderComparison(state.lastComparison)
  }catch(e){errorToast(e)}
}
async function rerunScenario(id){try{const compared=await api(`/api/scenarios/${id}/evaluate`,{method:'POST',body:'{}'});await openDecision(state.decision.id);state.lastComparison=compared.comparison;switchTab('scenarios');renderComparison(state.lastComparison);toast('Scenario comparison refreshed')}catch(e){errorToast(e)}}
function renderComparison(comparison){
  const box=$('#scenarioComparison');if(!box)return;
  box.innerHTML=`<div class="scenario-card"><div class="card-title"><div><h3>Baseline vs scenario</h3><p>Score deltas and ranks are compared for the same decision revision.</p></div></div><div class="comparison-row" style="font-weight:800;color:#718196"><span>Option</span><span>Baseline</span><span>Scenario</span><span>Change</span><span>Rank</span></div>${comparison.scoreChanges.map(x=>`<div class="comparison-row"><strong>${esc(x.name)}</strong><span>${x.baselineScore===null?'—':Number(x.baselineScore).toFixed(2)}</span><span>${x.scenarioScore===null?'—':Number(x.scenarioScore).toFixed(2)}</span><span class="delta ${x.delta>0?'up':x.delta<0?'down':''}">${x.delta===null?'—':`${x.delta>0?'+':''}${x.delta.toFixed(3)}`}</span><span>${x.baselineRank??'—'} → ${x.scenarioRank??'—'}</span></div>`).join('')}</div>`;
}
async function runSensitivity(){
  if(state.modelDirty)return toast('Save your model edits as a new revision before running sensitivity analysis.');
  try{const result=await api(`/api/decisions/${state.decision.id}/sensitivity`,{method:'POST',body:JSON.stringify({revisionId:state.decision.currentRevision.id,criterionId:$('#sensitivityCriterion').value,minimum:Number($('#sensitivityMin').value),maximum:Number($('#sensitivityMax').value),steps:21})});
    const names=Object.fromEntries(modelOf().options.map(o=>[o.id,o.name]));const maxScore=Math.max(0.01,...result.points.flatMap(p=>Object.values(p.scores).filter(x=>x!==null)));
    const drivers=result.tornado?.drivers||[],maxImpact=Math.max(.000001,...drivers.map(x=>x.absoluteImpact));
    $('#sensitivityResult').innerHTML=`<div class="section-head"><div><h2>Ranking across the range</h2><p>${result.transitions.map(t=>`${t.fromWeight}: ${esc(names[t.winner]||'No clear leader')}`).join(' · ')}</p></div></div><div class="sensitivity-chart" role="img" aria-label="Scores across 21 weight settings">${result.points.map(p=>{const best=Math.max(0,...Object.values(p.scores).filter(x=>x!==null));return `<div class="sensitivity-bar" style="height:${Math.max(3,best/maxScore*100)}%" title="Weight ${p.weight} · leader ${esc(names[p.winner]||'none')}"></div>`}).join('')}</div><div class="hint">This is a deterministic one-variable sweep. It shows how this model responds to the chosen weight range; it is not an outcome probability.</div><div class="section-head"><div><h2>What separates the top two</h2><p>${result.tornado?.leader?`${esc(result.tornado.leader)} vs ${esc(result.tornado.runnerUp)}`:'A complete baseline with two feasible options is needed.'}</p></div></div><div class="source-list">${drivers.map(x=>`<div class="source-row" style="display:block"><div style="display:flex;justify-content:space-between"><span>${esc(x.criterion)}</span><strong>${x.scoreDifference>0?'+':''}${x.scoreDifference.toFixed(3)} contribution gap</strong></div><div class="progress"><span style="width:${Math.max(1,x.absoluteImpact/maxImpact*100)}%"></span></div></div>`).join('')||'<div class="hint">No comparable top-two contribution breakdown is available yet.</div>'}</div>`
  }catch(e){errorToast(e)}
}
async function runRobustness(){
  if(state.modelDirty)return toast('Save your model edits before running robustness analysis.');
  try{
    const optionId=$('#robustnessOption').value,criterionId=$('#robustnessCriterion').value,weightId=$('#robustnessWeightCriterion').value;
    const minimum=Number($('#robustnessMin').value),maximum=Number($('#robustnessMax').value),weightMin=Number($('#robustnessWeightMin').value),weightMax=Number($('#robustnessWeightMax').value);
    const settings={revisionId:state.decision.currentRevision.id,assessmentRanges:{[optionId]:{[criterionId]:[minimum,maximum]}},weightRanges:{[weightId]:[weightMin,weightMax]},iterations:1000,seed:42};
    const result=await api(`/api/decisions/${state.decision.id}/robustness`,{method:'POST',body:JSON.stringify(settings)});
    renderRobustnessResult(result);const saved=await api(`/api/decisions/${state.decision.id}/robustness-runs`);state.robustnessRuns=saved.runs;$('#robustnessRunHistory').innerHTML=robustnessRunHistory();$$('.replay-robustness').forEach(b=>b.onclick=()=>replayRobustness(b.dataset.id));
  }catch(e){errorToast(e)}
}
async function exportDecision(){try{const data=await api(`/api/decisions/${state.decision.id}/export`);const json=JSON.stringify(data,null,2);const blob=new Blob([json],{type:'application/json'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`aura-${state.decision.id}.json`;a.click();URL.revokeObjectURL(url);toast('Decision and evidence export downloaded')}catch(e){errorToast(e)}}
async function exportBrief(){
  try{
    const data=await api(`/api/decisions/${state.decision.id}/export`),decision=data.decision,revision=decision.currentRevision,latest=data.evaluations.find(e=>!e.scenario_id),result=latest?.result;
    const options=result?.options||[],criteria=revision.model.criteria,assessments=revision.model.assessments||{};
    const optionRows=options.map(o=>`<tr><td>${esc(o.name)}</td><td>${o.rank??'—'}</td><td>${o.displayScore===null?'Incomplete':Number(o.displayScore).toFixed(2)}</td><td>${esc(o.constraintStatus)}</td><td>${o.missing.map(esc).join(', ')||'None'}</td></tr>`).join('');
    const weightRows=criteria.map(c=>{const weight=result?.normalizedWeights?.[c.id];return `<tr><td>${esc(c.name)}</td><td>${weight===undefined?esc(c.importance):`${(weight*100).toFixed(1)}%`}</td><td>${esc(c.direction)}</td><td>${esc(c.constraint?`${c.constraint.operator} ${c.constraint.threshold}`:'None')}</td></tr>`}).join('');
    const sourceRows=data.documents.map(d=>`<li>${esc(d.filename)} · ${d.chunkCount} passages</li>`).join('')||'<li>No sources attached.</li>';
    const scenarioRows=data.scenarios.map(s=>`<li>${esc(s.name)} · ${Object.keys(s.overrides.weights||{}).length+Object.keys(s.overrides.assessments||{}).length} assumption change(s)</li>`).join('')||'<li>No saved scenarios.</li>';
    const html=`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>AURA Decision Brief — ${esc(decision.title)}</title><style>body{font:16px/1.55 system-ui,Segoe UI,sans-serif;color:#172a3a;max-width:900px;margin:40px auto;padding:0 24px}h1,h2{color:#10253c}h1{font-size:32px;margin-bottom:4px}.eyebrow{color:#52708c;font-weight:700;letter-spacing:.12em;font-size:12px}table{border-collapse:collapse;width:100%;margin:14px 0 26px}th,td{text-align:left;border-bottom:1px solid #dce4eb;padding:10px 12px}th{background:#f1f5f8}aside{background:#eef4f7;padding:14px 18px;border-radius:8px}.muted{color:#5d7183}button{padding:10px 16px;margin:0 0 20px}@media print{body{margin:0 auto;padding:0 8mm}button{display:none}h2{break-after:avoid}table{break-inside:avoid}}</style><body><button onclick="window.print()">Print / Save as PDF</button><div class="eyebrow">AURA · DECISION BRIEF</div><h1>${esc(decision.title)}</h1><p>${esc(decision.question)}</p><p class="muted">Prepared ${new Date().toLocaleDateString()} · Revision ${revision.revisionNo} · Input hash ${esc(revision.inputHash)}</p><aside>Scores compare the options using the saved inputs. They do not predict outcomes. Review assumptions, missing information, and constraints before acting.</aside><h2>Baseline comparison</h2>${result?`<p>Status: <strong>${esc(result.status)}</strong>${result.recommendation?` · Leading option: <strong>${esc(options.find(o=>o.optionId===result.recommendation)?.name||'')}</strong>`:''}</p><table><thead><tr><th>Option</th><th>Rank</th><th>Score / 1</th><th>Constraints</th><th>Missing inputs</th></tr></thead><tbody>${optionRows}</tbody></table>`:'<p>No baseline evaluation has been saved for this revision.</p>'}<h2>Criteria and weights</h2><table><thead><tr><th>Criterion</th><th>Weight</th><th>Direction</th><th>Hard limit</th></tr></thead><tbody>${weightRows}</tbody></table><h2>Saved scenarios</h2><ul>${scenarioRows}</ul><h2>Sources</h2><ul>${sourceRows}</ul><p class="muted">This brief reflects the saved revision and baseline shown above. Scenario sampling is conditional on user-entered assumptions and is not a real-world probability.</p></body></html>`;
    const blob=new Blob([html],{type:'text/html;charset=utf-8'}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`aura-brief-${state.decision.id}.html`;a.click();URL.revokeObjectURL(url);toast('Printable decision brief downloaded');
  }catch(e){errorToast(e)}
}
async function deleteDecision(){if(!confirm('Delete this decision, its sources, scenarios, and evaluations? This cannot be undone.'))return;try{await api(`/api/decisions/${state.decision.id}`,{method:'DELETE'});state.decision=null;toast('Decision deleted');await refreshDecisions()}catch(e){errorToast(e)}}
async function init(){
  $('#newDecisionButton').onclick=renderCreate;
  $('#signOutButton').onclick=async()=>{try{await api('/api/auth/logout',{method:'POST',body:'{}'});state.authenticated=false;$('#signOutButton').hidden=true;renderAuth(true)}catch(e){errorToast(e)}};
  $('#content').innerHTML='<div class="loading">Opening your local decision workspace…</div>';
  try{
    const auth=await api('/api/auth/status');state.authenticated=auth.authenticated;$('#signOutButton').hidden=!state.authenticated;
    if(!auth.configured){renderAuth(true);return}
    if(!auth.authenticated){renderAuth(false);return}
    const health=await api('/api/health');state.modelConfigured=health.modelConfigured;await refreshDecisions()
  }catch(e){$('#content').innerHTML=`<div class="card"><h2>AURA could not start</h2><div class="warning error">${esc(e.message)}</div><p>Restart the local server from the project folder and reload this page.</p></div>`}
}
function renderAuth(setup){
  setTitle(setup?'Secure your workspace':'Sign in');$('#signOutButton').hidden=true;
  $('#content').innerHTML=`<div class="layout-two"><section class="card" style="max-width:520px"><div class="eyebrow">LOCAL WORKSPACE ACCESS</div><h1>${setup?'Set a workspace password':'Welcome back'}</h1><p>${setup?'Create the password that protects this AURA workspace on this device. Use at least 12 characters.':'Enter your AURA workspace password to continue.'}</p><form id="authForm"><div class="field"><label for="authPassword">${setup?'Create password':'Password'}</label><input id="authPassword" type="password" autocomplete="${setup?'new-password':'current-password'}" minlength="${setup?'12':'1'}" maxlength="1024" required autofocus></div><button class="button primary" type="submit">${setup?'Protect workspace':'Sign in'}</button><div class="hint" style="margin-top:14px">This local sign-in protects casual access to the workspace. Keep your device account secure; it does not encrypt the database file.</div></form></section><aside class="card"><h2>Your data stays local</h2><p>AURA stores decisions and extracted evidence in this workspace. Your password is stored as a salted, slow hash; sign-in sessions expire after 12 hours.</p></aside></div>`;
  $('#authForm').onsubmit=async event=>{event.preventDefault();const button=$('#authForm button[type=submit]');button.disabled=true;try{await api(`/api/auth/${setup?'setup':'login'}`,{method:'POST',body:JSON.stringify({password:$('#authPassword').value})});state.authenticated=true;toast(setup?'Workspace password saved':'Signed in');await init()}catch(e){errorToast(e);button.disabled=false}};
}
init();
