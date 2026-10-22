const test=require('node:test');
const assert=require('node:assert/strict');
const {workingTotal,hundredths,dollars}=require('../../static/js/pricing.js');
const rates={new:'1.25',attached:'.75',detached:'1.25',addition:'1.25',existing:'.75',renovation:'.75',hourly:'125'};
test('living and attached garage use their separate rates',()=>{
  assert.equal(workingTotal({mode:'area',new_area:'2000',attached_area:'500'},rates),287500n);
});
test('existing drawings and renovation are added independently',()=>{
  assert.equal(workingTotal({mode:'area',existing_area:'2000',renovation_area:'600'},rates),195000n);
});
test('detached upper floor is added exactly once at its own garage rate',()=>{
  assert.equal(workingTotal({mode:'area',detached_area:'600',detached_above_area:'400'},rates),125000n);
});
test('round each service half up in cents before summing',()=>{
  assert.equal(workingTotal({mode:'area',existing_area:'.02',renovation_area:'.02'},rates),4n);
  assert.equal(hundredths('.75'),75n);
});
test('hourly mode excludes retained area values and fixed-fee adjustments',()=>{
  assert.equal(workingTotal({mode:'hourly',hours:'12.5',new_area:'2000',custom_fee:'50',override_total:'1'},rates),156250n);
});
test('explicit zero override and custom fee behave consistently',()=>{
  assert.equal(workingTotal({mode:'area',new_area:'100',custom_fee:'50'},rates),17500n);
  assert.equal(workingTotal({mode:'area',new_area:'100',override_total:'0'},rates),0n);
  assert.equal(dollars(287500n),'$2,875.00 USD');
});
test('invalid or absent quantities never produce a misleading amount',()=>{
  assert.equal(workingTotal({mode:'area'},rates),null);
  for(const value of ['NaN','Infinity','-1','1.001','1e4'])assert.throws(()=>hundredths(value));
});
