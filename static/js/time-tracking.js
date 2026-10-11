/* Server-owned timers. Async controls never navigate or replace unsaved edits. */
(() => {
  'use strict';
  const panel = document.querySelector('[data-time-panel]');
  if (!panel) return;
  const running = panel.querySelector('[data-running-timer]');
  const start = panel.querySelector('[data-start-timer]');
  const clock = panel.querySelector('[data-time-clock]');
  const projectLink = panel.querySelector('[data-time-project]');
  const detail = panel.querySelector('[data-time-detail]');
  const message = panel.querySelector('[data-time-message]');
  const form = panel.querySelector('[data-start-form]');
  const projectInput = form.querySelector('[name="project"]');
  const jobInput = form.querySelector('[name="work_item"]');
  function filterJobs() {
    Array.from(jobInput.options).forEach(option => {
      const unavailable = !!option.value && option.dataset.project !== projectInput.value;
      option.hidden = unavailable; option.disabled = unavailable;
    });
    if (jobInput.selectedOptions[0]?.disabled) jobInput.value = '';
  }
  projectInput.addEventListener('change', filterJobs);
  form.addEventListener('reset', () => setTimeout(filterJobs, 0));
  filterJobs();
  let current = null, observedAt = 0, busy = false, query = 0;
  function show(text, error = false) {
    message.textContent = text; message.hidden = !text;
    message.classList.toggle('error', error);
  }
  function tick() {
    if (!current?.running) return;
    const seconds = current.elapsed_seconds + Math.max(0, Math.floor((performance.now()-observedAt)/1000));
    const pad = value => String(value).padStart(2, '0');
    clock.textContent = `${Math.floor(seconds/3600)}:${pad(Math.floor(seconds/60)%60)}:${pad(seconds%60)}`;
    panel.querySelector('[data-time-review]').hidden = seconds < 43200;
  }
  function apply(data) {
    current = data; observedAt = performance.now();
    running.hidden = !data.running; start.hidden = !!data.running;
    if (data.totals) {
      document.querySelectorAll('[data-time-total]').forEach(element => {
        const value = data.totals[element.dataset.timeTotal];
        element.textContent = value == null ? 'Not set' : `${value} h`;
      });
      const budget = document.querySelector('[data-time-budget]');
      if (budget) {
        budget.hidden = data.totals.estimated == null;
        budget.className = data.totals.over_budget ? 'notice error' : 'muted';
        budget.textContent = data.totals.over_budget ? 'Recorded time exceeds the project estimate. Review the scope and any additional work.' : `${data.totals.remaining} hours remaining against the estimate.`;
      }
    }
    if (data.running) {
      projectLink.textContent = data.project; projectLink.href = data.project_url;
      detail.textContent = [data.category, data.job, data.billable ? 'Billable' : 'Nonbillable', data.description].filter(Boolean).join(' · ');
      panel.querySelectorAll('[name="entry_id"]').forEach(input => { input.value = data.id; });
      tick();
    }
  }
  async function sync() {
    if (busy || document.visibilityState === 'hidden') return;
    const ticket = ++query;
    try {
      const response = await fetch(panel.dataset.stateUrl, {credentials:'same-origin', headers:{Accept:'application/json'}, cache:'no-store'});
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error();
      const data = await response.json();
      if (ticket === query && !busy) apply(data);
    } catch (_) { if (ticket === query && !busy) show('Timer connection unavailable. Your recorded time is safe; check your connection or sign in again.', true); }
  }
  panel.querySelectorAll('[data-time-action]').forEach(actionForm => {
    actionForm.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy) return;
      busy = true; ++query;
      panel.querySelectorAll('button').forEach(button => { button.disabled = true; });
      try {
        const response = await fetch(actionForm.getAttribute('action'), {method:'POST', credentials:'same-origin',
          headers:{Accept:'application/json'}, body:new FormData(actionForm)});
        if (!response.headers.get('content-type')?.includes('application/json')) throw new Error();
        const data = await response.json(); apply(data); show(data.message, !response.ok);
        if (response.ok && actionForm === form) {
          form.reset(); form.querySelector('[name="token"]').value = crypto.randomUUID();
        }
      } catch (_) {
        show('The timer response could not be confirmed. Check its current state before trying again; do not close unsaved work.', true);
      } finally {
        busy = false;
        panel.querySelectorAll('button').forEach(button => { button.disabled = false; });
        sync();
      }
    });
  });
  document.querySelectorAll('[data-track-project]').forEach(button => {
    button.addEventListener('click', () => {
      if (current?.running) {
        show('Stop your current timer before starting another.');
      } else {
        start.open = true;
        form.querySelector('[name="project"]').value = button.dataset.trackProject;
        filterJobs();
        jobInput.value = button.dataset.trackItem;
        show('Project and job selected. Choose the work type and start the timer.');
      }
      panel.scrollIntoView({block:'center', behavior:'smooth'});
      panel.tabIndex = -1; panel.focus({preventScroll:true});
    });
  });
  const manual = document.querySelector('#time-form form');
  if (manual) {
    const values = () => JSON.stringify(Array.from(new FormData(manual).entries()));
    const baseline = values();
    let submitting = false;
    manual.addEventListener('submit', () => { submitting = true; });
    window.addEventListener('beforeunload', event => {
      if (!submitting && values() !== baseline) { event.preventDefault(); event.returnValue = ''; }
    });
  }
  setInterval(tick, 1000); setInterval(sync, 15000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') sync(); });
  sync();
})();
