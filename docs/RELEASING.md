# Releasing Runsplice

Runsplice is a public beta. Use Python [PEP 440](https://peps.python.org/pep-0440/) versions in `pyproject.toml` (`0.2.0b1` for a beta) and matching `v`-prefixed Git tags (`v0.2.0b1`). Mark beta GitHub Releases as prereleases. A GitHub release does not publish a PyPI package; installation is currently from a checkout.

1. Confirm the source, examples, screenshot, and bundled assets can be distributed under the repository's licenses. Review the candidate commit and its history for credentials or private captures. Rotate any exposed credential before release; removing it from the current tree alone is insufficient.
2. Verify [private vulnerability reporting](https://github.com/weber-stephen/Runsplice/security/advisories/new) works and that `SECURITY.md` describes the same route.
3. Update `pyproject.toml` to the intended version and check that the README and SDK limits still match the implementation. Keep the beta label until the supported own-agent paths justify removing it.
4. On the candidate commit, run `python -m pip install .`, `runsplice --help`, `python -m unittest discover -s tests -v`, and `node --test tests/response_diff.test.mjs tests/inspection.test.mjs`. Use a Linux Docker engine to run the record, replay, fork, and diff commands in the README. Check optional SDK dependency advisories and compatibility before publishing.
5. Open a PR and wait for the required Ubuntu, Windows, and offline-demo checks. Merge the tested commit; do not tag an earlier head.
6. Create the matching `v` tag from the merged commit and a GitHub release. State the supported paths, replay limits, capture privacy, and checkout installation. Select **prerelease** for a beta version. Check the release page after publishing for the correct tag, commit, and prerelease label.
7. If the release is wrong, correct its metadata and publish a new version for code changes. Do not silently move a published tag. Announce only after the installation and demo commands work from the released commit.

Authenticated SDK tests make billable calls and are not part of CI. Run them deliberately when changing an SDK integration, following [SDK setup](SDK.md).
