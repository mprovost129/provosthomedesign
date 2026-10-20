(() => {
  const action = document.getElementById('id_freshbooks_action');
  if (!action) return;
  const form = action.form;
  const reuse = form.dataset.freshbooksReuse === 'yes';
  const update = () => {
    form.querySelectorAll('[data-billing-create]').forEach(el => {
      el.hidden = action.value !== 'create' || reuse;
      el.querySelectorAll('input, select').forEach(input => { input.disabled = el.hidden; });
    });
    form.querySelectorAll('[data-billing-link]').forEach(el => {
      el.hidden = action.value !== 'link' || reuse;
      el.querySelectorAll('input, select').forEach(input => { input.disabled = el.hidden; });
    });
    const amount = document.getElementById('id_freshbooks_amount');
    const pricing = document.getElementById('id_freshbooks_pricing');
    const label = form.querySelector('label[for="id_freshbooks_amount"]');
    if (label && pricing) label.textContent = pricing.value === 'hourly_rate' ? 'Hourly rate:' : 'Fixed project price:';
    if (amount) amount.required = action.value === 'create' && !reuse;
  };
  action.addEventListener('change', update);
  document.getElementById('id_freshbooks_pricing')?.addEventListener('change', update);
  update();
})();
