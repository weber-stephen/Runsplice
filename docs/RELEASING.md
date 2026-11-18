# Release checklist

- Set the version in `pyproject.toml`, then tag the verified `main` commit as `v<version>`.
- Check the GitHub release title and label against the version. A beta version must say **beta** in its title. GitHub excludes releases marked **prerelease** from **Latest**; while beta is the newest public release, publish it as a normal GitHub release and say beta explicitly. When a stable version ships, mark that release **Latest**.
- Confirm the tag, installed wheel, bundled UI assets, and required Ubuntu, Windows, and offline-demo checks before publishing. Do not publish to PyPI until package release verification is complete.
