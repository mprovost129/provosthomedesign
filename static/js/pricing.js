(function () {
  'use strict';
  const keys = ['new', 'attached', 'detached', 'addition', 'existing', 'renovation'];
  function hundredths(value) {
    const text = String(value == null ? '' : value).trim();
    if (!text) return 0n;
    if (!/^(?:\d{1,9}(?:\.\d{0,2})?|\.\d{1,2})$/.test(text)) throw new Error('Check the entered numbers.');
    const parts = text.split('.');
    return BigInt(parts[0] || '0') * 100n + BigInt(((parts[1] || '') + '00').slice(0, 2));
  }
  function workingTotal(values, rates) {
    let subtotal = 0n, count = 0;
    function add(key, quantity) {
      if (!quantity) return;
      const raw = values[key + '_rate'];
      const rate = hundredths(raw === '' || raw == null ? rates[key] : raw);
      subtotal += (quantity * rate + 50n) / 100n;
      count++;
    }
    if (values.mode === 'hourly') add('hourly', hundredths(values.hours));
    else {
      keys.forEach(function (key) {
        let quantity = hundredths(values[key + '_area']);
        if (key === 'detached') quantity += hundredths(values.detached_above_area);
        add(key, quantity);
      });
      subtotal += hundredths(values.custom_fee);
    }
    if (!count) return null;
    return values.mode !== 'hourly' && values.override_total !== '' && values.override_total != null
      ? hundredths(values.override_total) : subtotal;
  }
  function dollars(cents) {
    return '$' + (cents / 100n).toLocaleString('en-US') + '.' + String(cents % 100n).padStart(2, '0') + ' USD';
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {hundredths:hundredths, workingTotal:workingTotal, dollars:dollars};
    return;
  }
  const form = document.querySelector('[data-pricing-editor]');
  const configElement = document.getElementById('pricing-config');
  if (!form || !configElement) return;
  const config = JSON.parse(configElement.textContent);
  const field = function (name) { return form.elements.namedItem(name); };
  const category = field('category'), client = field('client'), card = field('rate_version');
  const mode = field('mode'), total = form.querySelector('[data-pricing-total]');
  const categoryMessage = form.querySelector('[data-pricing-category-message]');
  let submitting = false;
  const baseline = new URLSearchParams(new FormData(form)).toString();
  function render() {
    const hourly = mode.value === 'hourly';
    form.querySelector('[data-pricing-area]').hidden = hourly;
    form.querySelector('[data-pricing-hourly]').hidden = !hourly;
    const unclassified = client.value && !config.clients[client.value];
    const mismatch = client.value && config.clients[client.value] !== category.value;
    const selected = config.cards[card.value];
    Array.from(card.options).forEach(function (option) {
      option.disabled = option.value && config.cards[option.value].category !== category.value;
    });
    if (unclassified) categoryMessage.textContent = 'This client is not classified. Open their client record and select Homeowner, Contractor or Other before saving a calculation for them.';
    else if (mismatch) categoryMessage.textContent = 'The selected category does not match this client. Choose their matching category and review its rates, or update the client record first.';
    else if (!selected || selected.category !== category.value) categoryMessage.textContent = 'No matching rate card is selected. This category needs its own approved rates; another category is never substituted.';
    else categoryMessage.textContent = 'Using ' + category.options[category.selectedIndex].text + ' rates. Rate-card version ' + selected.sequence + '. Saved calculations keep their own rates.';
    form.querySelector('button[value="save"]').disabled = Boolean(unclassified || mismatch || !selected || selected.category !== category.value);
    if (!selected || selected.category !== category.value) { total.textContent = 'Select configured rates'; return; }
    try {
      const values = Object.fromEntries(new FormData(form));
      const cents = workingTotal(values, selected.rates);
      total.textContent = cents == null ? 'Enter a positive area or estimated hours' : dollars(cents);
    } catch (_) { total.textContent = 'Check the entered numbers'; }
  }
  function loadRates() {
    const selected = config.cards[card.value];
    keys.concat(['hourly']).forEach(function (key) { field(key + '_rate').value = selected ? selected.rates[key] : ''; });
    field('rate_reason').value = '';
    render();
  }
  category.addEventListener('change', function () { card.value = config.latest[category.value] || ''; loadRates(); });
  client.addEventListener('change', function () {
    if (client.value) { category.value = config.clients[client.value] || ''; card.value = config.latest[category.value] || ''; loadRates(); }
    render();
  });
  card.addEventListener('change', loadRates);
  form.addEventListener('input', render);
  form.addEventListener('change', render);
  form.addEventListener('submit', function () { submitting = true; });
  window.addEventListener('pageshow', function () { submitting = false; });
  window.addEventListener('beforeunload', function (event) {
    if (!submitting && new URLSearchParams(new FormData(form)).toString() !== baseline) {
      event.preventDefault(); event.returnValue = '';
    }
  });
  render();
})();
