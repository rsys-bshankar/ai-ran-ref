# Python requirements

Every service container (`smo/Dockerfile`) and every CI job installs its
third-party packages from the hashed, pinned files here — never from an
unpinned list — so a build is reproducible and a changed or tampered package
fails the hash check.

| File | Used by |
|---|---|
| `runtime.in` / `runtime.txt` | service images, the migration check |
| `dev.in` / `dev.txt` | runtime + pytest: unit, integration and live-replay jobs |
| `lint.in` / `lint.txt` | the `lint` job (ruff) |

The `.in` files hold the direct dependencies; the `.txt` files are generated and
must not be edited by hand. Dependabot updates the `.txt` files weekly (its pip
ecosystem understands `pip-compile` output), CI runs on the result, and the
`smo/shared` library is installed with `--no-deps` on top.

Recompile after editing a `.in` file (Python 3.11, the oldest supported):

```bash
pip install pip-tools
cd smo/requirements
pip-compile --generate-hashes --strip-extras --allow-unsafe -o runtime.txt runtime.in
pip-compile --generate-hashes --strip-extras --allow-unsafe -o dev.txt dev.in
pip-compile --generate-hashes --strip-extras --allow-unsafe -o lint.txt lint.in
```

Install locally: `pip install --require-hashes -r requirements/dev.txt && pip install --no-deps -e shared`.
