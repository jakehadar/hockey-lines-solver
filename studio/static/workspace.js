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

  async function copyText(text) {
    // navigator.clipboard is only exposed in secure contexts (https, or
    // localhost) - on plain http (e.g. this app reached over a Tailscale/LAN
    // IP), it's simply undefined on iOS Safari, so this throws immediately.
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (err) {
        // fall through to the legacy fallback below
      }
    }
    // Legacy execCommand fallback: not gated by secure-context, so it's what
    // actually works for plain-http mobile Safari.
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    document.body.removeChild(textarea);
    return ok;
  }

  copyBtn.addEventListener("click", async () => {
    const link = window.location.origin + "/w/" + window.WORKSPACE_TOKEN + "/rosters";
    const ok = await copyText(link);
    copyBtn.classList.remove("copied", "copy-failed");
    if (ok) {
      copyBtn.classList.add("copied");
      copyLabel.textContent = "Link copied";
    } else {
      copyBtn.classList.add("copy-failed");
      copyLabel.textContent = link;
    }
    setTimeout(() => {
      copyBtn.classList.remove("copied", "copy-failed");
      copyLabel.textContent = "Copy workspace link";
    }, ok ? 1600 : 4000);
  });

  if (toastClose) {
    toastClose.addEventListener("click", () => {
      toast.setAttribute("hidden", "");
    });
  }
})();
