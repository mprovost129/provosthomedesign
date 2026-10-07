(() => {
  const form = document.getElementById('confirm-access');
  const token = new URLSearchParams(location.hash.slice(1)).get('token');
  if (token && token.length <= 200) {
    form.elements.token.value = token;
    document.getElementById('confirm-button').disabled = false;
    document.getElementById('access-help').textContent = 'This link can be used once and expires after 24 hours.';
    history.replaceState(null, '', location.pathname);
  }
})();
