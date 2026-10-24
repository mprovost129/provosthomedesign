'use strict';
(() => {
  const panel = document.querySelector('[data-test-payment-state]');
  if (!panel) return;
  const current = new URL(window.location.href);
  if (current.searchParams.get('returned') === '1') {
    current.searchParams.delete('returned');
    window.history.replaceState(null, '', current.pathname + current.search);
    const refresh = document.querySelector('form[data-test-payment-refresh]');
    if (refresh) {
      refresh.requestSubmit(refresh.querySelector('button[value="refresh"]'));
      return;
    }
  }
  const endpoint = new URL(panel.dataset.testPaymentStateUrl, window.location.origin);
  if (endpoint.origin !== window.location.origin) return;
  const expires = Date.now() + 120000;
  const timer = setInterval(async () => {
    if (Date.now() > expires) { clearInterval(timer); return; }
    if (document.hidden) return;
    try {
      const response = await fetch(endpoint.href, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok) { clearInterval(timer); return; }
      const data = await response.json();
      if (typeof data.fingerprint === 'string' && data.fingerprint !== panel.dataset.testPaymentState) {
        clearInterval(timer);
        window.location.reload();
      }
    } catch (_) { clearInterval(timer); }
  }, 5000);
})();
