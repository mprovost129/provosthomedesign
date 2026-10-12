"use strict";
document.querySelectorAll("[data-history-client]").forEach(button => {
    button.addEventListener("click", () => {
        const selector = document.getElementById("id_client");
        if (!selector) return;
        selector.value = button.dataset.historyClient;
        selector.dispatchEvent(new Event("change", {bubbles: true}));
        selector.focus();
        selector.scrollIntoView({block: "center", behavior: "smooth"});
    });
});
