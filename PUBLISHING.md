# Python package publishing preparation

No package is published by this repository today.

- The exact distribution name `file-organiser` appears unregistered, but it is not reserved until a successful first publication.
- The US-spelled PyPI project `file-organizer` is unrelated. Never tell users to install it.
- Do not put a PyPI token in the repository or a general Actions secret.

Before publishing:

1. Confirm the final distribution name on PyPI and TestPyPI.
2. Build with `python -m build` in CI and inspect wheel/sdist contents.
3. Install both artefacts in clean Python 3.9–3.12 environments and run smoke tests.
4. Create a protected GitHub environment named for PyPI publishing with required reviewer approval.
5. Configure PyPI Trusted Publishing for this repository, the exact workflow filename, and that environment.
6. Add a tag-only publish job using `pypa/gh-action-pypi-publish` with `id-token: write` and no password.
7. Publish to TestPyPI first, then reserve the production name with a reviewed release.
8. Only after a successful real release should documentation mention `pip install file-organiser`.

Publishing is an external, irreversible namespace action and is intentionally not automated by this commercial-upgrade branch.
