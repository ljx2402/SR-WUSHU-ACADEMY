// Print button for receipts and invoices (no inline script, so a strict
// Content-Security-Policy can forbid inline JavaScript).
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("[data-print]").forEach(function (button) {
    button.addEventListener("click", function () { window.print(); });
  });
});
