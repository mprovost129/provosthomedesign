(() => {
  'use strict';
  const form = document.getElementById('client-intake');
  if (!form || !form.dataset.draftUrl) return;
  const status = document.getElementById('draft-status');
  const discard = document.getElementById('discard-draft');
  const excluded = new Set(['csrfmiddlewaretoken', 'intake_token', 'website', 'recaptcha_token', 'terms_accepted']);
  let revision = Number(form.dataset.draftRevision || 0);
  let generation = 0, savedGeneration = 0, busy = false, stopped = false, submitting = false, timer;
  let lastSeen;
  function answers() {
    const result = {intake_token: form.elements.intake_token.value, revision};
    for (const element of form.elements) {
      const name = element.name;
      if (!name || excluded.has(name) || element.type === 'file') continue;
      if (element.type === 'radio') { if (element.checked) result[name] = element.value; }
      else if (element.type === 'checkbox' && name === 'categories') {
        if (!result[name]) result[name] = [];
        if (element.checked) result[name].push(element.value);
      } else if (element.type === 'checkbox') result[name] = element.checked;
      else result[name] = element.value;
    }
    return result;
  }
  async function save() {
    if (busy || stopped || submitting || generation === savedGeneration) return;
    busy = true;
    const sendingGeneration = generation;
    status.textContent = 'Saving your unfinished form…';
    try {
      const response = await fetch(form.dataset.draftUrl, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value},
        body: JSON.stringify(answers())});
      const data = await response.json();
      if (!response.ok) {
        if (response.status === 409) stopped = true;
        throw new Error(data.error || 'Your latest changes have not been saved.');
      }
      revision = data.revision;
      savedGeneration = sendingGeneration;
      status.textContent = 'Draft saved in this browser. It has not been submitted.';
    } catch (error) {
      status.textContent = 'Your latest changes may not be saved. Keep this page open. ' + error.message;
    } finally {
      busy = false;
      if (!stopped && !submitting && generation !== savedGeneration && savedGeneration === sendingGeneration) {
        clearTimeout(timer); timer = setTimeout(save, 1200);
      }
    }
  }
  function changed() {
    if (submitting) return;
    const next = JSON.stringify({...answers(), revision: 0});
    if (next === lastSeen) return;
    lastSeen = next;
    generation += 1;
    if (!stopped) status.textContent = 'Changes waiting to save…';
    clearTimeout(timer); timer = setTimeout(save, 1200);
  }
  lastSeen = JSON.stringify({...answers(), revision: 0});
  form.addEventListener('input', changed);
  form.addEventListener('change', changed);
  form.addEventListener('intake-files-changed', changed);
  form.addEventListener('intake-submitting', () => { submitting = true; clearTimeout(timer); });
  window.addEventListener('pageshow', () => { submitting = false; });
  window.addEventListener('online', save);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') save(); });
  window.addEventListener('beforeunload', event => {
    if (!submitting && generation !== savedGeneration) { event.preventDefault(); event.returnValue = ''; }
  });
  discard.addEventListener('click', async () => {
    if (!window.confirm('Discard the saved answers in this form and start fresh? This does not remove submitted work.')) return;
    stopped = true; clearTimeout(timer); discard.disabled = true;
    // A save already in flight must finish before discarding the same draft.
    while (busy) await new Promise(resolve => setTimeout(resolve, 100));
    try {
      const response = await fetch(form.dataset.draftUrl, {method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value},
        body: JSON.stringify({action: 'discard', intake_token: form.elements.intake_token.value})});
      if (!response.ok) throw new Error();
      savedGeneration = generation;
      window.location.assign(form.action.split('?')[0]);
    } catch (_) { status.textContent = 'The saved form could not be discarded. Keep this page open and try again.'; discard.disabled = false; }
  });
})();
