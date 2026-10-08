/* Templates only populate a draft. Sending always requires a server-rendered preview. */
(function () {
  "use strict";
  const source = document.getElementById("information-templates");
  if (!source) return;
  const templates = JSON.parse(source.textContent);
  document.querySelectorAll(".information-compose").forEach(function (form) {
    const picker = form.querySelector(".template-picker");
    const message = form.querySelector("textarea");
    picker.hidden = false;
    form.querySelector("[data-apply-template]").addEventListener("click", function () {
      const key = form.querySelector("[data-information-template]").value;
      if (templates[key]) {
        message.value = templates[key];
        message.focus();
      }
    });
  });
  const review = document.querySelector("[data-email-review]");
  if (review) review.focus();
}());
