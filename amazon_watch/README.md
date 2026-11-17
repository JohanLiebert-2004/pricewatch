# Amazon AU on Dealwatch

Amazon discovery, SQLite price history, anomaly detection, fleet coordination and public snapshot publication are integrated into Dealwatch. The public page lives in `web/amazon.html`.

Use [INTEGRATION.md](INTEGRATION.md) for runtime, private data and publication configuration. Run from the repository root with `python -m amazon_watch.fleet watch`, setting `AMAZON_WATCH_DATA_DIR` to the existing private runtime directory. Install `amazon_watch/requirements.txt` in an isolated environment.

Price-error signals require a 60% / A$30 drop against the same seller, condition and delivery context, with at least three hourly history buckets spanning 24 hours. Reference discounts and community quotes are separate evidence. No mistake is guaranteed.

Validate with `python -m pytest amazon_watch/tests tests/test_amazon_publication.py` and the repository checks in `DEPLOY.md`. The original standalone launchers forward to this package for compatibility; private history and SSH keys are not moved into the repository.
