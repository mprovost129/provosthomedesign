(() => {
  const form = document.querySelector('[data-proposal-editor]');
  if (!form) return;
  const list = form.querySelector('[data-line-list]');
  const count = form.querySelector('[name="lines-TOTAL_FORMS"]');
  let dirty = false;
  const toggleDeleted = () => list.querySelectorAll('[data-line]').forEach(line => {
    const deleted = line.querySelector('[name$="-DELETE"]')?.checked || false;
    line.querySelectorAll('input:not([type=checkbox]), textarea').forEach(field => { field.disabled = deleted; });
  });
  const total = () => {
    let sum = 0;
    list.querySelectorAll('[data-line]').forEach(line => {
      if (line.querySelector('[name$="-DELETE"]')?.checked) return;
      const quantity = Number(line.querySelector('[name$="-quantity"]').value || 0);
      const rate = Number(line.querySelector('[name$="-rate"]').value || 0);
      if (Number.isFinite(quantity) && Number.isFinite(rate)) sum += Math.round((quantity * rate + Number.EPSILON) * 100) / 100;
    });
    form.querySelector('[data-proposal-total]').textContent = sum.toLocaleString('en-US', {style:'currency', currency:'USD'});
  };
  form.querySelector('[data-add-line]').addEventListener('click', () => {
    const index = Number(count.value);
    if (index >= 20) { alert('Use up to 20 service lines per proposal.'); return; }
    const template = form.querySelector('[data-empty-line]').content.cloneNode(true);
    template.querySelectorAll('[name]').forEach(field => { field.name = field.name.replaceAll('__prefix__', String(index)); field.id = field.id.replaceAll('__prefix__', String(index)); });
    template.querySelectorAll('label[for]').forEach(label => { label.htmlFor = label.htmlFor.replaceAll('__prefix__', String(index)); });
    template.querySelector('h3').textContent = `Service ${index+1}`;
    list.append(template);
    count.value = String(index+1);
    dirty = true;
  });
  form.addEventListener('input', () => { dirty = true; total(); });
  form.addEventListener('change', () => { dirty = true; toggleDeleted(); total(); });
  form.addEventListener('submit', () => { dirty = false; });
  window.addEventListener('beforeunload', event => { if (dirty) { event.preventDefault(); event.returnValue = ''; } });
  toggleDeleted(); total();
})();
