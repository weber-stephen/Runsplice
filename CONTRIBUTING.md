# Contributing

This project explores deterministic agent replay through three bundled offline examples. Read [Product intent](docs/PRODUCT_INTENT.md) before changing replay behavior or proposing a broader integration. Describe the user-visible effect in the pull request. Preserve strict request matching, the original run, and visible replay limitations.

Use Python 3.11 or newer. From a checkout, run `python -m pip install .` and `python -m unittest discover -s tests -v`. Docker integration tests need a Docker CLI connected to a Linux engine and skip otherwise. Replay matching and mocked SDK fork tests run without Docker. Add or update a focused test when behavior changes. Keep captures, credentials, and local stores out of commits.

For vulnerabilities, use the private reporting route in [SECURITY.md](SECURITY.md) rather than a public issue.
