# Repository Guidelines

## Project Structure & Module Organization

`agentdbg/` contains the Python package: recording and replay logic, the CLI, storage, reports, and SDK integrations. The local timeline UI lives in `agentdbg/web/`; its `vendor/` files are bundled assets. `examples/` holds optional live SDK examples; `docs/SDK.md` documents their setup and limits. `tests/test_*.py` contains unit and integration tests, with JSON inputs in `tests/fixtures/`. Read `docs/PRODUCT_INTENT.md` before changing replay behavior.

## Build, Test, and Development Commands

Use Python 3.11 or newer. Run these from the repository root:

```sh
python -m pip install .
python -m unittest discover -s tests -v
runsplice --help
runsplice --store .agentdbg/store record --capture-content -- python -m agentdbg.delivery_example
```

The install provides `runsplice` and the compatibility `agentdbg` command; the module form works directly from a checkout. The test command is the same one used in CI. The last command records a bundled offline example for local inspection. Docker integration tests require a Docker CLI connected to a Linux engine and skip otherwise; replay matching and SDK fork tests run without Docker.

## Coding Style & Naming Conventions

Follow the existing Python style: four-space indentation, `snake_case` functions and modules, and `PascalCase` classes. When changing replay behavior, preserve strict request matching, original runs, and visible replay limits. No formatter or linter is configured; match nearby code and avoid reformatting unrelated files.

## Testing Guidelines

Tests use the standard-library `unittest` framework. Put behavior tests in `tests/test_<area>.py` and name methods `test_<behavior>`. Add or update a focused test when behavior changes. Authenticated SDK tests are optional and make billable calls; follow the setup in `docs/SDK.md` before running `tests.test_sdk_live`.

## Commits, Pull Requests & Security

Recent commits commonly use `feat:`, `fix:`, `test:`, or `docs:` followed by a short imperative summary. In pull requests, describe the user-visible effect, link a relevant issue when one exists, and include screenshots for UI changes. Keep credentials, captured content, and local `.agentdbg/` stores out of commits. Report vulnerabilities privately as directed in `SECURITY.md`.
