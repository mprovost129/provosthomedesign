(() => {
  const form = document.querySelector('[data-proposal-access]');
  if (!form) return;
  const raw = new URLSearchParams(location.hash.slice(1)).get('token');
  if (location.hash) history.replaceState(null, '', location.pathname + location.search);
  if (raw && raw.length >= 20 && raw.length <= 100) {
    form.querySelector('[name="token"]').value = raw;
    form.requestSubmit();
  } else {
    const status = document.querySelector('[data-access-status]');
    if (status) status.textContent = 'Open the complete private link from your proposal email.';
  }
})();
