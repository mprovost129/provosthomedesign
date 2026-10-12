(() => {
  'use strict';
  const button = document.getElementById('copy-project-link');
  if (!button) return;
  button.addEventListener('click', async () => {
    const input = document.getElementById('project-link');
    const status = document.getElementById('copy-link-status');
    try {
      await navigator.clipboard.writeText(input.value);
      status.textContent = 'Project link copied.';
    } catch (_) {
      input.focus();
      input.select();
      status.textContent = 'Select and copy the project link above.';
    }
  });
})();
