const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('static/js/client-drafts.js', 'utf8');

function setup(fetchImpl) {
  const events = {}, windowEvents = {}, timers = [];
  const fields = [
    {name:'kind', type:'radio', value:'new', checked:true},
    {name:'contact_full_name', value:'', type:'text'},
    {name:'terms_accepted', checked:true, type:'checkbox'},
    {name:'csrfmiddlewaretoken', value:'csrf', type:'hidden'},
    {name:'intake_token', value:'bound-token', type:'hidden'},
    {name:'recaptcha_token', value:'CAPTCHA', type:'hidden'},
    {name:'categories', value:'Plans or survey', checked:true, type:'checkbox'},
    {name:'upload_ids', value:'', type:'hidden'},
  ];
  for (const field of fields) fields[field.name] = field;
  const form = {dataset:{draftUrl:'/submit-work/draft/', draftRevision:'0'}, elements:fields,
    action:'https://example.invalid/submit-work/?resume=test', addEventListener:(name, callback) => events[name]=callback};
  const status = {textContent:''}, discard = {addEventListener:()=>{}};
  const context = {document:{getElementById:name => ({'client-intake':form,'draft-status':status,'discard-draft':discard}[name]), addEventListener:()=>{}},
    window:{addEventListener:(name, callback)=>windowEvents[name]=callback}, fetch:fetchImpl,
    setTimeout:callback => {timers.push(callback); return timers.length;}, clearTimeout:()=>{}};
  vm.runInNewContext(source, context);
  return {form, events, windowEvents, status, timers, flush:async()=> {const next=timers.pop();timers.length=0;if(next)await next();}};
}

test('unchanged page load never creates a blank draft; sensitive technical fields are excluded', async()=>{
  let payload, calls=0;
  const ui = setup(async(_, options)=>{calls++;payload=JSON.parse(options.body);return {ok:true,json:async()=>({revision:1})};});
  ui.events['intake-files-changed'](); await ui.flush(); assert.equal(calls,0);
  ui.form.elements.contact_full_name.value='Alex'; ui.events.input(); await ui.flush();
  assert.equal(payload.contact_full_name,'Alex'); assert.deepEqual(payload.categories,['Plans or survey']);
  assert.equal(payload.terms_accepted,undefined); assert.equal(payload.recaptcha_token,undefined);
  assert.equal(payload.csrfmiddlewaretoken,undefined); assert.equal(payload.intake_token,'bound-token');
  assert.match(ui.status.textContent,/not been submitted/);
});

test('edits while a save is in flight use the acknowledged revision for the next save', async()=>{
  const requests=[]; let resolve;
  const ui=setup((_, options)=>{requests.push(JSON.parse(options.body));return new Promise(done=>{resolve=done;});});
  ui.form.elements.contact_full_name.value='First';ui.events.input();const saving=ui.flush();
  ui.form.elements.contact_full_name.value='Second';ui.events.input();
  resolve({ok:true,json:async()=>({revision:1})});await saving;
  const second=ui.flush();assert.equal(requests[1].revision,1);assert.equal(requests[1].contact_full_name,'Second');
  resolve({ok:true,json:async()=>({revision:2})});await second;
});

test('a conflict stops autosaving instead of overwriting a newer tab', async()=>{
  let calls=0;
  const ui=setup(async()=>{calls++;return {ok:false,status:409,json:async()=>({error:'This form changed in another tab.'})};});
  ui.form.elements.contact_full_name.value='First';ui.events.input();await ui.flush();
  ui.form.elements.contact_full_name.value='Second';ui.events.input();await ui.flush();
  assert.equal(calls,1);assert.match(ui.status.textContent,/another tab/);
  let prevented=false;ui.windowEvents.beforeunload({preventDefault:()=>{prevented=true;}});assert.equal(prevented,true);
});
