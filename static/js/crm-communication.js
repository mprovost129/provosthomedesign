(() => {
  'use strict';
  const form = document.querySelector('[data-compose], [data-preferences]');
  let editor, dirty = false, submitted = false;
  const source = form?.querySelector('textarea');
  const mount = document.querySelector('[data-rich-editor]');
  if (form && source && mount && window.Quill) {
    editor = new Quill(mount, {theme: 'snow', placeholder: mount.dataset.placeholder,
      formats: ['bold', 'italic', 'underline', 'strike', 'header', 'list', 'blockquote', 'link', 'color', 'background', 'align'],
      modules: {toolbar: [['bold', 'italic', 'underline', 'strike'], [{header:[2,3,false]}],
        [{list:'ordered'}, {list:'bullet'}], ['blockquote', 'link'], [{color:[]}, {background:[]}], [{align:[]}], ['clean']]}});
    editor.setContents(editor.clipboard.convert({html: source.value}));
    source.required = false;
    source.closest('[data-rich-fallback]').hidden = true;
    editor.root.setAttribute('aria-label', form.matches('[data-compose]') ? 'Email message' : 'Email signature');
    editor.on('text-change', (_d, _o, origin) => {if (origin === 'user') dirty = true;});
  }
  if (form) {
    form.addEventListener('input', () => {dirty = true;});
    const projects = form.querySelector('[name=project_subject]'), subject = form.querySelector('[name=subject]');
    projects?.addEventListener('change', () => {
      if (projects.value) subject.value = projects.options[projects.selectedIndex].textContent.trim().slice(0,255);
      subject?.focus(); dirty = true;
    });
    form.addEventListener('submit', event => {
      if (editor) source.value = editor.getSemanticHTML();
      if (form.matches('[data-compose]') && !(editor ? editor.getText().trim() : source.value.trim())) {
        event.preventDefault(); const error = document.querySelector('[data-mail-error]');
        error.textContent = 'Write a message before sending.'; error.hidden = false; return;
      }
      submitted = true; dirty = false;
      const send = form.querySelector('button[type=submit]');
      if (send) {send.disabled = true; send.textContent = form.matches('[data-compose]') ? 'Saving email…' : 'Saving…';}
    });
    window.addEventListener('beforeunload', event => {if (dirty && !submitted) {event.preventDefault();event.returnValue='';}});
  }
  const dialog = document.querySelector('[data-email-dialog]'), frame = document.querySelector('[data-email-frame]');
  let ready = false;
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-email-url]');
    if (!button || !dialog) return;
    event.preventDefault(); ready = false; frame.src = button.dataset.emailUrl; dialog.showModal();
  });
  function askClose() {
    if (ready && frame?.contentWindow) frame.contentWindow.postMessage({type:'phd-email-request-close'}, window.location.origin);
    else dialog?.close();
  }
  document.querySelector('[data-close-dialog]')?.addEventListener('click', askClose);
  dialog?.addEventListener('cancel', event => {event.preventDefault();askClose();});
  function closeEditor() {
    if (dirty && !window.confirm('Discard this unsent email draft?')) return;
    dirty = false;
    if (window.parent !== window) window.parent.postMessage({type:'phd-email-close'}, window.location.origin);
    else if (document.referrer && new URL(document.referrer).origin === window.location.origin) window.history.back();
    else window.location.assign('/clients/');
  }
  document.querySelectorAll('[data-close-email]').forEach(button => button.addEventListener('click', closeEditor));
  window.addEventListener('message', event => {
    if (event.origin !== window.location.origin) return;
    if (frame && event.source === frame.contentWindow && event.data?.type === 'phd-email-ready') ready = true;
    if (event.source === window.parent && event.data?.type === 'phd-email-request-close') closeEditor();
    if (frame && event.source === frame.contentWindow && event.data?.type === 'phd-email-close') {
      dialog.close(); frame.removeAttribute('src');
    }
  });
  if (window.parent !== window) window.parent.postMessage({type:'phd-email-ready'}, window.location.origin);
})();
