const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('static/js/client-intake-memory.js', 'utf8');
const KEY = 'phd.intake.contact.v1';
const contact = {contact_full_name:'Alex', company:'Homeowner', contact_email:'alex@example.invalid', contact_phone:'5550100',
  billing_street:'12 Billing Lane', billing_city:'Town', billing_state:'MA', billing_zip:'02769'};

function setup({saved, initial={}, restore=true, identity='', profiles=[], failStorage=false}={}) {
  const events = {}, dispatched = [], store = new Map(), win = {};
  if (saved) store.set(KEY, typeof saved === 'string' ? saved : JSON.stringify(saved));
  const field = value => ({value, addEventListener:(name, fn)=>{ events[`${value}:${name}`]=fn; }});
  const elements = Object.fromEntries(Object.keys(contact).map(name=>[name,field(initial[name] || '')]));
  elements.project_street = field('Never replace project address');
  elements.new_description = field('Keep these project instructions');
  elements.terms_accepted = {checked:false};
  for (const [name,input] of Object.entries(elements)) input.addEventListener=(type,fn)=>{events[`${name}:${type}`]=fn;};
  const form = {elements, addEventListener:(name,fn)=>events[`form:${name}`]=fn,
    dispatchEvent:event=>{dispatched.push(event.type);events[`form:${event.type}`]?.(event);}};
  const opt = {checked:false, addEventListener:(_,fn)=>events.opt=fn};
  const status = {textContent:''}, select = {value:'',focus:()=>{}};
  const nodes = {'client-intake':form, 'returning-details':{dataset:{canRestore:String(restore),profileEmail:identity}},
    'remember-intake-details':opt, 'remember-details-status':status,
    'intake-profile-data':{textContent:JSON.stringify(profiles)}, 'intake-profile':select,
    'apply-intake-profile':profiles.length?{addEventListener:(_,fn)=>events.apply=fn}:null,
    'forget-intake-details':{addEventListener:(_,fn)=>events.forget=fn}};
  vm.runInNewContext(source, {document:{getElementById:id=>nodes[id]}, window:{addEventListener:(name,fn)=>win[name]=fn},
    localStorage:{getItem:key=>store.get(key)||null, removeItem:key=>store.delete(key),
      setItem:(key,value)=>{if(failStorage)throw new Error('blocked');store.set(key,value);}},
    Event:class{constructor(type){this.type=type;}}, Date, console});
  return {events,dispatched,store,elements,opt,status,select,win};
}
const remembered = values=>({version:1,expires:Date.now()+86400000,values:{...contact,...values}});

test('opted-in browser fills only contact and billing, leaving project and terms untouched',()=>{
  const ui=setup({saved:remembered()});
  assert.equal(ui.opt.checked,true);
  assert.equal(ui.elements.billing_street.value,contact.billing_street);
  assert.equal(ui.elements.project_street.value,'Never replace project address');
  assert.equal(ui.elements.terms_accepted.checked,false);
  assert.deepEqual(ui.dispatched,['input']);
  assert.match(ui.status.textContent,/review/);
});

test('no storage without explicit opt-in; save whitelist excludes project and terms',()=>{
  const ui=setup({initial:contact});
  ui.events['contact_phone:input']();assert.equal(ui.store.size,0);
  ui.opt.checked=true;ui.events.opt();
  const saved=JSON.parse(ui.store.get(KEY));
  assert.deepEqual(saved.values,contact);assert.equal(saved.version,1);
  ui.elements.contact_phone.value='UPDATED';ui.events['contact_phone:input']();
  assert.equal(JSON.parse(ui.store.get(KEY)).values.contact_phone,'UPDATED');
  ui.opt.checked=false;ui.events.opt();assert.equal(ui.store.size,0);
});

test('restored drafts and failed POST answers are not changed by local memory',()=>{
  const ui=setup({saved:remembered(),restore:false,initial:{contact_email:contact.contact_email,contact_full_name:'Draft name'}});
  assert.equal(ui.elements.contact_full_name.value,'Draft name');
  assert.equal(ui.elements.billing_street.value,'');assert.equal(ui.dispatched.length,0);
});

test('different verified identity or entered email cannot inherit previous client details',()=>{
  for(const options of [{identity:'different@example.invalid'},{initial:{contact_email:'different@example.invalid'}}]){
    const ui=setup({saved:remembered(),...options});
    assert.equal(ui.opt.checked,false);assert.equal(ui.elements.billing_street.value,'');
  }
});

test('CRM profiles require explicit choice, and filling leaves work intact',()=>{
  const profiles=[{id:'one',values:contact},{id:'two',values:{...contact,company:'Second Company',billing_street:'Second Billing'}}];
  const ui=setup({profiles,identity:contact.contact_email,saved:remembered()});
  assert.equal(ui.elements.billing_street.value,'');
  ui.events.apply();assert.match(ui.status.textContent,/Choose/);
  ui.select.value='two';ui.events.apply();
  assert.equal(ui.elements.company.value,'Second Company');assert.equal(ui.elements.billing_street.value,'Second Billing');
  assert.equal(ui.elements.new_description.value,'Keep these project instructions');
  assert.equal(ui.elements.terms_accepted.checked,false);
});

test('expired, malformed or oversized saved values are discarded',()=>{
  for(const saved of [{...remembered(),expires:0},'{bad json',remembered({company:'x'.repeat(201)}),{...remembered(),version:99}]){
    const ui=setup({saved});assert.equal(ui.store.size,0);assert.equal(ui.opt.checked,false);
    assert.equal(ui.elements.contact_full_name.value,'');
  }
});

test('storage failures preserve normal input; forgetting and cross-tab removal disable saving',()=>{
  const blocked=setup({initial:contact,failStorage:true});blocked.opt.checked=true;blocked.events.opt();
  assert.equal(blocked.opt.checked,false);assert.equal(blocked.elements.contact_full_name.value,'Alex');
  assert.match(blocked.status.textContent,/could not remember/);
  const ui=setup({saved:remembered()});ui.events.forget();
  assert.equal(ui.store.size,0);assert.equal(ui.opt.checked,false);
  assert.equal(ui.dispatched.at(-1),'intake-submitting');
  ui.opt.checked=true;ui.win.storage({key:KEY,newValue:null});assert.equal(ui.opt.checked,false);
});
