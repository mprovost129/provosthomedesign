/* Full active queue, explicit save, shared draft guards; no client messages. */
(function () {
  "use strict";
  const tool = document.querySelector("[data-queue-ordering]");
  if (!tool) return;
  const form = tool.querySelector("[data-order-form]");
  const panel = tool.querySelector("#queue-order-panel");
  const open = tool.querySelector("[data-open-order]");
  const list = tool.querySelector("[data-order-list]");
  const save = tool.querySelector("[data-save-order]");
  const cancel = tool.querySelector("[data-cancel-order]");
  const reload = tool.querySelector("[data-reload-order]");
  const status = tool.querySelector("[data-order-status]");
  let original = [];
  let snapshot = "";
  let busy = false;
  let stale = false;
  let drag = null;
  let frame = 0;

  function ids() { return Array.from(list.children, row => row.dataset.id); }
  function guard() {
    return form.dispatchEvent(new CustomEvent("queue:before-order", {
      bubbles: true, cancelable: true, detail: {form: form}
    }));
  }
  function update() {
    const order = ids();
    const changed = JSON.stringify(order) !== JSON.stringify(original);
    form.elements.order_draft.value = changed ? JSON.stringify(order) : "";
    form.dispatchEvent(new Event("change", {bubbles: true}));
    save.disabled = busy || stale || !changed;
    cancel.disabled = busy;
    reload.disabled = busy;
    list.setAttribute("aria-busy", String(busy));
    Array.from(list.children).forEach(function (row, index) {
      row.querySelector("[data-rank]").textContent = String(index + 1);
      row.querySelector("[data-handle]").disabled = busy;
      row.querySelector("[data-up]").disabled = busy || index === 0;
      row.querySelector("[data-down]").disabled = busy || index === order.length - 1;
    });
  }
  function announce(row) {
    status.textContent = row.dataset.reference + " moved to position " + (Array.from(list.children).indexOf(row) + 1)
      + ". Click Save order to apply your changes.";
  }
  function move(row, direction, focus) {
    if (busy) return;
    const next = direction < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!next) return;
    if (direction < 0) list.insertBefore(row, next);
    else list.insertBefore(next, row);
    update();
    (focus.disabled ? row.querySelector("[data-handle]") : focus).focus({preventScroll: true});
    row.scrollIntoView({block: "nearest"});
    announce(row);
  }
  function rowFor(item) {
    const row = document.createElement("li");
    row.dataset.id = item.id;
    row.dataset.reference = item.reference;
    const handle = document.createElement("button");
    handle.type = "button"; handle.dataset.handle = ""; handle.className = "order-handle secondary";
    handle.textContent = "↕"; handle.setAttribute("aria-label", "Move " + item.reference);
    handle.title = "Drag to move, or use the up and down arrow keys";
    const rank = document.createElement("span"); rank.dataset.rank = ""; rank.className = "order-rank";
    rank.setAttribute("aria-label", "Position");
    const text = document.createElement("div"); text.className = "order-job";
    const title = document.createElement("strong"); title.textContent = item.reference + " · " + item.project;
    const detail = document.createElement("small"); detail.textContent = item.client + " · " + item.kind + " · " + item.status;
    text.append(title, detail);
    const actions = document.createElement("div"); actions.className = "order-row-actions";
    const up = document.createElement("button"); up.type = "button"; up.dataset.up = ""; up.className = "secondary";
    up.textContent = "↑"; up.setAttribute("aria-label", "Move " + item.reference + " up");
    const down = document.createElement("button"); down.type = "button"; down.dataset.down = ""; down.className = "secondary";
    down.textContent = "↓"; down.setAttribute("aria-label", "Move " + item.reference + " down");
    up.addEventListener("click", function () { move(row, -1, up); });
    down.addEventListener("click", function () { move(row, 1, down); });
    handle.addEventListener("keydown", function (event) {
      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
      event.preventDefault(); move(row, event.key === "ArrowUp" ? -1 : 1, handle);
    });
    handle.addEventListener("pointerdown", function (event) {
      if (busy || drag || event.button !== 0) return;
      event.preventDefault(); handle.focus({preventScroll: true});
      drag = {row: row, handle: handle, id: event.pointerId, startY: event.clientY,
        y: event.clientY, started: false, before: ids()};
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener("pointermove", function (event) {
      if (!drag || drag.id !== event.pointerId) return;
      drag.y = event.clientY;
      if (!drag.started && Math.abs(drag.y - drag.startY) < 5) return;
      drag.started = true; row.classList.add("order-dragging");
      placeAtPointer();
      if (!frame) frame = requestAnimationFrame(scrollDrag);
    });
    handle.addEventListener("pointerup", function () { finishDrag(false); });
    handle.addEventListener("pointercancel", function () { finishDrag(true); });
    handle.addEventListener("lostpointercapture", function () { finishDrag(true); });
    actions.append(up, down); row.append(handle, rank, text, actions);
    return row;
  }
  function placeAtPointer() {
    if (!drag) return;
    const others = Array.from(list.children).filter(row => row !== drag.row);
    const anchor = others.find(function (row) {
      const rect = row.getBoundingClientRect();
      return row !== drag.row && drag.y < rect.top + rect.height / 2;
    });
    const before = ids().join(",");
    const target = anchor ? others.indexOf(anchor) : others.length;
    // Keep the captured handle attached. Reparenting it during a pointer drag
    // releases capture in browsers and can cancel the move (especially touch).
    while (Array.from(list.children).indexOf(drag.row) > target) {
      list.insertBefore(drag.row.previousElementSibling, drag.row.nextElementSibling);
    }
    while (Array.from(list.children).indexOf(drag.row) < target) {
      list.insertBefore(drag.row.nextElementSibling, drag.row);
    }
    if (before !== ids().join(",")) update();
  }
  function scrollDrag() {
    frame = 0;
    if (!drag || !drag.started) return;
    const rect = list.getBoundingClientRect();
    if (drag.y < rect.top + 45) list.scrollTop -= 10;
    else if (drag.y > rect.bottom - 45) list.scrollTop += 10;
    placeAtPointer();
    frame = requestAnimationFrame(scrollDrag);
  }
  function finishDrag(canceled) {
    if (!drag) return;
    const prior = drag; drag = null;
    cancelAnimationFrame(frame); frame = 0;
    prior.row.classList.remove("order-dragging");
    if (canceled) {
      const rows = new Map(Array.from(list.children, row => [row.dataset.id, row]));
      prior.before.forEach(id => list.append(rows.get(id)));
    }
    if (prior.handle.hasPointerCapture(prior.id)) prior.handle.releasePointerCapture(prior.id);
    update();
    if (prior.started) {
      if (canceled) status.textContent = "Move canceled. Your previous order is still here.";
      else announce(prior.row);
    }
  }
  async function request(options) {
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, 15000);
    try {
      const response = await fetch(tool.dataset.url, Object.assign({credentials: "same-origin", cache: "no-store",
        signal: controller.signal}, options));
      if (response.redirected) throw new Error("Your sign-in expired. Sign in again in another tab, then reload the order.");
      let data;
      try { data = await response.json(); } catch (_) { throw new Error("The queue could not be reached. Reload the order to check before retrying."); }
      if (!response.ok) {
        if (response.status === 409) stale = true;
        throw new Error(data.error || "The order could not be saved. Reload the order to check before retrying.");
      }
      return data;
    } finally { clearTimeout(timer); }
  }
  async function load() {
    if (!guard() || busy) return;
    busy = true; update(); reload.hidden = true;
    status.textContent = "Loading the active queue…";
    try {
      const data = await request({headers: {"Accept": "application/json"}});
      snapshot = data.snapshot;
      original = data.items.map(item => item.id);
      list.replaceChildren(...data.items.map(rowFor));
      stale = false;
      status.textContent = original.length ? "Arrange the jobs, then click Save order." : "There are no active jobs to arrange.";
    } catch (error) {
      stale = true; reload.hidden = false;
      status.textContent = error.message || "Could not load the queue. Try Reload order.";
    } finally { busy = false; update(); }
  }
  open.hidden = false;
  open.addEventListener("click", function () {
    if (!guard()) return;
    panel.hidden = false; open.hidden = true; open.setAttribute("aria-expanded", "true");
    const heading = panel.querySelector("h2"); heading.tabIndex = -1; heading.focus(); load();
  });
  cancel.addEventListener("click", function () {
    if (busy) return;
    finishDrag(true);
    list.replaceChildren(); original = []; snapshot = ""; update();
    panel.hidden = true; open.hidden = false; open.setAttribute("aria-expanded", "false"); open.focus();
  });
  reload.addEventListener("click", load);
  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    if (busy || stale || save.disabled || !guard()) return;
    busy = true; update(); status.textContent = "Saving the queue order…";
    try {
      const data = await request({method: "POST", headers: {"Accept": "application/json", "Content-Type": "application/json",
        "X-CSRFToken": form.elements.csrfmiddlewaretoken.value}, body: JSON.stringify({ordered_ids: ids(), snapshot: snapshot})});
      if (!data.saved) throw new Error("Could not confirm the saved order. Reload the order before retrying.");
      original = ids(); stale = true; update();
      const refreshed = form.dispatchEvent(new CustomEvent("queue:refresh-after-order", {bubbles: true, cancelable: true}));
      if (!refreshed) status.textContent = "Order saved. Save or undo your other edits, then refresh the queue to see the new positions.";
    } catch (error) {
      // A lost response may still have committed. Re-read before another save.
      stale = true; reload.hidden = false;
      status.textContent = error.message || "Could not confirm the saved order. Reload the order before retrying.";
    } finally { busy = false; update(); }
  });
}());
