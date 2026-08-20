(function () {
  "use strict";

  const trigger = document.getElementById("ws-trigger");
  const menu = document.getElementById("ws-menu");
  const toast = document.getElementById("ws-toast");
  const toastClose = document.getElementById("ws-toast-close");
  const copyBtn = document.getElementById("ws-copy-btn");
  const copyLabel = document.getElementById("ws-copy-label");

  function openMenu() {
    menu.classList.add("open");
    trigger.setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    menu.classList.remove("open");
    trigger.setAttribute("aria-expanded", "false");
  }

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    if (menu.classList.contains("open")) closeMenu();
    else openMenu();
  });

  document.addEventListener("click", (e) => {
    if (!menu.contains(e.target) && e.target !== trigger) closeMenu();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeMenu();
  });

  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(window.location.origin + "/w/" + window.WORKSPACE_TOKEN + "/rosters");
    } catch (err) {
      // Clipboard permission denied or unavailable — the link is still visible in the address bar.
    }
    copyBtn.classList.add("copied");
    copyLabel.textContent = "Link copied";
    setTimeout(() => {
      copyBtn.classList.remove("copied");
      copyLabel.textContent = "Copy workspace link";
    }, 1600);
  });

  if (toastClose) {
    toastClose.addEventListener("click", () => {
      toast.setAttribute("hidden", "");
    });
  }
})();
