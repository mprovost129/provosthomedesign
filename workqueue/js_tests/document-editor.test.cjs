const assert=require('node:assert/strict');
const test=require('node:test');
const {lineCents,money}=require('../../static/js/document-editor.js');

test('document totals use exact cents and independent existing and renovation services',()=>{
  assert.equal(lineCents('1250','.75'),93750n);
  assert.equal(lineCents('600','.75'),45000n);
  assert.equal(money(lineCents('1250','.75')+lineCents('600','.75')),'$1,387.50');
});
test('half cent service rounding agrees with saved Decimal totals',()=>{
  assert.equal(lineCents('1.25','125.50'),15688n);
  assert.equal(lineCents('.01','.50'),1n);
  assert.equal(money(0n),'$0.00');
});
test('invalid numbers cannot look like a valid zero price',()=>{
  for(const [q,r] of [['','1'],['0','1'],['-1','2'],['1','-2'],['NaN','2'],['1','Infinity'],['1','1.001'],['1000000','1']]){
    assert.throws(()=>lineCents(q,r));
  }
  assert.equal(lineCents('1','0'),0n);
});
