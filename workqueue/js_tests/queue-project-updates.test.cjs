const test = require('node:test');
const assert = require('node:assert/strict');
const {attach} = require('../../static/js/queue-project-updates.js');
const names = ['project_name','project_street','project_city','project_state','project_zip','contact_full_name','company','contact_email','contact_phone','billing_street','billing_city','billing_state','billing_zip','service_needed','description','project_context','requested_deadline'];
global.window = {location: {origin: 'https://example.invalid'}};
function setup({initial={},bound=false,fetcher}={}) {
  const node = (value='') => ({value,required:false,hidden:false,disabled:false,listeners:{},
    addEventListener(name,fn){this.listeners[name]=fn;},dispatch(name,event={}){return this.listeners[name]?.(event);}});
  const fields = Object.fromEntries([...names,'project','kind','update_source'].map(n=>[n,node(initial[n]||'')]));
  fields.kind.value ||= 'new';fields.contact_email.required=true;fields.contact_phone.required=true;
  fields.project.options=[{value:'',textContent:'New project'},{value:'A',textContent:'Lot 12 Park Street'},{value:'B',textContent:'Townhouse Boston'}];
  const nodes=Object.fromEntries(['save','status','replace','search','accept','keep','load'].map(n=>[n,node()]));
  const form=node();form.elements={namedItem:n=>fields[n.slice(4)]};
  form.dataset={bound:bound?'1':'0',defaultsUrl:'/work-queue/projects/00000000-0000-0000-0000-000000000000/defaults/'};
  form.querySelector=s=>nodes[s.match(/data-project-(.*)\]/)[1]];
  form.dispatchEvent=()=>{};
  const pending=[];
  attach(form,fetcher || (()=>new Promise((resolve,reject)=>pending.push({resolve,reject}))));
  const select=id=>{fields.project.value=id;fields.project.dispatch('change');};
  const answer=(index,id,extra={})=>pending[index].resolve({ok:true,json:async()=>({project_id:id,source_request:'source-'+id,
    fields:Object.fromEntries(names.map(n=>[n,n==='project_name'?'Project '+id:n==='description'?'Original work':''])),contact_known:true,notice:'Loaded',...extra})});
  const flush=()=>new Promise(resolve=>setImmediate(resolve));
  return {fields,nodes,form,pending,select,answer,flush};
}
test('selection loads full defaults, update flag and source; known contact may lack email',async()=>{
  const ui=setup();ui.select('A');assert.equal(ui.nodes.save.disabled,true);
  ui.answer(0,'A');await ui.flush();
  assert.equal(ui.fields.project_name.value,'Project A');assert.equal(ui.fields.kind.value,'update');
  assert.equal(ui.fields.update_source.value,'source-A');assert.equal(ui.fields.contact_email.required,false);
  assert.equal(ui.nodes.save.disabled,false);
});
test('late response cannot replace a newer project',async()=>{
  const ui=setup();ui.select('A');ui.select('B');ui.answer(1,'B');await ui.flush();
  ui.answer(0,'A');await ui.flush();assert.equal(ui.fields.project_name.value,'Project B');
});
test('draft editing requires inline replacement choice; keep restores selected project',async()=>{
  const ui=setup();ui.select('A');ui.answer(0,'A');await ui.flush();
  ui.fields.description.value='Unsaved changes';ui.select('B');
  assert.equal(ui.nodes.replace.hidden,false);assert.equal(ui.pending.length,1);
  ui.nodes.keep.dispatch('click');assert.equal(ui.fields.project.value,'A');
  assert.equal(ui.fields.description.value,'Unsaved changes');assert.equal(ui.nodes.save.disabled,false);
});
test('explicit replacement replaces edited draft only after acceptance',async()=>{
  const ui=setup();ui.fields.description.value='Manual draft';ui.select('B');
  assert.equal(ui.pending.length,0);ui.nodes.accept.dispatch('click');ui.answer(0,'B');await ui.flush();
  assert.equal(ui.fields.description.value,'Original work');
});
test('failed lookup keeps draft and blocks saving until kept or reloaded',async()=>{
  const ui=setup();ui.select('A');ui.pending[0].reject(new Error('offline'));await ui.flush();
  assert.equal(ui.nodes.save.disabled,true);assert.match(ui.nodes.status.textContent,/could not be loaded/);
  let prevented=false;ui.form.dispatch('submit',{preventDefault(){prevented=true;}});assert.equal(prevented,true);
  ui.nodes.keep.dispatch('click');assert.equal(ui.fields.project.value,'');assert.equal(ui.nodes.save.disabled,false);
});
test('bound validation errors and server shortcut details never auto-load',()=>{
  const ui=setup({initial:{project:'A',description:'Validation draft',kind:'update'},bound:true});
  assert.equal(ui.pending.length,0);ui.select('B');assert.equal(ui.pending.length,0);
  assert.equal(ui.fields.description.value,'Validation draft');
});
test('search filters choices while keeping selected project available',()=>{
  const ui=setup({initial:{project:'A'}});ui.nodes.search.value='boston';ui.nodes.search.dispatch('input');
  assert.equal(ui.fields.project.options[1].hidden,false);assert.equal(ui.fields.project.options[2].hidden,false);
  ui.fields.project.value='';ui.nodes.search.dispatch('input');assert.equal(ui.fields.project.options[1].hidden,true);
});
test('new request type cannot be selected for a connected project',()=>{
  const ui=setup({initial:{project:'A'}});ui.fields.kind.dispatch('change');assert.equal(ui.fields.kind.value,'update');
});
test('edits typed during a slow lookup are kept until explicit replacement',async()=>{
  const ui=setup();ui.select('A');ui.fields.description.value='Typed during loading';ui.answer(0,'A');await ui.flush();
  assert.equal(ui.fields.description.value,'Typed during loading');assert.equal(ui.nodes.save.disabled,true);
  assert.equal(ui.nodes.replace.hidden,false);
});
test('clearing a selection restores the manual draft and new request type',async()=>{
  const ui=setup();ui.fields.description.value='Original manual draft';ui.select('A');ui.nodes.accept.dispatch('click');
  ui.answer(0,'A');await ui.flush();ui.select('');await ui.flush();
  assert.equal(ui.fields.description.value,'Original manual draft');assert.equal(ui.fields.kind.value,'new');
  assert.equal(ui.fields.update_source.value,'');
});
