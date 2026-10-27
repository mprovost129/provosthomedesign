const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const {pathToFileURL} = require('node:url');
const model = import(pathToFileURL(require('node:path').resolve('static/js/plan-review-model.mjs')));
const source = fs.readFileSync('static/js/plan-review.js','utf8').replace('await import(app.dataset.editorModel)','await loadModel()').replace('await import(app.dataset.library)','pdfLibrary');

async function setup(fetchImpl, initial={}, failures={}) {
  let doc;
  class Element {
    constructor(tag='div'){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.style={};this.attributes={};this.events={};this.value='';this.disabled=false;this.textContent='';this.classList={toggle:()=>{}};}
    append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
    replaceChildren(...nodes){this.children.forEach(node=>node.parentElement=null);this.children=[];this.append(...nodes);}
    setAttribute(key,value){this.attributes[key]=String(value);}
    cloneNode(){const node=new Element(this.tagName);node.attributes={...this.attributes};return node;}
    addEventListener(name,handler){(this.events[name] ||= []).push(handler);}
    async emit(name, values={}){const event={target:this,preventDefault(){this.defaultPrevented=true;},stopPropagation(){},pointerId:1,isPrimary:true,button:0,detail:1,...values};for(const handler of this.events[name]||[])await handler(event);return event;}
    matches(selector){return selector.split(',').some(s=>s==='[data-mark]'?this.dataset.mark!==undefined:s==='[data-mark-tool]'?this.dataset.markTool!==undefined:s==='[contenteditable="true"]'?false:s==='[name=csrfmiddlewaretoken]'?this.name==='csrfmiddlewaretoken':this.tagName===s.toUpperCase());}
    closest(selector){return this.matches(selector)?this:this.parentElement?.closest(selector);}
    querySelectorAll(selector){return this.children.flatMap(node=>[...(node.matches(selector)?[node]:[]),...node.querySelectorAll(selector)]);}
    querySelector(selector){return this.querySelectorAll(selector)[0];}
    contains(node){return node===this||this.children.some(child=>child.contains(node));}
    focus(){if(doc.activeElement===this)return;const previous=doc.activeElement;doc.activeElement=this;previous?.emit('blur');this.emit('focus');}
    scrollIntoView(){}
    getBoundingClientRect(){return {left:0,top:0,width:1000,height:1000};}
    setPointerCapture(id){this.capture=id;}
    hasPointerCapture(id){return this.capture===id;}
    releasePointerCapture(){this.capture=null;}
    getContext(){return {};}
    reportValidity(){return true;}
  }
  const ids={};const make=(id,tag='div')=>{const node=new Element(tag);node.id=id;ids[id]=node;return node;};
  const app=make('plan-review-app');const list=make('review-note-list','ol');
  for(const id of ['plan-review-data','review-form','review-summary','save-status','pdf-status','retry-plan','review-page-canvas','review-page','review-zoom','undo-mark','redo-mark','mark-mode','comment-mark','save-review','submit-review','review-version','previous-page','next-page','page-total'])app.append(make(id,id==='review-summary'?'textarea':id==='review-page'||id==='review-zoom'?'select':/mark$|review$|page$|retry-plan/.test(id)?'button':'div'));
  app.append(list);const surface=ids['review-page-canvas'];surface.parentElement.clientWidth=1028;
  for(const id of ['plan-pins','plan-shapes','plan-handles','plan-canvas'])surface.append(make(id));
  const csrf=new Element('input');csrf.name='csrfmiddlewaretoken';csrf.value='csrf';ids['review-form'].append(csrf);
  for(const kind of ['select','pin','arrow','box','freehand']){const button=new Element('button');button.dataset.markTool=kind;button.textContent=kind;app.append(button);}
  const state={editable:true,version:1,annotations:[],geometry:[{page:1,width:612,height:792,rotation:0},{page:2,width:612,height:792,rotation:0}],summary:'',save_url:'/save',submit_url:'/submit',...initial};
  ids['plan-review-data'].textContent=JSON.stringify(state);ids['review-summary'].value=state.summary;ids['review-zoom'].value='fit';
  doc={activeElement:null,getElementById:id=>ids[id]||find(app,id),createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
  function find(node,id){if(node.id===id)return node;for(const child of node.children){const found=find(child,id);if(found)return found;}}
  const timers=new Map();let nextTimer=0;const requests=[];
  const drawing={getViewport:()=>({width:612,height:792,rotation:0}),render:()=>({promise:failures.render?failures.render():Promise.resolve(),cancel(){}}),cleanup(){}};
  const library={GlobalWorkerOptions:{},getDocument:()=>({promise:Promise.resolve({numPages:2,getPage:async page=>failures.getPage?failures.getPage(page,drawing):drawing})})};
  const context={loadModel:()=>failures.model?Promise.reject(new Error('Module unavailable')):model,document:doc,pdfLibrary:library,structuredClone,crypto:require('node:crypto').webcrypto,AbortController,ResizeObserver:class{observe(){}},setTimeout:(fn,delay)=>{timers.set(++nextTimer,{fn,delay});return nextTimer;},clearTimeout:id=>timers.delete(id),window:{devicePixelRatio:1,addEventListener(){},location:{assign(url){context.redirect=url;},reload(){context.reloaded=true;}}},fetch:async(url,options)=>{const data=JSON.parse(options.body);requests.push({url,data});return fetchImpl?fetchImpl(url,data):{ok:true,json:async()=>({version:requests.length+1,redirect:'/done'})};}};
  await vm.runInNewContext(`(async()=>{${source}\n})()`,context);
  const settle=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
  return {ids,doc,app,surface,requests,context,settle,
    mode:async kind=>{await app.querySelectorAll('[data-mark-tool]').find(button=>button.dataset.markTool===kind).emit('click');},
    point:async(name,x,y,extra={})=>{await surface.emit(name,{clientX:x,clientY:y,...extra});await settle();},
    save:async()=>{await ids['save-review'].emit('click');await settle();return requests.at(-1)?.data;},
    tick:async()=>{const entry=[...timers].find(([,t])=>t.delay<20000);if(entry){timers.delete(entry[0]);await entry[1].fn();}await settle();},
    key:async(key,target=surface,extra={})=>app.emit('keydown',{key,target,...extra}),
  };
}
const box={id:'box',page:1,x:.2,y:.2,text:'original',type:'box',points:[[.2,.2],[.5,.5]]};

test('persistent drawing, canceled pointers, secondary pointers and page switches do not create stray marks',async()=>{
  const ui=await setup();await ui.mode('box');
  await ui.point('pointerdown',100,100);await ui.point('pointerup',400,400);
  await ui.point('pointerdown',200,200);await ui.point('pointerup',500,500);
  assert.equal((await ui.save()).annotations.length,2);
  await ui.point('pointerdown',200,200);await ui.point('pointermove',800,800,{pointerId:2});await ui.point('pointercancel',800,800);
  await ui.point('pointerup',800,800);
  await ui.point('pointerdown',100,300);await ui.point('pointerup',400,600);
  const afterCancel=await ui.save();assert.equal(afterCancel.annotations.length,3);
  assert.deepEqual(afterCancel.annotations[2].points,[[.1,.3],[.4,.6]]);
  await ui.point('pointerdown',300,300);await ui.ids['next-page'].emit('click');await ui.settle();await ui.point('pointerup',600,600);
  assert.equal((await ui.save()).annotations.length,3);
  assert.equal(ui.doc.activeElement,ui.surface);
});

test('moves and endpoint resize commit once; previews never leak into saves; pointer loss restores original',async()=>{
  const ui=await setup(undefined,{annotations:[box]});
  const target=()=>ui.ids['plan-pins'].children[0];
  await ui.point('pointerdown',200,200,{target:target()});await ui.point('pointermove',300,400);
  await ui.ids['review-summary'].emit('input');await ui.tick();
  assert.equal(ui.requests.at(-1).data.annotations[0].x,.2);
  await ui.point('pointerup',300,400);assert.equal((await ui.save()).annotations[0].x,.3);
  await ui.ids['undo-mark'].emit('click');assert.deepEqual((await ui.save()).annotations[0],box);
  await ui.ids['redo-mark'].emit('click');
  await ui.point('pointerdown',600,700,{target:ui.ids['plan-handles'].children[1]});await ui.point('pointerup',800,900);
  assert.deepEqual((await ui.save()).annotations[0].points,[[.3,.4],[.8,.9]]);
  await ui.point('pointerdown',300,400,{target:target()});await ui.point('pointermove',0,0);await ui.point('lostpointercapture',0,0);
  assert.deepEqual((await ui.save()).annotations[0].points,[[.3,.4],[.8,.9]]);
});

test('comment and general note edits are deliberate history entries; input shortcuts keep native text behavior',async()=>{
  const ui=await setup(undefined,{annotations:[box]});const input=ui.doc.getElementById('note-box');input.focus();input.value='comment edit';await input.emit('input');
  const native=await ui.key('z',input,{ctrlKey:true});assert.equal(native.defaultPrevented,undefined);assert.equal(input.value,'comment edit');
  await ui.ids['undo-mark'].emit('click');assert.equal((await ui.save()).annotations[0].text,'original');
  await ui.ids['redo-mark'].emit('click');assert.equal((await ui.save()).annotations[0].text,'comment edit');
  ui.ids['review-summary'].focus();ui.ids['review-summary'].value='General notes';await ui.ids['review-summary'].emit('input');await ui.ids['review-summary'].emit('blur');
  await ui.key('z',ui.surface,{ctrlKey:true});assert.equal((await ui.save()).summary,'');
  await ui.key('z',ui.surface,{metaKey:true,shiftKey:true});assert.equal((await ui.save()).summary,'General notes');
});

test('serialized autosave uses acknowledged versions and retries errors; conflicts halt further writes',async()=>{
  let resolve;const ui=await setup(()=>new Promise(done=>resolve=done));
  await ui.mode('pin');await ui.key('Enter');const saving=ui.save();await ui.settle();await ui.key('Enter');
  resolve({ok:true,json:async()=>({version:9})});await saving;await ui.settle();
  const saving2=ui.save();await ui.settle();assert.equal(ui.requests[1].data.version,9);assert.equal(ui.requests[1].data.annotations.length,2);
  resolve({ok:false,status:503,json:async()=>({error:'Retry save'})});await saving2;await ui.settle();
  const retry=ui.save();await ui.settle();resolve({ok:false,status:409,json:async()=>({error:'Conflict: copy notes'})});await retry;await ui.settle();
  await ui.key('Enter');await ui.tick();await ui.save();assert.equal(ui.requests.length,3);assert.equal(ui.ids['save-status'].textContent,'Conflict: copy notes');
});

test('submission flushes pending comments and freezes interactions; failed submit unlocks edits',async()=>{
  let release;const ui=await setup((url)=>url==='/submit'?new Promise(done=>release=done):Promise.resolve({ok:true,json:async()=>({version:4})}),{annotations:[box]});
  const input=ui.doc.getElementById('note-box');input.focus();input.value='final comment';await input.emit('input');
  const submitting=ui.ids['review-form'].emit('submit');await ui.settle();
  assert.equal(ui.requests[1].url,'/submit');assert.equal(ui.requests[1].data.annotations[0].text,'final comment');assert.equal(ui.requests[1].data.version,4);
  await ui.key('Delete');assert.equal(ui.ids['undo-mark'].disabled,true);
  release({ok:false,status:503,json:async()=>({error:'Submit failed'})});await submitting;
  assert.equal(ui.ids['undo-mark'].disabled,false);
});


test('simulated touch freehand moves as one shape and keyboard focus selects the correct mark',async()=>{
  const ui=await setup();await ui.mode('freehand');
  await ui.point('pointerdown',100,100,{pointerType:'touch'});
  await ui.point('pointermove',200,250,{pointerType:'touch'});await ui.point('pointerup',300,300,{pointerType:'touch'});
  const original=(await ui.save()).annotations[0];assert.equal(original.type,'freehand');
  await ui.mode('select');await ui.point('pointerdown',100,100,{target:ui.ids['plan-pins'].children[0],pointerType:'touch'});await ui.point('pointerup',200,200,{pointerType:'touch'});
  const moved=(await ui.save()).annotations[0];assert.equal(moved.x,.2);assert.equal(moved.y,.2);assert.equal(moved.points.length,original.points.length);
  assert.deepEqual(moved.points,original.points.map(point=>point.map(value=>Math.round((value+.1)*10000)/10000)));
  await ui.mode('pin');await ui.key('Enter');await ui.mode('select');
  ui.ids['plan-pins'].children[0].focus();await ui.key('ArrowRight');const saved=await ui.save();
  assert.equal(saved.annotations[0].x,.201);assert.equal(saved.annotations[1].x,.5);
  await ui.key('Delete');assert.equal((await ui.save()).annotations.length,1);await ui.key('z',ui.surface,{ctrlKey:true});assert.equal((await ui.save()).annotations.length,2);
});

test('reloading a draft retains marks but starts fresh history; read-only reviews reject mutations',async()=>{
  const ui=await setup(undefined,{annotations:[box]});assert.equal(ui.ids['undo-mark'].disabled,true);assert.equal(ui.ids['redo-mark'].disabled,true);
  const readonly=await setup(undefined,{annotations:[box],editable:false});await readonly.mode('pin');await readonly.point('pointerdown',100,100);await readonly.point('pointerup',200,200);await readonly.key('Delete');
  assert.equal(readonly.requests.length,0);assert.equal(readonly.ids['plan-pins'].children.length,1);
});

test('Escape leaves drawing mode from notes without consuming native text undo',async()=>{
  const ui=await setup(undefined,{annotations:[box]});
  for(const input of [ui.doc.getElementById('note-box'),ui.ids['review-summary']]) {
    await ui.mode('box');input.focus();input.value='Keep this edit';await input.emit('input');
    assert.equal((await ui.key('z',input,{ctrlKey:true})).defaultPrevented,undefined);
    assert.equal((await ui.key('Escape',input)).defaultPrevented,true);
    assert.equal(ui.app.querySelectorAll('[data-mark-tool]')[0].attributes['aria-pressed'],'true');
    assert.equal(input.value,'Keep this edit');assert.equal(ui.doc.activeElement,input);
    input.value='Continued after Escape';await input.emit('input');await input.emit('blur');
    await ui.ids['undo-mark'].emit('click');
    const savedAfterEscape=await ui.save();
    assert.equal(input===ui.ids['review-summary']?savedAfterEscape.summary:savedAfterEscape.annotations[0].text,'Keep this edit');
    await ui.point('pointerdown',700,700);await ui.point('pointerup',900,900);
    assert.equal((await ui.save()).annotations.length,1);
  }
});

test('collapsed box drags and resizes never enter saved geometry or history',async()=>{
  const ui=await setup(undefined,{annotations:[box]});await ui.mode('box');
  await ui.point('pointerdown',100,100);await ui.point('pointerup',100,500);
  assert.equal(ui.ids['plan-pins'].children.length,1);assert.equal(ui.ids['undo-mark'].disabled,true);
  await ui.mode('select');await ui.point('pointerdown',200,200,{target:ui.ids['plan-pins'].children[0]});await ui.point('pointerup',200,200);
  await ui.point('pointerdown',500,500,{target:ui.ids['plan-handles'].children[1]});await ui.point('pointerup',200,700);
  await ui.ids['review-summary'].emit('input');assert.deepEqual((await ui.save()).annotations[0],box);
  assert.equal(ui.ids['undo-mark'].disabled,true);
});

test('page fetch and render failures allow retry or navigation without edits on a stale canvas',async()=>{
  for(const stage of ['getPage','render']) {
    let broken=true;const failures=stage==='getPage'?{getPage:async(page,drawing)=>{if(broken)throw new Error('fetch');return drawing;}}:{render:()=>broken?Promise.reject(new Error('render')):Promise.resolve()};
    const ui=await setup(undefined,{annotations:[box]},failures);
    assert.equal(ui.ids['retry-plan'].hidden,false);assert.match(ui.ids['pdf-status'].textContent,/Retry/);
    assert.equal(ui.ids['plan-canvas'].hidden,true);assert.equal(ui.ids['next-page'].disabled,false);
    await ui.mode('pin');await ui.point('pointerdown',100,100);await ui.point('pointerup',100,100);
    await ui.ids['review-summary'].emit('input');assert.deepEqual((await ui.save()).annotations,[box]);
    broken=false;await ui.ids['retry-plan'].emit('click');await ui.settle();
    assert.equal(ui.ids['retry-plan'].hidden,true);assert.equal(ui.ids['plan-canvas'].hidden,false);
    assert.equal(ui.app.querySelectorAll('[data-mark-tool]')[1].disabled,false);
    await ui.point('pointerdown',100,100);await ui.point('pointerup',100,100);assert.equal((await ui.save()).annotations.length,2);
  }
});

test('obsolete page errors cannot replace a newer successful render',async()=>{
  let rejectOld;let delayed=false;const failures={getPage:async(page,drawing)=>delayed&&page===1?new Promise((resolve,reject)=>rejectOld=reject):drawing};
  const ui=await setup(undefined,{annotations:[box]},failures);delayed=true;
  const oldRender=ui.ids['review-zoom'].emit('change');await ui.settle();
  await ui.ids['next-page'].emit('click');await ui.settle();
  rejectOld(new Error('old page failed'));await oldRender;await ui.settle();
  assert.match(ui.ids['pdf-status'].textContent,/Page 2 of 2/);assert.equal(ui.ids['retry-plan'].hidden,true);
  assert.equal(ui.app.querySelectorAll('[data-mark-tool]')[1].disabled,false);
});

test('obsolete render failure cannot unlock or clear a newer pending page',async()=>{
  let rejectOld,finishNew,renderCount=0;const failures={};
  const ui=await setup(undefined,{annotations:[box]},failures);
  failures.render=()=>++renderCount===1?new Promise((resolve,reject)=>rejectOld=reject):new Promise(resolve=>finishNew=resolve);
  const oldRender=ui.ids['review-zoom'].emit('change');await ui.settle();
  await ui.ids['next-page'].emit('click');await ui.settle();rejectOld(new Error('old canvas failed'));await oldRender;await ui.settle();
  assert.equal(ui.app.querySelectorAll('[data-mark-tool]')[1].disabled,true);assert.equal(ui.ids['retry-plan'].hidden,true);
  assert.equal(ui.ids['plan-canvas'].hidden,true);
  finishNew();await ui.settle();assert.match(ui.ids['pdf-status'].textContent,/Page 2 of 2/);
  assert.equal(ui.app.querySelectorAll('[data-mark-tool]')[1].disabled,false);
});

test('helper import failure is explicit, preserves notes, blocks writes, and offers deliberate reload',async()=>{
  const ui=await setup(undefined,{annotations:[box],summary:'Saved general notes'},{model:true});
  assert.match(ui.ids['pdf-status'].textContent,/editor could not load/i);
  assert.equal(ui.ids['review-summary'].value,'Saved general notes');assert.equal(ui.ids['review-summary'].readOnly,true);
  assert.equal(ui.ids['submit-review'].disabled,true);assert.equal(ui.ids['save-review'].disabled,true);
  assert.equal((await ui.ids['review-form'].emit('submit')).defaultPrevented,true);
  assert.equal(ui.requests.length,0);assert.equal(ui.ids['retry-plan'].hidden,false);
  await ui.ids['retry-plan'].emit('click');assert.equal(ui.context.reloaded,true);
});
