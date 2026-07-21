(function () {
  "use strict";

  function sendEvent(name, parameters) {
    if (typeof window.gtag !== "function") return;
    window.gtag("event", name, parameters || {});
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-analytics-page-event]").forEach(function (element) {
      var parameters = {
        page_location: window.location.href,
      };
      if (element.dataset.analyticsSource) {
        parameters.inquiry_source = element.dataset.analyticsSource;
      }
      if (element.dataset.analyticsProjectType) {
        parameters.project_type = element.dataset.analyticsProjectType;
      }
      sendEvent(element.dataset.analyticsPageEvent, parameters);
    });

    document.querySelectorAll("form[data-analytics-form]").forEach(function (form) {
      var started = false;
      var formName = form.dataset.analyticsForm;
      form.addEventListener("focusin", function () {
        if (started) return;
        started = true;
        sendEvent("form_start", {
          form_name: formName,
          inquiry_source: form.dataset.analyticsSource || "direct",
        });
      });
      form.addEventListener("submit", function () {
        sendEvent("form_submit", {
          form_name: formName,
          inquiry_source: form.dataset.analyticsSource || "direct",
        });
      });
    });

    document.addEventListener("click", function (event) {
      var target = event.target.closest("a, button");
      if (!target) return;

      var eventName = target.dataset.analyticsEvent;
      var label = target.dataset.analyticsLabel || target.textContent.trim().slice(0, 100);
      if (eventName) {
        sendEvent(eventName, { event_label: label });
        return;
      }

      if (target.matches("a[href^='tel:']")) {
        sendEvent("phone_click", { link_url: target.getAttribute("href") });
      } else if (target.matches("a[href^='mailto:']")) {
        sendEvent("email_click", { link_url: target.getAttribute("href") });
      } else if (target.classList.contains("toggle-favorite")) {
        sendEvent("plan_favorite_toggle", { plan_id: target.dataset.planId });
      } else if (target.classList.contains("toggle-comparison")) {
        sendEvent("plan_comparison_toggle", { plan_id: target.dataset.planId });
      }
    });
  });
})();
