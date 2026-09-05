# Releasing

`aiflow-format` is **not yet published to PyPI**. This is the procedure to publish it,
written down because two of the steps require the maintainer's own PyPI account and
cannot be done by anyone else.

The automation already exists: [`.github/workflows/publish.yml`](../.github/workflows/publish.yml)
builds, runs the full test suite, verifies the wheel resolves its spec artifacts from
outside the checkout, checks the version matches the tag, and uploads via
[trusted publishing](https://docs.pypi.org/trusted-publishers/) — so no API token is
ever stored in the repository.

## One-time setup — maintainer only

These two steps need a PyPI login and cannot be automated from here.

**1. Reserve the project name.** Sign in at [pypi.org](https://pypi.org/), then either
publish once manually or create a pending publisher (below), which reserves the name
without an upload.

**2. Configure trusted publishing.** At
<https://pypi.org/manage/account/publishing/>, add a GitHub publisher:

| Field | Value |
|---|---|
| PyPI project name | `aiflow-format` |
| Owner | `AyushSinghRana15` |
| Repository name | `AIFLOW` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The environment name must be exactly `pypi` — it is what the `publish` job in
`publish.yml` declares. A mismatch fails the upload with a confusing permissions error
rather than an obvious one.

**3. Create the GitHub environment.** In the repository, under
*Settings → Environments*, create one named `pypi`. Adding a required reviewer there is
worth it: it turns every release into something a human approves.

## Each release

1. **Update the changelog.** Move items out of `[Unreleased]` into a new version
   heading in [`CHANGELOG.md`](../CHANGELOG.md).

2. **Bump the version** in `pyproject.toml`. The workflow refuses to publish if it does
   not match the tag, so these two cannot silently diverge.

3. **Verify locally**, because a release must not ship code the suites have not passed:

   ```bash
   python tests/run_all.py
   python docs/build.py --check
   python -m build && python -m twine check dist/*
   ```

4. **Tag and push.**

   ```bash
   git tag -a v0.1.0 -m "AIFLOW 0.1.0"
   git push origin v0.1.0
   ```

5. **Cut the release.** `gh release create v0.1.0 --generate-notes`, or use the GitHub
   UI. Publishing the release is what triggers the workflow — pushing the tag alone
   does not.

6. **Confirm.** `pip install aiflow-format` in a clean virtualenv, then run
   `aiflow --version` from a directory that is not a checkout. That last part matters:
   the spec artifacts are force-included into the wheel from the repository root, and
   running inside a checkout would mask a break in that wiring.

## Dry run

`publish.yml` can be run manually from the Actions tab with `dry-run` enabled. It
builds and checks everything without uploading, which is the right way to validate a
change to the workflow itself.

## After the first release

Update [`README.md`](../README.md): make `pip install aiflow-format` the primary
install instruction again and remove the "not yet published" note. Update the version
table in [`docs/CONFORMANCE.md`](CONFORMANCE.md) if the implementation's conformance
level has changed.

## Versioning

The **package** and the **specification** are versioned separately. Shipping
`aiflow-format` 0.2.0 does not imply a spec change, and a spec change to 1.2 does not
require a major package bump. See [the spec's version table](../spec/SPEC.md#9-versioning)
and [governance](../spec/SPEC.md#10-governance).
