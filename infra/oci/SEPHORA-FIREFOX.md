# Sephora AU laptop collector

`local_sephora_sweep.py` runs the existing catalogue refresh through standard
headless Firefox on the Ubuntu laptop. It uses a separate disposable browser
profile, the existing two-second delay, and same-origin browser API requests
with `X-Platform: Web` and `X-Site-Country: AU`. No personal profile, browser
identity overrides, stealth, proxy or challenge handling is used. A retailer
refusal stops the run. Other retailer transports are unchanged.

The wrapper selects `SEPHORA_BROWSER=firefox` for its child process; no private
environment-file change is required. Installed Firefox and geckodriver are
required, as for Big W. The private database uses the existing restricted SSH
tunnel. A local file lock prevents duplicate laptop runs.

Install `services/pricewatch-sephora-local.service` as the user unit
`pricewatch-sephora.service` and the matching timer as `pricewatch-sephora.timer`.
The timer runs every thirty minutes at :08 and :38 in laptop local time,
with up to two minutes of jitter. The service has a 3,700-second deadline and
cleans up the browser process group. Logs are in `~/pricewatch/local_sephora.log`.

The job refreshes up to two API pages (500 listings each), then resumes from
the next page on its next run. `sephora_refresh_state` is persisted only after
pending product writes commit; a blocked page does not advance it. The cursor
wraps at the reported final page. At 15 source pages, a complete rotation
takes eight successful windows, around four hours. Missing page counts and
empty responses are errors. Partially committed failed runs do not write a
success heartbeat. The log must report a completed window and a nonzero
listing count before `sephora_local_heartbeat` is updated. Delisting probes
run at the end of a rotation, rather than after every partial window.

While the job runs, `sephora_local_running` claims ownership for at most
3,700 seconds. Normal cleanup removes that marker, and the hourly CI refresh
skips Sephora while that marker or a success heartbeat younger than 90 minutes
is present. If the laptop stops collecting, CI's original transport resumes.
Sephora has no separate CI enrichment matrix entry; its API supplies product
images, availability and reference prices as part of refresh.

Rollback: disable the user timer, stop its service, and remove any stale
`sephora_local_running`/`sephora_local_heartbeat` rows to release CI ownership
immediately (or let them expire). The original HTTP transport remains the
default outside the laptop wrapper.
