(() => {
  'use strict';
  const form = document.getElementById('crm-work-entry');
  if (!form) return;
  function showProjectFields() {
    const existing = !!form.elements.project.value;
    for (const field of form.querySelectorAll('[data-new-project-field]')) {
      field.hidden = existing;
      for (const control of field.querySelectorAll('input')) control.disabled = existing;
    }
    form.elements.project_name.required = !existing;
    form.elements.project.required = form.elements.kind.value === 'update';
  }
  form.elements.project.addEventListener('change', showProjectFields);
  form.elements.kind.addEventListener('change', showProjectFields);
  showProjectFields();
})();
