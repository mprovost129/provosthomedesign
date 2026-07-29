(function () {
  "use strict";

  const FORM_SELECTOR = [
    "form[data-preserve-file-uploads]",
    "body.app-plans.model-plans.change-form form#plans_form",
  ].join(",");

  function controlsByName(form) {
    const controls = new Map();
    form.querySelectorAll("input[name], select[name], textarea[name]").forEach((control) => {
      if (!controls.has(control.name)) controls.set(control.name, control);
    });
    return controls;
  }

  function fieldLabel(form, control) {
    if (!control) return "Form";
    const label = Array.from(form.querySelectorAll("label")).find(
      (candidate) => candidate.htmlFor === control.id
    );
    return (label && label.textContent.trim().replace(/\s+/g, " ")) || control.name;
  }

  function errorMessages(errorList) {
    return Array.from(errorList.children)
      .map((item) => item.textContent.trim().replace(/\s+/g, " "))
      .filter(Boolean);
  }

  function relatedControl(errorList) {
    const containers = [".form-row", "td", ".fieldBox", ".mb-3", ".card-body"];
    for (const selector of containers) {
      const container = errorList.closest(selector);
      if (!container) continue;
      const invalid = container.querySelector('[name][aria-invalid="true"]');
      if (invalid) return invalid;
      const control = container.querySelector("input[name], select[name], textarea[name]");
      if (control) return control;
    }
    return null;
  }

  function showValidationErrors(form, returnedForm) {
    form.querySelectorAll("[data-preserved-upload-error]").forEach((node) => node.remove());
    form.querySelectorAll('[aria-invalid="true"]').forEach((control) => {
      control.removeAttribute("aria-invalid");
    });

    const currentControls = controlsByName(form);
    const errors = [];

    returnedForm.querySelectorAll("ul.errorlist").forEach((errorList) => {
      const serverControl = relatedControl(errorList);
      const currentControl = serverControl ? currentControls.get(serverControl.name) : null;
      const label = fieldLabel(returnedForm, serverControl);

      errorMessages(errorList).forEach((message) => {
        const description = label === "Form" ? message : `${label}: ${message}`;
        if (!errors.includes(description)) errors.push(description);
      });

      if (currentControl) currentControl.setAttribute("aria-invalid", "true");
    });

    const notice = document.createElement("p");
    notice.className = "errornote";
    notice.dataset.preservedUploadError = "true";
    notice.setAttribute("role", "alert");
    notice.textContent = "Please correct the errors below. Your selected images are still attached.";

    form.prepend(notice);

    if (errors.length) {
      const summary = document.createElement("ul");
      summary.className = "errorlist nonfield";
      summary.dataset.preservedUploadError = "true";
      errors.forEach((message) => {
        const item = document.createElement("li");
        item.textContent = message;
        summary.appendChild(item);
      });
      notice.insertAdjacentElement("afterend", summary);
    }

    notice.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function showRequestError(form) {
    form.querySelectorAll("[data-preserved-upload-request-error]").forEach((node) => node.remove());
    const notice = document.createElement("p");
    notice.className = "errornote";
    notice.dataset.preservedUploadRequestError = "true";
    notice.setAttribute("role", "alert");
    notice.textContent = "The save request could not be completed. Your selected images are still attached; please try again.";
    form.prepend(notice);
    notice.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest(FORM_SELECTOR);
    if (!form || !form.querySelector('input[type="file"]')) return;
    if (form.dataset.preserveUploadSubmitting === "true") {
      event.preventDefault();
      return;
    }

    event.preventDefault();
    form.dataset.preserveUploadSubmitting = "true";

    const submitter = event.submitter;
    const formData = new FormData(form);
    if (submitter && submitter.name && !formData.has(submitter.name)) {
      formData.append(submitter.name, submitter.value);
    }

    const buttons = Array.from(form.querySelectorAll('button[type="submit"], input[type="submit"]'));
    const priorDisabledState = buttons.map((button) => button.disabled);
    buttons.forEach((button) => { button.disabled = true; });

    try {
      const response = await fetch(form.action || window.location.href, {
        method: (form.method || "post").toUpperCase(),
        body: formData,
        credentials: "same-origin",
        headers: {
          "X-Preserve-File-Uploads": "1",
          "X-Requested-With": "XMLHttpRequest",
        },
      });

      const contentType = response.headers.get("content-type") || "";
      if (response.ok && contentType.includes("application/json")) {
        const result = await response.json();
        if (result.redirect) {
          window.location.assign(result.redirect);
          return;
        }
      }

      if (!response.ok) throw new Error(`Save failed with status ${response.status}`);

      const returnedDocument = new DOMParser().parseFromString(await response.text(), "text/html");
      const returnedForm = returnedDocument.querySelector(FORM_SELECTOR);
      if (!returnedForm) throw new Error("The validation response did not contain the plan form");

      showValidationErrors(form, returnedForm);
    } catch (error) {
      showRequestError(form);
    } finally {
      delete form.dataset.preserveUploadSubmitting;
      buttons.forEach((button, index) => { button.disabled = priorDisabledState[index]; });
    }
  });
})();
