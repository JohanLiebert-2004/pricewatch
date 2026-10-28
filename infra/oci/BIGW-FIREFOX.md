# Big W collection with standard Firefox

The Ubuntu laptop can use its installed Firefox and geckodriver instead of
the HTTP client. Set `BIGW_BROWSER=firefox` in its private `~/pricewatch/.env`.
The local wrapper passes this setting to both refresh and product crawl.
It still removes `PROXY_URL`, preserves the 2.5-second delay, and skips
product crawling if refresh fails. Other retailers and CI keep their current
transports.

The transport uses standard headless WebDriver and a disposable profile.
It does not use personal browser profiles, import cookies, override the
browser identity, install stealth tools, or retry retailer refusals. No
desktop session is required. Browser and driver close on normal process exit.

Firefox and geckodriver must be installed. Ubuntu's Firefox Snap includes
`/snap/bin/geckodriver`; the transport selects the matching Firefox binary.
Other installations can use `GECKODRIVER` and `FIREFOX_BINARY` to specify their
installed executable paths. No additional Python package is required.

Rollback: remove `BIGW_BROWSER=firefox` from the laptop's private environment
file. Subsequent runs use the previous HTTP client. No timer change is needed.

On 28 October 2026, standard Firefox 157 returned HTTP 200 for the LEGO Mini
Orchid product and a LEGO catalogue page. The existing parser extracted the
$39 product price and 144 catalogue records, including six reference prices.
This verifies those pages; a full catalogue sweep must be checked separately.
