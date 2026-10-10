(() => {
  'use strict';
  const form = document.getElementById('client-intake') || document.getElementById('completion-delivery') || document.getElementById('crm-work-entry');
  if (!form) return;
  const delivery = form.id !== 'client-intake';
  const submit = document.getElementById('submit-request');
  const chooser = document.getElementById('project-files');
  const list = document.getElementById('upload-list');
  const uploadMessage = document.getElementById('upload-message');
  const csrf = form.elements.csrfmiddlewaretoken.value;
  const token = form.elements.intake_token.value;
  const ready = new Set([...list.querySelectorAll('[data-upload-id]')].map(row => row.dataset.uploadId).filter(Boolean));
  let activeUploads = 0;
  let submitting = false;
  let uploadChain = Promise.resolve();
  const newRequired = new Set(['billing_street','billing_city','billing_state','billing_zip','project_street','project_city','project_state','project_zip','service_needed','new_description']);
  const updateRequired = new Set(['update_description']);
  function syncUploads() {
    form.elements.upload_ids.value = [...ready].join(',');
    submit.disabled = submitting || activeUploads > 0 || !!list.querySelector('.upload-failed');
    if (activeUploads) uploadMessage.textContent = 'Please wait for your files to finish uploading before submitting.';
    else if (list.querySelector('.upload-failed')) uploadMessage.textContent = 'A file needs attention. Retry it or remove it before submitting.';
    else uploadMessage.textContent = ready.size ? `${ready.size} file${ready.size === 1 ? '' : 's'} ready to submit.` : '';
    updateCategories();
    form.dispatchEvent(new Event('intake-files-changed'));
  }
  function updateCategories() {
    const boxes = [...form.querySelectorAll('input[name="categories"]')];
    const required = !delivery && (form.elements.kind.value === 'update' || ready.size > 0);
    if (boxes.length) boxes[0].required = required && !boxes.some(box => box.checked);
  }
  function switchPath() {
    if (delivery) return;
    const kind = form.elements.kind.value;
    for (const [id, path, required] of [['new-fields','new',newRequired],['update-fields','update',updateRequired]]) {
      const group = document.getElementById(id);
      const enabled = kind === path;
      group.hidden = !enabled;
      group.disabled = !enabled;
      for (const input of group.querySelectorAll('input,select,textarea')) {
        input.disabled = !enabled;
        input.required = enabled && required.has(input.name);
        if (input.required) {
          const wrapper = input.closest('.field');
          if (wrapper && !wrapper.querySelector('.required')) {
            const badge = document.createElement('span'); badge.className = 'required'; badge.textContent = 'Required';
            wrapper.querySelector('label').insertAdjacentElement('afterend', badge);
          }
        }
      }
    }
    updateCategories();
  }
  function copyAddress() {
    if (!form.elements.same_address.checked) return;
    for (const suffix of ['street','city','state','zip']) form.elements[`project_${suffix}`].value = form.elements[`billing_${suffix}`].value;
  }
  form.querySelectorAll('input[name="kind"]').forEach(input => input.addEventListener('change', switchPath));
  form.querySelectorAll('input[name="categories"]').forEach(input => input.addEventListener('change', updateCategories));
  if (!delivery) form.elements.same_address.addEventListener('change', copyAddress);
  // Copy once when checked; later client edits to either address remain intact.
  function xhrUpload(url, body, onProgress, ownServer) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      if (ownServer) xhr.setRequestHeader('X-CSRFToken', csrf);
      xhr.timeout = 10 * 60 * 1000;
      xhr.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.round(event.loaded / event.total * 100)); };
      xhr.onload = () => {
        let value = {};
        if (ownServer) { try { value = JSON.parse(xhr.responseText); } catch (_) {} }
        if (xhr.status >= 200 && xhr.status < 300) resolve(value);
        else reject(new Error(value.error || 'The file could not be uploaded. Check your connection and retry.'));
      };
      xhr.onerror = () => reject(new Error('The connection was interrupted. Your form answers are still here.'));
      xhr.ontimeout = () => reject(new Error('The upload took too long. Try again or provide a secure file link.'));
      xhr.send(body);
    });
  }
  async function postOwn(url, fields) {
    const body = new FormData();
    for (const [key, value] of Object.entries(fields)) body.append(key, value);
    const response = await fetch(url, {method:'POST', headers:{'X-CSRFToken':csrf}, body, credentials:'same-origin'});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('The request could not be completed. Please try again.'); }
    if (!response.ok) throw new Error(data.error || 'The request could not be completed.');
    return data;
  }
  function uploadActionUrl(id) { return form.dataset.uploadUrl + encodeURIComponent(id) + '/'; }
  async function removeRow(row) {
    const id = row.dataset.uploadId;
    if (id) {
      try { await postOwn(uploadActionUrl(id), {intake_token:token, action:'remove'}); }
      catch (error) { uploadMessage.textContent = error.message; return; }
      ready.delete(id);
    }
    row.remove();
    syncUploads();
  }
  list.querySelectorAll('.remove-upload').forEach(button => button.addEventListener('click', () => removeRow(button.closest('li'))));
  async function uploadFile(file) {
    activeUploads += 1;
    const row = document.createElement('li');
    const left = document.createElement('div');
    const label = document.createElement('span');
    const progress = document.createElement('progress');
    const remove = document.createElement('button');
    const retry = document.createElement('button');
    retry.type = 'button'; retry.className = 'remove-upload'; retry.textContent = 'Retry'; retry.hidden = true;
    retry.addEventListener('click', async () => { retry.disabled = true; await removeRow(row); if (!row.isConnected) uploadChain = uploadChain.then(() => uploadFile(file)); else retry.disabled = false; });
    progress.max = 100; progress.value = 0; progress.setAttribute('aria-label', `Upload progress for ${file.name}`);
    label.textContent = file.name + ' — Uploading';
    left.append(label, progress); row.append(left, retry, remove); list.append(row);
    remove.type = 'button'; remove.className = 'remove-upload'; remove.textContent = 'Remove'; remove.disabled = true;
    remove.addEventListener('click', () => removeRow(row));
    syncUploads();
    try {
      if (file.size === 0 || file.size > 100 * 1024 * 1024) throw new Error('Each file must be nonempty and no larger than 100 MB.');
      let result;
      const uploadId = crypto.randomUUID();
      const recaptchaToken = await window.phdRecaptcha.token('work_upload');
      if (form.dataset.direct === 'true') {
        result = await postOwn(form.dataset.uploadUrl, {intake_token:token, name:file.name, size:file.size, upload_id:uploadId, recaptcha_token:recaptchaToken});
        row.dataset.uploadId = result.id;
        if (!result.ready) {
          const direct = new FormData();
          for (const [key, value] of Object.entries(result.policy.fields)) direct.append(key, value);
          direct.append('file', file);
          await xhrUpload(result.policy.url, direct, value => { progress.value = value; }, false);
          label.textContent = file.name + ' — Checking file';
          result = await postOwn(uploadActionUrl(result.id), {intake_token:token, action:'complete'});
        }
      } else {
        const body = new FormData(); body.append('intake_token', token); body.append('file', file); body.append('upload_id', uploadId);
        body.append('recaptcha_token', recaptchaToken);
        result = await xhrUpload(form.dataset.uploadUrl, body, value => { progress.value = value; }, true);
        row.dataset.uploadId = result.id;
      }
      ready.add(result.id);
      label.textContent = file.name + ' — Ready'; progress.remove();
    } catch (error) {
      row.classList.add('upload-failed');
      retry.hidden = false;
      label.textContent = file.name + ' — ' + error.message;
      progress.remove();
    } finally {
      remove.disabled = false;
      activeUploads -= 1;
      syncUploads();
    }
  }
  chooser.addEventListener('change', () => {
    const files = [...chooser.files];
    chooser.value = '';
    if (list.children.length + files.length > 10) { uploadMessage.textContent = 'Select no more than 10 files. Remove a file before adding more.'; return; }
    // Sequential files avoid overwhelming the connection, memory or server.
    submit.disabled = true;
    for (const file of files) uploadChain = uploadChain.then(() => uploadFile(file));
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submitting) return;
    if (activeUploads || list.querySelector('.upload-failed')) { event.preventDefault(); syncUploads(); return; }
    updateCategories();
    if (!form.reportValidity()) { event.preventDefault(); return; }
    submitting = true;
    submit.disabled = true;
    chooser.disabled = true;
    const error = form.querySelector('.verification-error');
    error.hidden = true;
    const status = document.getElementById('submission-message');
    status.textContent = 'Verifying your request…';
    try {
      if (!delivery) form.elements.recaptcha_token.value = await window.phdRecaptcha.token('work_submission');
      status.textContent = 'Saving your request…';
      form.dispatchEvent(new Event('intake-submitting'));
      HTMLFormElement.prototype.submit.call(form);
    } catch (_) {
      submitting = false;
      chooser.disabled = false;
      status.textContent = '';
      error.textContent = 'We could not verify this request. Please try again. If it continues, contact Provost Home Design.';
      error.hidden = false;
      syncUploads();
    }
  });
  window.addEventListener('pageshow', () => { submitting = false; chooser.disabled = false; form.elements.recaptcha_token.value = ''; syncUploads(); });
  switchPath(); syncUploads();
})();
