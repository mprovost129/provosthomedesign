const assert = require('node:assert/strict');
const test = require('node:test');
const {pathToFileURL} = require('node:url');
const load = import(pathToFileURL(require('node:path').resolve('static/js/plan-review-model.mjs')));
const pin = {id:'pin',page:1,x:.9,y:.5,text:'Original'};
const shape = {id:'shape',page:1,x:.2,y:.3,text:'Shape',type:'box',points:[[.2,.3],[.8,.7]]};

test('moving shapes clamps the whole geometry, preserves size and synchronizes anchor', async()=>{
  const {adjustMark} = await load;
  for (const type of ['box','arrow','freehand']) {
    const original = {...shape,type};
    const moved = adjustMark(original,.5,-.5);
    assert.deepEqual(moved.points, [[.4,0],[1,.4]]);
    assert.equal(moved.x,moved.points[0][0]); assert.equal(moved.y,moved.points[0][1]);
    assert.deepEqual(original.points,shape.points);
  }
  assert.equal(adjustMark(pin,.5,0).x,1);
});
test('endpoint and box corner edits keep the other endpoint fixed and reject collapse', async()=>{
  const {adjustMark,validMark} = await load;
  assert.deepEqual(adjustMark(shape,.1,.1,'0').points, [[.3,.4],[.8,.7]]);
  assert.deepEqual(adjustMark(shape,.3,-.1,'1').points, [[.2,.3],[1,.6]]);
  assert.equal(validMark(adjustMark(shape,.6,.4,'0')),false);
  assert.equal(validMark(pin),true);
});
test('history reverses and replays adds, moves, resizes, removals, comments and summary without version rollback', async()=>{
  const {EditHistory,adjustMark} = await load;
  const history = new EditHistory();
  let current = {annotations:[],summary:'',version:17,geometry:[{page:1}]};
  const stages=[structuredClone(current)];
  function edit(change){const before=structuredClone(current);change(current);assert.equal(history.record(before,current),true);stages.push(structuredClone(current));}
  edit(s=>s.annotations.push(structuredClone(pin)));
  edit(s=>s.annotations.push(structuredClone(shape)));
  edit(s=>s.annotations[0]=adjustMark(s.annotations[0],-.2,.1));
  edit(s=>s.annotations[1]=adjustMark(s.annotations[1],.1,.1,'1'));
  edit(s=>s.annotations[1].text='Deliberate edit');
  edit(s=>s.summary='General edit');
  edit(s=>s.annotations.splice(0,1));
  current.version=99;
  for(let i=stages.length-2;i>=0;i--){current=history.apply(current,'undo');assert.deepEqual(current.annotations,stages[i].annotations);assert.equal(current.summary,stages[i].summary);assert.equal(current.version,99);}
  assert.equal(history.apply(current,'undo'),null);
  for(let i=1;i<stages.length;i++){current=history.apply(current,'redo');assert.deepEqual(current.annotations,stages[i].annotations);assert.equal(current.summary,stages[i].summary);}
});
test('no-op edits retain redo; a new edit discards redo; records are detached from live objects',async()=>{
  const {EditHistory}=await load;const history=new EditHistory();
  const before={annotations:[structuredClone(pin)],summary:''};
  const after=structuredClone(before);after.annotations[0].text='new';history.record(before,after);
  after.annotations[0].text='later';const undone=history.apply(after,'undo');
  assert.equal(history.record(undone,undone),false);assert.equal(history.redo.length,1);
  assert.equal(history.apply(undone,'redo').annotations[0].text,'new');
  const second=history.apply(after,'undo');second.summary='branch';history.record(before,second);assert.equal(history.redo.length,0);
});
