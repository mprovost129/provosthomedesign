const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../static/js/client-test-payment.js'), 'utf8');

function preview({returned=false, endpoint='/invoice-tests/example/state/', hidden=false, result='original', failure=false}={}) {
  const calls={submitted:0,reloads:0,cleared:0,fetch:[],history:[],timer:null};
  const button={name:'action',value:'refresh'};
  const form={querySelector:()=>button,requestSubmit(value){assert.equal(value,button);calls.submitted++;}};
  const panel={dataset:{testPaymentState:'original',testPaymentStateUrl:endpoint}};
  const document={hidden,querySelector(selector){return selector.startsWith('[data-')?panel:form;}};
  const window={location:{href:'https://example.test/invoice-tests/example/payments/example/'+(returned?'?returned=1':''),origin:'https://example.test',reload(){calls.reloads++;}},history:{replaceState(...args){calls.history.push(args);}}};
  vm.runInNewContext(code,{document,window,URL,Date,setInterval(fn){calls.timer=fn;return 1;},clearInterval(){calls.cleared++;},
    async fetch(url,options){calls.fetch.push({url,options});if(failure)throw new Error('offline');return {ok:true,json:async()=>({fingerprint:result})};}});
  return calls;
}

test('Stripe return requests one native CSRF form submission and removes the return marker',()=>{
  const calls=preview({returned:true});assert.equal(calls.submitted,1);assert.equal(calls.timer,null);
  assert.equal(calls.history[0][2],'/invoice-tests/example/payments/example/');
});
test('polling reads only same-origin state and reloads only on a changed result',async()=>{
  const same=preview();await same.timer();assert.equal(same.reloads,0);assert.equal(same.submitted,0);
  assert.equal(same.fetch[0].options.credentials,'same-origin');assert.equal(same.fetch[0].options.cache,'no-store');
  const changed=preview({result:'changed'});await changed.timer();assert.equal(changed.reloads,1);assert.equal(changed.cleared,1);
});
test('an external polling address is rejected',()=>{
  const calls=preview({endpoint:'https://other.example/state/'});assert.equal(calls.timer,null);assert.equal(calls.fetch.length,0);
});
test('hidden pages do not poll',async()=>{
  const calls=preview({hidden:true});await calls.timer();assert.equal(calls.fetch.length,0);
});
test('network errors stop polling without retrying payment creation',async()=>{
  const calls=preview({failure:true});await calls.timer();assert.equal(calls.cleared,1);assert.equal(calls.submitted,0);assert.equal(calls.reloads,0);
});
