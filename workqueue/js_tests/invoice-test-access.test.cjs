const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../static/js/invoice-test-access.js'),'utf8');

function run(hash,hasForm=true){
  const events=[],input={value:''},status={textContent:''};
  const form={querySelector:()=>input,requestSubmit:()=>events.push(['submit',input.value])};
  vm.runInNewContext(source,{URLSearchParams,location:{hash,pathname:'/invoice-test/access/',search:''},
    history:{replaceState:(_state,_unused,url)=>events.push(['erase',url])},
    document:{querySelector:selector=>selector==='[data-invoice-access]'?(hasForm?form:null):status}});
  return {events,input,status};
}

test('private bearer fragment is erased before native CSRF form submission',()=>{
  const raw='private-test-code-at-least-twenty-characters';
  const result=run('#token='+raw);
  assert.deepEqual(result.events,[['erase','/invoice-test/access/'],['submit',raw]]);
  assert.ok(!result.events[0][1].includes(raw));
});
test('missing or malformed private links never submit',()=>{
  for(const fragment of ['', '#token=short', '#unrelated=value', '#token='+('x'.repeat(101))]){
    const result=run(fragment);
    assert.ok(!result.events.some(event=>event[0]==='submit'));
    assert.match(result.status.textContent,/complete private link/);
  }
});
test('the access script does nothing outside its dedicated form',()=>{
  assert.deepEqual(run('#token=some-valid-long-private-code',false).events,[]);
});
