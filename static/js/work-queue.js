/* Client messages are explicit. Polling never replaces queue rows or drafts. */
(function () {
  "use strict";
  const queue = document.querySelector(".queue-work");
  if (!queue) return;
  const forms = Array.from(queue.querySelectorAll('form[method="post"]'));
  const baseline = new Map();
  const dirty = new Set();
  const warning = queue.querySelector("[data-unsaved-warning]");
  const notice = queue.querySelector("[data-arrivals]");
  const refresh = queue.querySelector("[data-refresh-queue]");
  const arrivalText = queue.querySelector("[data-arrival-text]");
  const arrivalHelp = queue.querySelector("[data-arrival-help]");
  let navigating = false;
  let unavailable = false;

  function values(form) {
    return JSON.stringify(Array.from(new FormData(form).entries()));
  }
  function protectRefresh() {
    if (!refresh) return;
    refresh.disabled = dirty.size > 0;
    arrivalHelp.textContent = dirty.size
      ? "Save or undo your unsaved edits before refreshing. Your drafts are still on this page."
      : "Refresh keeps your current filters. Your edits are never replaced automatically.";
  }
  function updateDirty(form) {
    if (form.dataset.unsaved === "true" || values(form) !== baseline.get(form)) dirty.add(form);
    else dirty.delete(form);
    if (!dirty.size && warning) warning.hidden = true;
    protectRefresh();
  }
  forms.forEach(function (form) {
    if (form.querySelector(".errorlist")) form.dataset.unsaved = "true";
    baseline.set(form, values(form));
    updateDirty(form);
    form.addEventListener("input", function () { updateDirty(form); });
    form.addEventListener("change", function () { updateDirty(form); });
    form.addEventListener("reset", function () {
      delete form.dataset.unsaved;
      setTimeout(function () { updateDirty(form); }, 0);
    });
  });
  queue.addEventListener("submit", function (event) {
    if (event.defaultPrevented) return;
    const others = Array.from(dirty).filter(function (draft) { return draft !== event.target; });
    if (others.length) {
      event.preventDefault();
      warning.textContent = "You have unsaved edits in another form. Save or undo those edits first so they are not lost.";
      warning.hidden = false;
      warning.tabIndex = -1;
      warning.focus();
      return;
    }
    navigating = true;
  });
  window.addEventListener("beforeunload", function (event) {
    if (dirty.size && !navigating) {
      event.preventDefault();
      event.returnValue = "";
    }
  });
  window.addEventListener("pageshow", function () { navigating = false; });

  // The arranger uses the same draft protection as status, notes and emails.
  queue.addEventListener("queue:before-order", function (event) {
    if (Array.from(dirty).some(function (form) { return form !== event.detail.form; })) {
      event.preventDefault();
      warning.textContent = "Save or undo your other unsaved edits before arranging the queue.";
      warning.hidden = false;
      warning.tabIndex = -1;
      warning.focus();
    }
  });
  queue.addEventListener("queue:refresh-after-order", function (event) {
    if (dirty.size) { event.preventDefault(); protectRefresh(); return; }
    navigating = true;
    window.location.reload();
  });

  const source = document.getElementById("information-templates");
  if (source) {
    const templates = JSON.parse(source.textContent);
    queue.querySelectorAll(".information-compose").forEach(function (form) {
      const picker = form.querySelector(".template-picker");
      const message = form.querySelector("textarea");
      picker.hidden = false;
      form.querySelector("[data-apply-template]").addEventListener("click", function () {
        const key = form.querySelector("[data-information-template]").value;
        if (templates[key]) {
          message.value = templates[key];
          updateDirty(form);
          message.focus();
        }
      });
    });
  }
  queue.querySelectorAll("form[data-current-status]").forEach(function (form) {
    const status = form.querySelector('select[name="status"], select[name$="-status"]');
    const choice = form.querySelector('select[name="client_email"], select[name$="-client_email"]');
    const preview = form.querySelector("[data-milestone-preview]");
    function updatePreview() {
      const sends = choice.value === "send" && status.value !== form.dataset.currentStatus;
      preview.hidden = choice.value !== "send";
      preview.textContent = sends && status.value === "in_progress"
        ? 'Client message: "Work has started on your request." Includes their request ID, project and tracking link.'
        : sends && status.value === "completed"
          ? 'Client message: "The work for your request has been marked complete." Includes their request ID, project and tracking link.'
          : "No email will be sent for this save. Emails send only on a change to In Progress or Completed.";
    }
    status.addEventListener("change", updatePreview);
    choice.addEventListener("change", updatePreview);
    updatePreview();
  });
  const review = queue.querySelector("[data-email-review]");
  if (review) review.focus();

  if (!notice || !refresh) return;
  refresh.addEventListener("click", function () {
    if (dirty.size) { protectRefresh(); return; }
    navigating = true;
    window.location.reload();
  });
  let inFlight = false;
  async function poll() {
    if (document.hidden || inFlight || navigating) return;
    inFlight = true;
    const controller = new AbortController();
    const timer = setTimeout(function () { controller.abort(); }, 10000);
    try {
      const url = new URL(notice.dataset.url, window.location.origin);
      url.searchParams.set("snapshot", notice.dataset.snapshot);
      const response = await fetch(url, {credentials: "same-origin", cache: "no-store",
        headers: {"Accept": "application/json"}, signal: controller.signal});
      if (!response.ok || response.redirected) throw new Error("Unavailable");
      const data = await response.json();
      if (!Number.isSafeInteger(data.count) || data.count < 0) throw new Error("Invalid count");
      unavailable = false;
      notice.hidden = data.count === 0;
      const text = data.count + " new request" + (data.count === 1 ? "" : "s");
      if (arrivalText.textContent !== text) arrivalText.textContent = text;
      protectRefresh();
    } catch (error) {
      if (!unavailable) {
        unavailable = true;
        notice.hidden = false;
        arrivalText.textContent = "Checking for new requests is unavailable. Refresh when ready to check the queue.";
        protectRefresh();
      }
    } finally {
      clearTimeout(timer);
      inFlight = false;
    }
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  setInterval(poll, 30000);
  poll();
}());
