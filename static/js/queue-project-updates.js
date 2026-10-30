/* Staff-only defaults. Selecting a project never creates or changes a record. */
(function () {
  'use strict';
  const names = ['project_name', 'project_street', 'project_city', 'project_state', 'project_zip',
    'contact_full_name', 'company', 'contact_email', 'contact_phone', 'billing_street', 'billing_city',
    'billing_state', 'billing_zip', 'service_needed', 'description', 'project_context', 'requested_deadline'];
  function attach(form, fetcher = window.fetch.bind(window)) {
    const field = name => form.elements.namedItem('new-' + name);
    const project = field('project');
    const save = form.querySelector('[data-project-save]');
    const status = form.querySelector('[data-project-status]');
    const prompt = form.querySelector('[data-project-replace]');
    const search = form.querySelector('[data-project-search]');
    const read = () => Object.fromEntries([...names, 'kind', 'update_source'].map(n => [n, field(n).value]));
    const requirements = () => Object.fromEntries(['contact_email', 'contact_phone'].map(n => [n, field(n).required]));
    let original = read(), originalRequired = requirements();
    let baseline = read(), loaded = project.value, pending = null, revision = 0;
    let bound = form.dataset.bound === '1', busy = false, failed = false;
    const setStatus = text => { status.textContent = text; };
    const edited = () => bound || names.some(n => field(n).value !== baseline[n]);
    const manualDraft = () => !loaded && names.some(n => field(n).value.trim());
    const changed = () => { form.dispatchEvent(new Event('change', {bubbles: true})); };
    function apply(values, required) {
      for (const n of [...names, 'kind', 'update_source']) field(n).value = values[n] || '';
      for (const n of ['contact_email', 'contact_phone']) field(n).required = required[n];
      baseline = read(); bound = false; changed();
    }
    async function load(id) {
      const current = ++revision;
      prompt.hidden = true; pending = null; failed = false;
      busy = Boolean(id); save.disabled = busy;
      if (!id) {
        apply({...original, kind: 'new', update_source: ''}, originalRequired);
        loaded = ''; setStatus('New manual entry.'); return;
      }
      if (!loaded) { original = read(); originalRequired = requirements(); }
      const draftAtStart = read();
      setStatus('Loading project and client details…');
      try {
        const url = new URL(form.dataset.defaultsUrl.replace('00000000-0000-0000-0000-000000000000', id), window.location.origin);
        if (url.origin !== window.location.origin) throw new Error('Unexpected address');
        const response = await fetcher(url.href, {credentials: 'same-origin', headers: {Accept: 'application/json'}, cache: 'no-store'});
        if (!response.ok) throw new Error('Lookup failed');
        const data = await response.json();
        if (current !== revision || project.value !== id) return;
        if (data.project_id !== id || !data.fields || names.some(n => typeof data.fields[n] !== 'string')) throw new Error('Invalid details');
        if (names.some(n => field(n).value !== draftAtStart[n])) {
          failed = true; pending = id; prompt.hidden = false;
          setStatus('You edited the draft while details were loading. Keep this draft, or load the selected details.');
          return;
        }
        apply({...data.fields, kind: 'update', update_source: data.source_request},
          {contact_email: !data.contact_known, contact_phone: !data.contact_known});
        loaded = id; setStatus(data.notice);
      } catch (error) {
        if (current !== revision) return;
        failed = true; setStatus('Details could not be loaded. Your draft is still here. Use “Load project details” to reload, or keep your current draft.');
        pending = id; prompt.hidden = false;
      } finally {
        if (current === revision) { busy = false; save.disabled = failed; }
      }
    }
    project.addEventListener('change', () => {
      revision++; busy = false;
      if (project.value === loaded) { prompt.hidden = true; pending = null; failed = false; save.disabled = false; return; }
      if (edited() || manualDraft()) {
        pending = project.value; prompt.hidden = false; save.disabled = true;
        setStatus('Keep this draft, or load the selected project’s details.');
      } else { load(project.value); }
    });
    form.querySelector('[data-project-accept]').addEventListener('click', () => { if (pending !== null) load(pending); });
    form.querySelector('[data-project-keep]').addEventListener('click', () => {
      revision++; project.value = loaded; pending = null; prompt.hidden = true; failed = false; busy = false;
      save.disabled = false; setStatus('Current draft kept.'); changed();
    });
    search.addEventListener('input', () => {
      const term = search.value.trim().toLocaleLowerCase();
      for (const option of project.options) option.hidden = Boolean(option.value && option.value !== project.value && !option.textContent.toLocaleLowerCase().includes(term));
    });
    field('kind').addEventListener('change', () => {
      if (project.value && field('kind').value !== 'update') {
        field('kind').value = 'update'; setStatus('An existing project is selected, so this request is an update.');
      }
    });
    form.addEventListener('submit', event => {
      if (event.submitter && event.submitter.hasAttribute('data-project-load')) return;
      if (busy || failed || pending !== null || project.value !== loaded) {
        event.preventDefault(); setStatus('Load the selected details or keep your current draft before saving.');
      }
    });
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {attach};
  if (typeof document !== 'undefined') document.querySelectorAll('[data-project-update]').forEach(form => attach(form));
}());
