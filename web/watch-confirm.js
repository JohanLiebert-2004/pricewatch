/* Double-opt-in enhancement for the static product page. */
(() => {
  const source = [...document.scripts].map(s => s.textContent).find(s => s.includes("SUPABASE_ANON_KEY")) || "";
  const apiUrl = (source.match(/SUPABASE_URL\s*=\s*"([^"]+)"/) || [])[1];
  const anonKey = (source.match(/SUPABASE_ANON_KEY\s*=\s*"([^"]+)"/) || [])[1];
  const confirmation = new URLSearchParams(location.search).get("confirm");

  function showConfirmation(message, ok) {
    const existing = document.getElementById("confirmation-result");
    const box = existing || document.createElement("div");
    box.id = "confirmation-result";
    box.className = ok ? "watchok" : "watcherr";
    box.textContent = message;
    if (!existing) document.getElementById("main").before(box);
  }

  async function confirmWatch() {
    if (!confirmation || !apiUrl || !anonKey) return;
    try {
      const response = await fetch(`${apiUrl}/rest/v1/rpc/confirm_watch`, {
        method: "POST",
        headers: {apikey: anonKey, Authorization: `Bearer ${anonKey}`, "Content-Type": "application/json"},
        body: JSON.stringify({p_token: confirmation})
      });
      const confirmed = response.ok && await response.json();
      showConfirmation(confirmed
        ? "Your price alert is confirmed. We’ll email you when the target price is reached."
        : "This confirmation link is no longer active. It may already be confirmed or cancelled.", confirmed);
      history.replaceState({}, "", location.pathname);
    } catch (_) {
      showConfirmation("We couldn’t confirm that alert. Please try the link again shortly.", false);
    }
  }

  function tuneWatchPanel() {
    const form = document.getElementById("wform");
    if (!form || form.dataset.confirmationReady) return;
    form.dataset.confirmationReady = "true";
    const button = form.querySelector("button[type=submit]");
    if (button) button.textContent = "Send confirmation email";
    const note = form.nextElementSibling;
    if (note && note.classList.contains("watchnote")) {
      note.textContent = "Confirm the email we send before this alert activates. No account needed; unsubscribe any time.";
    }
    const result = document.getElementById("wresult");
    if (!result) return;
    new MutationObserver(() => {
      if (result.querySelector(".watchok")) {
        result.innerHTML = '<div class="watchok">Almost there — check your email and confirm the alert before it activates.</div>';
      }
    }).observe(result, {childList: true, subtree: true});
  }

  new MutationObserver(tuneWatchPanel).observe(document.getElementById("main"), {childList: true, subtree: true});
  tuneWatchPanel();
  confirmWatch();
})();