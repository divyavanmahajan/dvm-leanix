# Publishing to PyPI

This project publishes automatically to [PyPI](https://pypi.org/project/dvm-leanix/)
when a version tag (e.g. `v0.2.0`) is pushed to GitHub.
It uses **Trusted Publishing** (OIDC) — no PyPI API token needs to be stored as a secret.

---

## One-time setup

### 1. Configure PyPI Trusted Publisher

Do this once before the first release.

1. Log in to **https://pypi.org** (create an account if needed)
2. Go to **Your projects → dvm-leanix → Publishing** (or use the link below on first publish)  
   Direct URL: **https://pypi.org/manage/project/dvm-leanix/settings/publishing/**  
   If the project doesn't exist yet, go to **https://pypi.org/manage/account/publishing/**
3. Click **Add a new publisher** and fill in:

   | Field | Value |
   |-------|-------|
   | PyPI Project Name | `dvm-leanix` |
   | Owner | `divyavanmahajan` |
   | Repository name | `dvm-leanix` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

4. Click **Add**

### 2. Create the GitHub Actions environment

1. Go to **https://github.com/divyavanmahajan/dvm-leanix/settings/environments**
2. Click **New environment**, name it `pypi`
3. Optionally add a protection rule (e.g. require a reviewer before publishing)
4. Save

---

## Releasing a new version

### Step 1 — Bump the version

Edit `pyproject.toml`:

```toml
[project]
version = "0.2.0"
```

### Step 2 — Sync the lock file

```powershell
uv sync
```

### Step 3 — Commit the version bump

```powershell
git add pyproject.toml uv.lock
git commit -m "Bump version to 0.2.0"
git push
```

### Step 4 — Tag and push

```powershell
git tag v0.2.0
git push origin v0.2.0
```

Pushing the tag triggers the **Publish to PyPI** workflow automatically.

---

## What the workflow does

1. **Build** — runs `uv build` to produce a wheel (`.whl`) and source distribution (`.tar.gz`) in `dist/`
2. **Publish** — uploads both files to PyPI using OIDC (no password or token required)

You can monitor the run at:  
**https://github.com/divyavanmahajan/dvm-leanix/actions**

---

## Installing the published package

```powershell
uv add dvm-leanix
# or
pip install dvm-leanix
```

---

## Test publish (TestPyPI)

To verify packaging before a real release, publish to TestPyPI first:

1. Set up a Trusted Publisher on **https://test.pypi.org** using the same steps above  
   (use environment name `testpypi`)
2. Add a second job to the workflow or run manually:

```powershell
uv build
uv publish --publish-url https://test.pypi.org/legacy/ --token <your-testpypi-token>
```

Install from TestPyPI:

```powershell
pip install --index-url https://test.pypi.org/simple/ dvm-leanix
```
