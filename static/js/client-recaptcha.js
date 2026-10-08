(() => {
  'use strict';
  const config = document.getElementById('client-recaptcha-config');
  const message = 'We could not verify this request. Please try again. If it continues, contact Provost Home Design.';
  async function token(action) {
    if (config.dataset.local === 'true') return '';
    const api = config.dataset.enterprise === 'true' ? window.grecaptcha?.enterprise : window.grecaptcha;
    if (!config.dataset.siteKey || !api) throw new Error(message);
    // Generate a fresh, single-use token for each upload reservation or form POST.
    // Never reuse the upload token after a large file finishes transferring.
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(message)), 20000);
      api.ready(() => {
        Promise.resolve().then(() => api.execute(config.dataset.siteKey, {action})).then(value => {
          clearTimeout(timeout);
          if (value) resolve(value); else reject(new Error(message));
        }, () => { clearTimeout(timeout); reject(new Error(message)); });
      });
    });
  }
  window.phdRecaptcha = {token};
  for (const form of document.querySelectorAll('form[data-recaptcha-action]')) {
    let busy = false;
    const buttons = [...form.querySelectorAll('button[type="submit"],button:not([type])')];
    const error = form.querySelector('.verification-error');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (busy || !form.reportValidity()) return;
      busy = true;
      buttons.forEach(button => { button.disabled = true; });
      error.hidden = true;
      try {
        form.elements.recaptcha_token.value = await token(form.dataset.recaptchaAction);
        HTMLFormElement.prototype.submit.call(form);
      } catch (_) {
        busy = false;
        buttons.forEach(button => { button.disabled = false; });
        error.textContent = message;
        error.hidden = false;
      }
    });
    window.addEventListener('pageshow', () => {
      busy = false;
      buttons.forEach(button => { button.disabled = false; });
      form.elements.recaptcha_token.value = '';
    });
  }
})();
