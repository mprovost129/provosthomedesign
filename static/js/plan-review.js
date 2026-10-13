/* Version-bound pins, bounded page rendering, serialized autosave. No document data leaves this origin. */
const app = document.getElementById('plan-review-app');
if (app) {
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
  const add = document.getElementById('add-pin');
  const tools = document.getElementById('mark-tool');
  const undo = document.getElementById('undo-mark');
  const shapes = document.getElementById('plan-shapes');
  let stroke = null;
  const undoStack = [];
  const tool = () => tools?.value || 'pin';
  const saveButton = document.getElementById('save-review');
  const submitButton = document.getElementById('submit-review');
  let page = 1, pdf = null, placing = false, selected = '';
  let revision = 0, savedRevision = 0, savePromise = null, timer = null, blocked = false, submitting = false;
  let renderTask = null, renderGeneration = 0;
  const csrf = form.querySelector('[name=csrfmiddlewaretoken]').value;
  const showStatus = (text, error = false) => { status.textContent = text; status.classList.toggle('error', error); };
  const markDirty = () => {
    if (!state.editable) return;
    revision++;
    showStatus('Unsaved changes. Saving your draft…');
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
  function highlight(id) {
    selected = id;
    for (const button of pins.children) button.classList.toggle('selected', button.dataset.pin === id);
    for (const entry of list.children) entry.classList.toggle('selected', entry.dataset.pin === id);
  }
  function drawPins() {
    pins.replaceChildren();
    shapes.replaceChildren();
    state.annotations.forEach(mark => { if (mark.page === page && mark.type && mark.type !== 'pin') drawShape(mark); });
    if (stroke) drawShape(stroke);
    state.annotations.forEach((pin, index) => {
      if (pin.page !== page) return;
      const button = document.createElement('button');
      button.type = 'button'; button.className = 'plan-pin'; button.textContent = index + 1;
      button.dataset.pin = pin.id; button.style.left = `${pin.x * 100}%`; button.style.top = `${pin.y * 100}%`;
      button.setAttribute('aria-label', `Note ${index + 1}, page ${pin.page}: ${pin.text || 'Add a comment'}`);
      button.addEventListener('click', event => {
        event.stopPropagation(); highlight(pin.id);
        const target = document.getElementById(`note-${pin.id}`);
        if (target) { target.focus({preventScroll: true}); target.scrollIntoView({block: 'nearest', behavior: 'smooth'}); }
      });
      pins.append(button);
    });
    highlight(selected);
  }
  function buildNotes(focus = '') {
    list.replaceChildren();
    state.annotations.forEach((pin, index) => {
      const entry = document.createElement('li'); entry.dataset.pin = pin.id;
      const heading = document.createElement('div'); heading.className = 'review-note-heading';
      const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'secondary';
      jump.textContent = `Note ${index + 1} · Page ${pin.page}${pin.type && pin.type !== 'pin' ? ' · ' + pin.type : ''}`;
      jump.addEventListener('click', () => { highlight(pin.id); if (pdf) { pageSelect.value = pin.page; page = pin.page; renderPage(); } });
      heading.append(jump);
      if (state.editable) {
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary';
        remove.textContent = 'Remove'; remove.setAttribute('aria-label', `Remove note ${index + 1}`);
        remove.addEventListener('click', () => { undoStack.push({kind: 'remove', mark: structuredClone(pin), index}); state.annotations = state.annotations.filter(note => note.id !== pin.id); undo.disabled = false; markDirty(); buildNotes(); drawPins(); });
        heading.append(remove);
      }
      entry.append(heading);
      const location = document.createElement('p'); location.className = 'review-pin-location';
      location.textContent = `${Math.round(pin.x * 100)}% from left · ${Math.round(pin.y * 100)}% from top`;
      entry.append(location);
      if (state.editable) {
        const label = document.createElement('label'); label.htmlFor = `note-${pin.id}`; label.textContent = `Change requested for note ${index + 1}`;
        const input = document.createElement('textarea'); input.id = label.htmlFor; input.rows = 3; input.maxLength = 1500; input.value = pin.text;
        input.addEventListener('input', () => { pin.text = input.value; markDirty(); });
        input.addEventListener('focus', () => highlight(pin.id));
        entry.append(label, input);
      } else {
        const text = document.createElement('p'); text.className = 'preserve-lines'; text.id = `note-${pin.id}`; text.tabIndex = -1; text.textContent = pin.text;
        entry.append(text);
      }
      list.append(entry);
    });
    highlight(selected);
    if (focus) document.getElementById(`note-${focus}`)?.focus({preventScroll: true});
  }
  function setPlacing(value) {
    stroke = null; placing = value; surface.classList.toggle('placing', value);
    surface.classList.toggle('drawing', value && tool() !== 'pin');
    if (!value) drawPins();
    if (add) { add.setAttribute('aria-pressed', String(value)); add.textContent = value ? 'Cancel' : (tool() === 'pin' ? 'Add a note' : 'Draw ' + tool()); }
  }
  function place(x, y) {
    if (!placing || !pdf || !state.editable) return;
    if (state.annotations.length >= 100) { showStatus('Use at most 100 notes per review.', true); return; }
    const pin = {id: crypto.randomUUID(), page, x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y)), text: ''};
    state.annotations.push(pin); undoStack.push({kind: 'add', id: pin.id}); undo.disabled = false; selected = pin.id;
    setPlacing(false); markDirty(); buildNotes(pin.id); drawPins();
    document.getElementById(`note-${pin.id}`).scrollIntoView({block: 'nearest', behavior: 'smooth'});
  }
  surface.addEventListener('click', event => {
    if (event.target.closest('.plan-pin') || tool() !== 'pin') return;
    const bounds = surface.getBoundingClientRect();
    place((event.clientX - bounds.left) / bounds.width, (event.clientY - bounds.top) / bounds.height);
  });
  surface.addEventListener('keydown', event => { if (placing && tool() === 'pin' && event.key === 'Enter') { event.preventDefault(); place(.5, .5); } if (event.key === 'Escape') setPlacing(false); });
  add?.addEventListener('click', () => { setPlacing(!placing); if (placing) surface.focus({preventScroll: true}); });
  tools?.addEventListener('change', () => setPlacing(false));
  undo?.addEventListener('click', () => {
    const last = undoStack.pop(); if (!last) return;
    if (last.kind === 'add') state.annotations = state.annotations.filter(mark => mark.id !== last.id);
    else state.annotations.splice(last.index, 0, last.mark);
    undo.disabled = !undoStack.length; markDirty(); buildNotes(); drawPins();
  });
  function drawShape(mark) {
    const bounds = surface.getBoundingClientRect();
    shapes.setAttribute('viewBox', `0 0 ${bounds.width} ${bounds.height}`);
    const points = mark.points.map(point => [point[0]*bounds.width, point[1]*bounds.height]);
    const svg = name => document.createElementNS('http://www.w3.org/2000/svg', name);
    let node;
    if (mark.type === 'box') {
      node = svg('rect'); const a = points[0], b = points[1];
      for (const [key,value] of Object.entries({x:Math.min(a[0],b[0]),y:Math.min(a[1],b[1]),width:Math.abs(a[0]-b[0]),height:Math.abs(a[1]-b[1])})) node.setAttribute(key,value);
    } else {
      node = svg('polyline'); node.setAttribute('points',points.map(point => point.join(',')).join(' '));
    }
    node.setAttribute('fill','none'); node.setAttribute('stroke','#b83825'); node.setAttribute('stroke-width','3');
    node.setAttribute('stroke-linecap','round'); node.setAttribute('stroke-linejoin','round'); shapes.append(node);
    if (mark.type === 'arrow' && points.length === 2) {
      const a=points[0],b=points[1],angle=Math.atan2(b[1]-a[1],b[0]-a[0]);
      const head=svg('polyline'); head.setAttribute('points',[[b[0]-14*Math.cos(angle-.45),b[1]-14*Math.sin(angle-.45)],b,[b[0]-14*Math.cos(angle+.45),b[1]-14*Math.sin(angle+.45)]].map(point=>point.join(',')).join(' '));
      head.setAttribute('stroke','#b83825');head.setAttribute('stroke-width','3');head.setAttribute('fill','none');shapes.append(head);
    }
  }
  function pointerPoint(event) {
    const box=surface.getBoundingClientRect();
    return [event.clientX-box.left,event.clientY-box.top].map((value,index)=>Math.round(Math.max(0,Math.min(1,value/(index ? box.height : box.width)))*10000)/10000);
  }
  surface.addEventListener('pointerdown', event => {
    if (!placing || !state.editable || !pdf || tool()==='pin' || event.target.closest('.plan-pin')) return;
    if (state.annotations.length>=100) { showStatus('Use at most 100 notes per review.',true); return; }
    event.preventDefault();surface.setPointerCapture(event.pointerId);
    const point=pointerPoint(event);
    stroke={id:crypto.randomUUID(),type:tool(),page,x:point[0],y:point[1],points:[point,point],text:''};
    drawPins();
  });
  surface.addEventListener('pointermove', event => {
    if (!stroke) return;
    const point=pointerPoint(event);
    if (stroke.type==='freehand') {
      const last=stroke.points.at(-1);
      if (Math.hypot(point[0]-last[0],point[1]-last[1])>.001) {
        if (stroke.points.length>=300) stroke.points=stroke.points.filter((_,index)=>index%2===0);
        stroke.points.push(point);
      }
    } else stroke.points[1]=point;
    drawPins();
  });
  surface.addEventListener('pointerup', event => {
    if (!stroke) return;
    const mark=stroke; stroke=null;
    if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    if (Math.max(...mark.points.map(point=>Math.abs(point[0]-mark.x)+Math.abs(point[1]-mark.y)))<.001) { drawPins();return; }
    const pointCount=state.annotations.reduce((count,entry)=>count+(entry.points?.length||0),0)+mark.points.length;
    if (pointCount>3000) { showStatus('Use fewer strokes or start a separate review for more drawing detail.',true);drawPins();return; }
    state.annotations.push(mark);undoStack.push({kind:'add',id:mark.id});undo.disabled=false;selected=mark.id;
    setPlacing(false);markDirty();buildNotes(mark.id);drawPins();
    document.getElementById(`note-${mark.id}`).scrollIntoView({block:'nearest',behavior:'smooth'});
  });
  surface.addEventListener('pointercancel',()=>{stroke=null;drawPins();});
  async function renderPage() {
    if (!pdf) return;
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
      drawPins();
      pdfStatus.textContent = `Page ${page} of ${pdf.numPages}. ${state.editable ? 'Add a numbered note where you want a change.' : 'Select a numbered pin to read its note.'}`;
      document.getElementById('previous-page').disabled = page <= 1;
      document.getElementById('next-page').disabled = page >= pdf.numPages;
    } catch (error) {
      if (error.name !== 'RenderingCancelledException') pdfStatus.textContent = 'This page could not be displayed. Open the original PDF; your saved notes are still available.';
    }
  }
  pageSelect.addEventListener('change', () => { page = Number(pageSelect.value); setPlacing(false); renderPage(); });
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
  buildNotes();
  if (state.editable) {
    summary.addEventListener('input', markDirty);
    if (summary.value !== state.summary) markDirty();
    saveButton.addEventListener('click', () => flush().catch(() => {}));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (submitting || !form.reportValidity()) return;
      setPlacing(false); submitting = true; submitButton.disabled = true; saveButton.disabled = true; add.disabled = true;
      if (tools) tools.disabled = true; if (undo) undo.disabled = true; summary.readOnly = true; for (const input of list.querySelectorAll('textarea,button')) input.disabled = true;
      try {
        await flush();
        showStatus('Submitting your revision request…');
        const result = await post(state.submit_url, payload());
        state.editable = false; savedRevision = revision;
        window.location.assign(result.redirect);
      } catch (error) {
        showStatus(error.name === 'AbortError' ? 'The response timed out. Your request may have arrived; retrying this review will not create a duplicate.' : error.message, true);
        submitting = false; submitButton.disabled = false; saveButton.disabled = false; add.disabled = !pdf;
        if (tools) tools.disabled = false; if (undo) undo.disabled = !undoStack.length; summary.readOnly = false; for (const input of list.querySelectorAll('textarea,button')) input.disabled = false;
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
    if (add) add.disabled = false;
    await renderPage();
  } catch {
    pdfStatus.textContent = 'The drawing preview could not load. Open the original PDF below; you can still describe changes and submit general notes. Keep this tab open if you have unsaved edits.';
  }
}
