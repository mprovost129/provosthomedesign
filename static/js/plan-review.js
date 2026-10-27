/* Version-bound pins, bounded page rendering, serialized autosave. No document data leaves this origin. */
const app = document.getElementById('plan-review-app');
if (app) {
  const {adjustMark, validMark, EditHistory} = await import(app.dataset.editorModel);
  const state = JSON.parse(document.getElementById('plan-review-data').textContent);
  const form = document.getElementById('review-form');
  const summary = document.getElementById('review-summary');
  const status = document.getElementById('save-status');
  const pdfStatus = document.getElementById('pdf-status');
  const list = document.getElementById('review-note-list');
  const pins = document.getElementById('plan-pins');
  const surface = document.getElementById('review-page-canvas');
  const canvas = document.getElementById('plan-canvas');
  const pageSelect = document.getElementById('review-page');
  const zoom = document.getElementById('review-zoom');
  const tools = [...app.querySelectorAll('[data-mark-tool]')];
  const modeStatus = document.getElementById('mark-mode');
  const commentButton = document.getElementById('comment-mark');
  const undo = document.getElementById('undo-mark');
  const shapes = document.getElementById('plan-shapes');
  const redo = document.getElementById('redo-mark');
  const handles = document.getElementById('plan-handles');
  const history = new EditHistory();
  let stroke = null, gesture = null, textEdit = null, rendering = true;
  let mode = 'select';
  const saveButton = document.getElementById('save-review');
  const submitButton = document.getElementById('submit-review');
  let page = 1, pdf = null, selected = '';
  let revision = 0, savedRevision = 0, savePromise = null, timer = null, blocked = false, submitting = false;
  let renderTask = null, renderGeneration = 0;
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const showStatus = (text, error = false) => { status.textContent = text; status.classList.toggle('error', error); };
  const markDirty = () => {
    if (!state.editable) return;
    revision++;
    if (!blocked) showStatus('Unsaved changes. Saving your draft…');
    clearTimeout(timer);
    if (!blocked && !submitting) timer = setTimeout(() => saveDraft().catch(() => {}), 1200);
  };
  const payload = () => ({version: state.version, summary: summary.value,
    annotations: structuredClone(state.annotations), geometry: structuredClone(state.geometry)});
  async function post(url, data) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {method: 'POST', credentials: 'same-origin', signal: controller.signal,
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf}, body: JSON.stringify(data)});
      let body;
      try { body = await response.json(); } catch { throw new Error('Your session may have expired. Keep this tab and your notes; sign in again in another tab, then retry.'); }
      if (!response.ok) {
        if (response.status === 409) blocked = true;
        throw new Error(body.error || 'Could not save. Keep this tab open and retry.');
      }
      return body;
    } finally { clearTimeout(timeout); }
  }
  async function saveDraft() {
    if (!state.editable || blocked) throw new Error('Reload after copying your notes to resolve the draft conflict.');
    if (savePromise) return savePromise;
    if (revision === savedRevision) { showStatus('Draft saved. Not submitted yet.'); return; }
    const snapshot = revision;
    showStatus('Saving your draft…');
    let success = false;
    savePromise = post(state.save_url, payload()).then(result => {
      success = true;
      state.version = result.version;
      document.getElementById('review-version').value = state.version;
      savedRevision = snapshot;
      showStatus(revision === savedRevision ? 'Draft saved. Not submitted yet.' : 'Saving newer changes…');
    }).catch(error => {
      showStatus(error.name === 'AbortError' ? 'Saving timed out and may have succeeded. Keep your notes before reloading; do not close this tab.' : error.message, true);
      throw error;
    }).finally(() => {
      savePromise = null;
      if (success && revision !== savedRevision && !blocked && !submitting) {
        clearTimeout(timer); timer = setTimeout(() => saveDraft().catch(() => {}), 300);
      }
    });
    return savePromise;
  }
  async function flush() {
    clearTimeout(timer);
    while (revision !== savedRevision) await saveDraft();
  }
  const draft = () => ({annotations: structuredClone(state.annotations), summary: summary.value});
  const editable = () => state.editable && !submitting;
  const interactive = () => editable() && pdf && !rendering;
  function updateControls() {
    const pendingText = textEdit && JSON.stringify(textEdit) !== JSON.stringify(draft());
    if (undo) undo.disabled = !editable() || (!history.undo.length && !pendingText);
    if (redo) redo.disabled = !editable() || !history.redo.length || pendingText;
    tools.forEach(button => {
      button.disabled = !interactive();
      button.setAttribute('aria-pressed', String(button.dataset.markTool === mode));
    });
    if (commentButton) commentButton.disabled = !editable() || !state.annotations.some(mark => mark.id === selected);
  }
  function commitText() {
    if (!textEdit) return;
    history.record(textEdit, draft()); textEdit = null; updateControls(); drawPins();
  }
  function finishEdit(before) {
    if (history.record(before, draft())) markDirty();
    updateControls(); buildNotes(); drawPins();
  }
  function cancelGesture() {
    const pointerId = gesture?.pointerId;
    gesture = null; stroke = null;
    if (pointerId !== undefined && surface.hasPointerCapture(pointerId)) surface.releasePointerCapture(pointerId);
    drawPins();
  }
  function setMode(value) {
    commitText(); cancelGesture(); mode = value;
    surface.classList.toggle('placing', value !== 'select');
    surface.classList.toggle('drawing', value !== 'select');
    if (modeStatus) modeStatus.textContent = value === 'select'
      ? 'Select: tap a mark, drag it to move, or use its handles to adjust. Scroll on empty plan space.'
      : `${tools.find(button => button.dataset.markTool === value)?.textContent}: ${value === 'pin' ? 'tap to place a note (or press Enter on the plan).' : 'drag on the plan to draw.'} This tool stays active. Choose Select or press Esc when finished.`;
    updateControls(); drawPins();
  }
  function highlight(id) {
    selected = id;
    for (const button of pins.children) {
      button.classList.toggle('selected', button.dataset.pin === id);
      button.setAttribute('aria-pressed', String(button.dataset.pin === id));
    }
    for (const entry of list.children) entry.classList.toggle('selected', entry.dataset.pin === id);
    updateControls();
  }
  function openComment(id) {
    commitText(); highlight(id); drawPins();
    const target = document.getElementById(`note-${id}`);
    if (target) { target.focus({preventScroll: true}); target.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }
  }
  function drawPins() {
    if (!pins || !shapes || !handles) return;
    const active = document.activeElement;
    const focus = active?.closest('[data-mark]');
    const focusKey = focus && surface.contains(focus) ? {id: focus.dataset.mark, handle: focus.dataset.handle} : null;
    pins.replaceChildren(); shapes.replaceChildren(); handles.replaceChildren();
    if (rendering) return;
    const visible = state.annotations.map(mark => gesture?.preview?.id === mark.id ? gesture.preview : mark);
    visible.forEach(mark => { if (mark.page === page && mark.points) drawShape(mark); });
    if (stroke?.points) drawShape(stroke, true);
    visible.forEach((mark, index) => {
      if (mark.page !== page) return;
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'plan-pin'; button.textContent = index + 1;
      button.dataset.pin = mark.id; button.dataset.mark = mark.id;
      if (mark.id === selected && mode === 'select' && ['box', 'arrow'].includes(mark.type) && editable()) button.className += ' has-handles';
      button.style.left = `${mark.x * 100}%`; button.style.top = `${mark.y * 100}%`;
      button.setAttribute('aria-label', `Note ${index + 1}, page ${mark.page}: ${mark.text || 'Add a comment'}. ${state.editable ? 'Select to adjust; Enter to edit comment.' : 'Enter to read comment.'}`);
      button.addEventListener('focus', () => {
        if (selected !== mark.id) { highlight(mark.id); drawPins(); }
      });
      button.addEventListener('click', event => {
        event.stopPropagation(); highlight(mark.id);
        if (event.detail === 0 || !state.editable) openComment(mark.id);
        else drawPins();
      });
      pins.append(button);
      if (mark.id === selected && mark.points?.length === 2 && ['box', 'arrow'].includes(mark.type) && mode === 'select' && editable()) {
        mark.points.forEach((point, endpoint) => {
          const handle = document.createElement('button'); handle.type = 'button'; handle.className = 'plan-handle';
          handle.dataset.mark = mark.id; handle.dataset.handle = String(endpoint);
          handle.style.left = `${point[0] * 100}%`; handle.style.top = `${point[1] * 100}%`;
          handle.setAttribute('aria-label', `Note ${index + 1}, ${mark.type === 'box' ? 'corner' : 'endpoint'} ${endpoint + 1}. Drag or use arrow keys to adjust.`);
          handles.append(handle);
        });
      }
    });
    highlight(selected);
    if (focusKey) {
      const target = [...pins.children, ...handles.children].find(node =>
        node.dataset.mark === focusKey.id && node.dataset.handle === focusKey.handle);
      (target || surface).focus({preventScroll: true});
    }
  }
  function removeMark(id) {
    if (!editable()) return;
    commitText(); cancelGesture(); const before = draft();
    state.annotations = state.annotations.filter(mark => mark.id !== id);
    if (selected === id) selected = '';
    finishEdit(before);
  }
  function buildNotes() {
    list.replaceChildren();
    state.annotations.forEach((pin, index) => {
      const entry = document.createElement('li'); entry.dataset.pin = pin.id;
      const heading = document.createElement('div'); heading.className = 'review-note-heading';
      const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'secondary';
      jump.textContent = `Note ${index + 1} · Page ${pin.page}${pin.type && pin.type !== 'pin' ? ' · ' + pin.type : ''}`;
      jump.addEventListener('click', () => {
        commitText(); cancelGesture(); highlight(pin.id);
        if (pdf && page !== pin.page) { pageSelect.value = pin.page; page = pin.page; renderPage(); }
        else drawPins();
        surface.scrollIntoView({block: 'nearest'}); surface.focus({preventScroll: true});
      });
      heading.append(jump);
      if (state.editable) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary';
        remove.textContent = 'Remove'; remove.setAttribute('aria-label', `Remove note ${index + 1}`);
        remove.addEventListener('click', () => removeMark(pin.id)); heading.append(remove);
      }
      entry.append(heading);
      const location = document.createElement('p'); location.className = 'review-pin-location';
      location.textContent = `${Math.round(pin.x * 100)}% from left · ${Math.round(pin.y * 100)}% from top`;
      entry.append(location);
      if (state.editable) {
        const label = document.createElement('label'); label.htmlFor = `note-${pin.id}`; label.textContent = `Change requested for note ${index + 1}`;
        const input = document.createElement('textarea'); input.id = label.htmlFor; input.rows = 3; input.maxLength = 1500; input.value = pin.text;
        input.addEventListener('input', () => { if (!editable()) return; pin.text = input.value; markDirty(); updateControls(); });
        input.addEventListener('focus', () => { commitText(); textEdit = draft(); highlight(pin.id); drawPins(); });
        input.addEventListener('blur', commitText);
        entry.append(label, input);
      } else {
        const text = document.createElement('p'); text.className = 'preserve-lines'; text.id = `note-${pin.id}`; text.tabIndex = -1; text.textContent = pin.text;
        entry.append(text);
      }
      list.append(entry);
    });
    highlight(selected);
  }
  function addMark(mark) {
    if (!interactive()) return;
    if (state.annotations.length >= 100) { showStatus('Use at most 100 notes per review.', true); return; }
    if (state.annotations.reduce((count, entry) => count + (entry.points?.length || 0), 0) + (mark.points?.length || 0) > 3000) {
      showStatus('Use fewer strokes or start a separate review for more drawing detail.', true); drawPins(); return;
    }
    commitText(); const before = draft(); state.annotations.push(mark); selected = mark.id; finishEdit(before);
  }
  tools.forEach(button => button.addEventListener('click', () => {
    setMode(button.dataset.markTool); surface.focus({preventScroll: true});
  }));
  commentButton?.addEventListener('click', () => openComment(selected));
  function travelHistory(direction) {
    if (!editable()) return;
    commitText(); cancelGesture();
    const result = history.apply(draft(), direction); if (!result) return;
    state.annotations = result.annotations; summary.value = result.summary;
    if (!state.annotations.some(mark => mark.id === selected)) selected = '';
    markDirty(); buildNotes(); drawPins(); updateControls();
  }
  undo?.addEventListener('click', () => travelHistory('undo'));
  redo?.addEventListener('click', () => travelHistory('redo'));
  function drawShape(mark, preview = false) {
    const bounds = surface.getBoundingClientRect();
    shapes.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    const points = mark.points.map(point => [point[0] * bounds.width, point[1] * bounds.height]);
    const svg = name => document.createElementNS('http://www.w3.org/2000/svg', name);
    let node;
    if (mark.type === 'box') {
      node = svg('rect'); const a = points[0], b = points[1];
      for (const [key, value] of Object.entries({x: Math.min(a[0], b[0]), y: Math.min(a[1], b[1]), width: Math.abs(a[0] - b[0]), height: Math.abs(a[1] - b[1])})) node.setAttribute(key, value);
    } else {
      node = svg('polyline'); node.setAttribute('points', points.map(point => point.join(',')).join(' '));
    }
    node.setAttribute('fill', 'none'); node.setAttribute('stroke', mark.id === selected ? '#174f73' : '#b83825'); node.setAttribute('stroke-width', mark.id === selected ? '5' : '3');
    node.setAttribute('stroke-linecap', 'round'); node.setAttribute('stroke-linejoin', 'round'); shapes.append(node);
    if (!preview && mode === 'select') {
      const hit = node.cloneNode(); hit.dataset.mark = mark.id; hit.setAttribute('class', 'plan-shape-hit');
      hit.setAttribute('stroke', 'transparent'); hit.setAttribute('stroke-width', '24'); shapes.append(hit);
    }
    if (mark.type === 'arrow' && points.length === 2) {
      const a = points[0], b = points[1], angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
      const head = svg('polyline'); head.setAttribute('points', [[b[0]-14*Math.cos(angle-.45),b[1]-14*Math.sin(angle-.45)],b,[b[0]-14*Math.cos(angle+.45),b[1]-14*Math.sin(angle+.45)]].map(point=>point.join(',')).join(' '));
      head.setAttribute('stroke', mark.id === selected ? '#174f73' : '#b83825'); head.setAttribute('stroke-width','3'); head.setAttribute('fill','none'); shapes.append(head);
    }
  }
  function pointerPoint(event) {
    const box = surface.getBoundingClientRect();
    return [event.clientX-box.left,event.clientY-box.top].map((value,index)=>Math.round(Math.max(0,Math.min(1,value/(index ? box.height : box.width)))*10000)/10000);
  }
  surface.addEventListener('pointerdown', event => {
    if (!interactive() || gesture || !event.isPrimary || event.button !== 0) return;
    commitText();
    const hit = event.target.closest('[data-mark]');
    if (mode === 'select' && !hit) { highlight(''); drawPins(); return; }
    event.preventDefault(); surface.focus({preventScroll: true});
    const point = pointerPoint(event);
    if (mode === 'select') {
      const original = state.annotations.find(mark => mark.id === hit.dataset.mark);
      if (!original) return;
      selected = original.id;
      gesture = {pointerId: event.pointerId, start: point, original: structuredClone(original), handle: hit.dataset.handle ?? '', preview: structuredClone(original)};
    } else {
      if (state.annotations.length >= 100) { showStatus('Use at most 100 notes per review.', true); return; }
      gesture = {pointerId: event.pointerId, start: point};
      stroke = {id: crypto.randomUUID(), page, x: point[0], y: point[1], text: ''};
      if (mode !== 'pin') Object.assign(stroke, {type: mode, points: [point, [...point]]});
    }
    surface.setPointerCapture(event.pointerId); drawPins();
  });
  function updateGesture(event) {
    if (!gesture || event.pointerId !== gesture.pointerId) return;
    const point = pointerPoint(event);
    if (gesture.original) {
      gesture.preview = adjustMark(gesture.original, point[0] - gesture.start[0], point[1] - gesture.start[1], gesture.handle);
    } else if (stroke?.points) {
      if (stroke.type === 'freehand') {
        const last = stroke.points.at(-1);
        if (Math.hypot(point[0] - last[0], point[1] - last[1]) > .001) {
          if (stroke.points.length >= 300) stroke.points = stroke.points.filter((_, index) => index % 2 === 0);
          stroke.points.push(point);
        }
      } else stroke.points[1] = point;
    }
  }
  surface.addEventListener('pointermove', event => {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    updateGesture(event); drawPins();
  });
  surface.addEventListener('pointerup', event => {
    if (!gesture || gesture.pointerId !== event.pointerId) return;
    updateGesture(event);
    const completed = gesture, mark = stroke; gesture = null; stroke = null;
    if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    if (completed.original) {
      if (validMark(completed.preview)) {
        const before = draft(); state.annotations = state.annotations.map(item => item.id === completed.original.id ? completed.preview : item); finishEdit(before);
      } else drawPins();
    } else if (mark && validMark(mark)) addMark(mark);
    else drawPins();
  });
  surface.addEventListener('pointercancel', event => { if (gesture?.pointerId === event.pointerId) cancelGesture(); });
  surface.addEventListener('lostpointercapture', event => { if (gesture?.pointerId === event.pointerId) cancelGesture(); });
  app.addEventListener('keydown', event => {
    if (!editable() || event.defaultPrevented || event.isComposing || event.target.closest('input,textarea,select,[contenteditable="true"]')) return;
    const key = event.key.toLowerCase();
    if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === 'z' || key === 'y')) {
      event.preventDefault(); travelHistory(key === 'y' || event.shiftKey ? 'redo' : 'undo'); return;
    }
    if (event.key === 'Escape') { event.preventDefault(); setMode('select'); return; }
    if (!surface.contains(event.target) || !interactive()) return;
    if (event.key === 'Enter' && mode === 'pin' && event.target === surface) {
      event.preventDefault(); addMark({id: crypto.randomUUID(), page, x: .5, y: .5, text: ''}); return;
    }
    if (mode !== 'select' || event.ctrlKey || event.metaKey || event.altKey) return;
    const mark = state.annotations.find(item => item.id === selected && item.page === page); if (!mark) return;
    if (event.key === 'Delete' || event.key === 'Backspace') { event.preventDefault(); removeMark(mark.id); return; }
    const vectors = {ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1]};
    if (!vectors[event.key]) return;
    event.preventDefault(); cancelGesture(); commitText();
    const [dx, dy] = vectors[event.key], step = event.shiftKey ? .01 : .001;
    const adjusted = adjustMark(mark, dx * step, dy * step, event.target.dataset.handle ?? '');
    if (!validMark(adjusted)) return;
    const before = draft(); state.annotations = state.annotations.map(item => item.id === mark.id ? adjusted : item); finishEdit(before);
  });
  async function renderPage() {
    if (!pdf) return;
    cancelGesture(); rendering = true; updateControls(); drawPins();
    const current = ++renderGeneration;
    renderTask?.cancel();
    try {
      const drawing = await pdf.getPage(page);
      if (current !== renderGeneration) return;
      const base = drawing.getViewport({scale: 1});
      const fit = Math.max(200, surface.parentElement.clientWidth - 28) / base.width;
      const scale = zoom.value === 'fit' ? fit : Number(zoom.value);
      // Cap each canvas to four million pixels; high zoom uses CSS size without unbounded phone memory.
      const rasterScale = Math.min(scale * Math.min(window.devicePixelRatio || 1, 2), Math.sqrt(4000000 / (base.width * base.height)));
      const viewport = drawing.getViewport({scale: rasterScale});
      canvas.width = Math.ceil(viewport.width); canvas.height = Math.ceil(viewport.height);
      surface.style.width = `${base.width * scale}px`; surface.style.height = `${base.height * scale}px`;
      renderTask = drawing.render({canvasContext: canvas.getContext('2d'), viewport});
      await renderTask.promise;
      if (current !== renderGeneration) return;
      drawing.cleanup();
      if (!state.geometry.some(info => info.page === page)) {
        state.geometry.push({page, width: base.width, height: base.height, rotation: base.rotation});
        if (state.editable) markDirty();
      }
      rendering = false; updateControls(); drawPins();
      pdfStatus.textContent = `Page ${page} of ${pdf.numPages}. ${state.editable ? 'Add a numbered note where you want a change.' : 'Select a numbered pin to read its note.'}`;
      document.getElementById('previous-page').disabled = page <= 1;
      document.getElementById('next-page').disabled = page >= pdf.numPages;
    } catch (error) {
      if (error.name !== 'RenderingCancelledException') pdfStatus.textContent = 'This page could not be displayed. Open the original PDF; your saved notes are still available.';
    }
  }
  pageSelect.addEventListener('change', () => { page = Number(pageSelect.value); renderPage(); });
  document.getElementById('previous-page').addEventListener('click', () => { if (page > 1) { page--; pageSelect.value = page; renderPage(); } });
  document.getElementById('next-page').addEventListener('click', () => { if (page < pdf.numPages) { page++; pageSelect.value = page; renderPage(); } });
  zoom.addEventListener('change', () => renderPage());
  let resizeTimer;
  let lastWidth = 0;
  new ResizeObserver(entries => {
    const width = Math.round(entries[0].contentRect.width);
    if (width === lastWidth) return;
    lastWidth = width; clearTimeout(resizeTimer);
    if (zoom.value === 'fit') resizeTimer = setTimeout(renderPage, 200);
  }).observe(surface.parentElement);
  buildNotes(); setMode('select');
  if (state.editable) {
    summary.addEventListener('focus', () => { commitText(); textEdit = draft(); });
    summary.addEventListener('blur', commitText);
    summary.addEventListener('input', () => { markDirty(); updateControls(); });
    if (summary.value !== state.summary) markDirty();
    saveButton.addEventListener('click', () => { commitText(); cancelGesture(); flush().catch(() => {}); });
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (submitting || !form.reportValidity()) return;
      commitText(); setMode('select'); submitting = true; updateControls(); submitButton.disabled = true; saveButton.disabled = true;
      summary.readOnly = true; for (const input of list.querySelectorAll('textarea,button')) input.disabled = true;
      try {
        await flush();
        showStatus('Submitting your revision request…');
        const result = await post(state.submit_url, payload());
        state.editable = false; savedRevision = revision;
        window.location.assign(result.redirect);
      } catch (error) {
        showStatus(error.name === 'AbortError' ? 'The response timed out. Your request may have arrived; retrying this review will not create a duplicate.' : error.message, true);
        submitting = false; updateControls(); submitButton.disabled = false; saveButton.disabled = false;
        summary.readOnly = false; for (const input of list.querySelectorAll('textarea,button')) input.disabled = false;
      }
    });
    window.addEventListener('beforeunload', event => { if (state.editable && revision !== savedRevision) { event.preventDefault(); event.returnValue = ''; } });
  }
  try {
    const library = await import(app.dataset.library);
    library.GlobalWorkerOptions.workerSrc = app.dataset.worker;
    const task = library.getDocument({url: state.pdf_url, withCredentials: true, isEvalSupported: false,
      useWasm: false, cMapUrl: app.dataset.assets + 'cmaps/', cMapPacked: true,
      standardFontDataUrl: app.dataset.assets + 'standard_fonts/', wasmUrl: app.dataset.assets + 'wasm/',
      disableAutoFetch: true, disableStream: true});
    task.onPassword = () => { task.destroy(); pdfStatus.textContent = 'This PDF is password protected. Open the original and describe your changes in the notes box, or ask us to publish an unlocked review copy.'; };
    pdf = await task.promise;
    if (pdf.numPages > 500) { await pdf.destroy(); pdf = null; throw new Error('Too many pages'); }
    pageSelect.replaceChildren();
    for (let index = 1; index <= pdf.numPages; index++) { const option = document.createElement('option'); option.value = index; option.textContent = index; pageSelect.append(option); }
    pageSelect.disabled = false; document.getElementById('page-total').textContent = `of ${pdf.numPages}`;
    await renderPage();
  } catch {
    pdfStatus.textContent = 'The drawing preview could not load. Open the original PDF below; you can still describe changes and submit general notes. Keep this tab open if you have unsaved edits.';
  }
}
