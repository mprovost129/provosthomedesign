(() => {
  'use strict';
  const form = document.getElementById('client-intake');
  const panel = document.getElementById('returning-details');
  if (!form || !panel) return;
  const KEY = 'phd.intake.contact.v1';
  const DAYS = 90 * 86400000;
  const limits = {contact_full_name:200, company:200, contact_email:254, contact_phone:50,
    billing_street:250, billing_city:100, billing_state:50, billing_zip:20};
  const fields = Object.keys(limits);
  const opt = document.getElementById('remember-intake-details');
  const status = document.getElementById('remember-details-status');
  const normalize = value => value.trim().toLowerCase();
  const profiles = JSON.parse(document.getElementById('intake-profile-data').textContent);
  let applying = false;
  function remove() {
    try { localStorage.removeItem(KEY); } catch (_) { /* Storage may be disabled. */ }
  }
  function read() {
    try {
      const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
      if (!saved) return null;
      if (saved.version !== 1 || !Number.isFinite(saved.expires) || saved.expires <= Date.now() || saved.expires > Date.now() + DAYS + 60000 ||
          !saved.values || fields.some(name => typeof saved.values[name] !== 'string' || saved.values[name].length > limits[name])) {
        remove(); return null;
      }
      return saved;
    } catch (_) { remove(); return null; }
  }
  function values() {
    return Object.fromEntries(fields.map(name => [name, form.elements[name].value.slice(0, limits[name])]));
  }
  function save() {
    if (!opt.checked || applying) return;
    const current = values();
    if (!current.contact_email.includes('@')) {
      // Do not leave another client's saved details behind while changing email.
      remove(); return;
    }
    try {
      localStorage.setItem(KEY, JSON.stringify({version:1, expires:Date.now() + DAYS, values:current}));
      status.textContent = 'Contact and billing details remembered on this device for 90 days. Review them before each submission.';
    } catch (_) {
      opt.checked = false;
      status.textContent = 'This browser could not remember your details. You can still submit normally.';
    }
  }
  function fill(data, overwrite) {
    applying = true;
    let changed = false;
    for (const name of fields) {
      const input = form.elements[name];
      if (overwrite || !input.value) {
        if (input.value !== data[name]) { input.value = data[name]; changed = true; }
      }
    }
    applying = false;
    // One draft save after all values are applied; never check terms or copy a project address.
    if (changed) form.dispatchEvent(new Event('input', {bubbles:true}));
    save();
  }
  const saved = read();
  if (saved) {
    const email = normalize(form.elements.contact_email.value);
    const identity = normalize(panel.dataset.profileEmail || '');
    const savedEmail = normalize(saved.values.contact_email);
    const sameIdentity = (!email || email === savedEmail) && (!identity || identity === savedEmail);
    if (sameIdentity) {
      opt.checked = true;
      if (panel.dataset.canRestore === 'true' && profiles.length === 0) {
        fill(saved.values, false);
        status.textContent = 'Your remembered contact and billing details are filled in. Please review them.';
      } else status.textContent = 'Details remembered on this device. Your current answers have been kept.';
    }
  }
  opt.addEventListener('change', () => {
    if (opt.checked) save();
    else { remove(); status.textContent = 'The details saved in this browser have been removed. Use Forget this device to also remove verified recognition and sign out.'; }
  });
  for (const name of fields) {
    form.elements[name].addEventListener('input', save);
    form.elements[name].addEventListener('change', save);
  }
  const apply = document.getElementById('apply-intake-profile');
  if (apply) apply.addEventListener('click', () => {
    const select = document.getElementById('intake-profile');
    const profile = profiles.find(candidate => candidate.id === select.value);
    if (!profile) { status.textContent = 'Choose your contact / company first.'; select.focus(); return; }
    fill(profile.values, true);
    status.textContent = 'Contact and billing details filled in. Review them before submitting; this does not change your saved CRM record.';
  });
  form.addEventListener('intake-submitting', save);
  document.getElementById('forget-intake-details').addEventListener('submit', () => {
    remove(); opt.checked = false;
    form.dispatchEvent(new Event('intake-submitting'));
  });
  window.addEventListener('storage', event => {
    if (event.key === KEY && !event.newValue) {
      opt.checked = false;
      status.textContent = 'Remembered details were removed in another tab.';
    }
  });
})();
