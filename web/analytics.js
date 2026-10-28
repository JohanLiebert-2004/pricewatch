/* GA4 is installed directly. Do not add a second GA4 tag in GTM. */
(() => {
  'use strict';
  if (window.__dealwatchGA4) return;
  const id = 'G-3V1XFPB2L7';
  const url = new URL(window.location.href);
  // Confirmation/unsubscribe links can contain private email-watch tokens.
  // Skip collection entirely on URLs containing sensitive parameters/values.
  if ([...url.searchParams].some(([key, value]) =>
    /token|email|password|secret|auth|code/i.test(key) || value.includes('@'))) return;
  window.__dealwatchGA4 = true;
  const cleanUrl = value => {
    try { const parsed = new URL(value); return parsed.origin + parsed.pathname; }
    catch (_) { return ''; }
  };
  window.dataLayer = window.dataLayer || [];
  window.gtag = window.gtag || function () { window.dataLayer.push(arguments); };
  const page = {
    page_location: url.origin + url.pathname,
    page_referrer: cleanUrl(document.referrer),
    allow_google_signals: false,
    allow_ad_personalization_signals: false,
  };
  window.gtag('js', new Date());
  window.gtag('set', page);
  // The config command sends one initial page_view; no duplicate manual event.
  window.gtag('config', id, page);
  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://www.googletagmanager.com/gtag/js?id=' + id;
  document.head.appendChild(script);
})();
