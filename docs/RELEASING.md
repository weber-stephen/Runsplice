# Release checklist

- Confirm ownership and license terms for the source, examples, screenshot, and bundled assets. Scan the candidate commit and its history for credentials or private captures; rotate any exposed credential before release.
- Verify that [private vulnerability reporting](https://github.com/weber-stephen/Runsplice/security/advisories) is available and that `SECURITY.md` points to it. Check optional SDK dependencies for known advisories.
- Set the version in `pyproject.toml`, then check that the README and SDK support limits still match the implementation.
- On the candidate commit, install from a clean Python 3.11 environment and run `runsplice --help`, `python -m unittest discover -s tests -v`, and `node --test tests/response_diff.test.mjs tests/inspection.test.mjs`. With a Linux Docker engine, run the README's record, replay, fork, and diff commands.
- Open a PR and wait for the required Ubuntu, Windows, and offline-demo checks. Merge the tested commit, then tag that `main` commit as `v<version>`; do not move a published tag.
- Check the GitHub release title and label against the version. A beta version must say **beta** in its title. GitHub excludes releases marked **prerelease** from **Latest**; while beta is the newest public release, publish it as a normal GitHub release and say beta explicitly. When a stable version ships, mark that release **Latest**.
- Confirm the tag, installed wheel, bundled UI assets, and required Ubuntu, Windows, and offline-demo checks before publishing. Do not publish to PyPI until package release verification is complete.
- If release metadata is wrong, correct it. For code changes after publication, make a new version. Announce only after the installation and demo commands work from the released commit.

Authenticated SDK tests can make billable calls and are not part of CI. Run them deliberately when an SDK integration changes, following [SDK setup](SDK.md).
