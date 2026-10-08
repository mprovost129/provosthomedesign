const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/client-recaptcha.js'), 'utf8');

function harness(execute, {valid = true, local = false, siteKey = 'test-site'} = {}) {
  const listeners = {};
  const pageshow = [];
  const error = {hidden: true};
  const button = {disabled: false};
  const form = {
    dataset: {recaptchaAction: 'work_tracking_signin'},
    elements: {recaptcha_token: {value: ''}, email: {value: 'client@example.invalid'}},
    reportValidity: () => valid,
    querySelectorAll: () => [button], querySelector: () => error,
    addEventListener: (name, callback) => { listeners[name] = callback; },
    posts: 0,
  };
  const window = {grecaptcha: {enterprise: {ready: callback => callback(), execute}},
    addEventListener: (_, callback) => pageshow.push(callback)};
  function HTMLFormElement() {}
  HTMLFormElement.prototype.submit = function () { this.posts++; };
  const document = {getElementById: () => ({dataset: {local: String(local), enterprise: 'true', siteKey}}),
    querySelectorAll: () => [form]};
  vm.runInNewContext(source, {window, document, HTMLFormElement, setTimeout, clearTimeout});
  return {window, form, button, error, pageshow, submit: () => listeners.submit({preventDefault() {}})};
}

test('uploads and final submission obtain separate fresh action-bound tokens', async () => {
  const actions = [];
  const h = harness(async (_, {action}) => { actions.push(action); return `single-use-${actions.length}`; });
  assert.equal(await h.window.phdRecaptcha.token('work_upload'), 'single-use-1');
  assert.equal(await h.window.phdRecaptcha.token('work_submission'), 'single-use-2');
  assert.deepEqual(actions, ['work_upload', 'work_submission']);
});

test('verification failure retains answers, restores controls and permits retry', async () => {
  let fail = true;
  const h = harness(async () => { if (fail) throw Error('provider unavailable'); return 'fresh-token'; });
  await h.submit();
  assert.equal(h.form.posts, 0);
  assert.equal(h.button.disabled, false);
  assert.equal(h.error.hidden, false);
  assert.equal(h.form.elements.email.value, 'client@example.invalid');
  fail = false;
  await h.submit();
  assert.equal(h.form.posts, 1);
  assert.equal(h.form.elements.recaptcha_token.value, 'fresh-token');
});

test('invalid fields do not request tokens or post forms', async () => {
  let calls = 0;
  const h = harness(async () => { calls++; return 'token'; }, {valid: false});
  await h.submit();
  assert.equal(calls, 0);
  assert.equal(h.form.posts, 0);
});

test('missing production configuration does not submit without verification', async () => {
  const h = harness(async () => 'token', {siteKey: ''});
  await h.submit();
  assert.equal(h.form.posts, 0);
  assert.equal(h.error.hidden, false);
});

test('back navigation clears used tokens and restores buttons', async () => {
  const h = harness(async () => 'used-token');
  await h.submit();
  assert.equal(h.button.disabled, true);
  h.pageshow.forEach(callback => callback());
  assert.equal(h.form.elements.recaptcha_token.value, '');
  assert.equal(h.button.disabled, false);
});
