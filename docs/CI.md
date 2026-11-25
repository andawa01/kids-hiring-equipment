# CI & Git Workflow

This repository is the **backend** of PartyPlay. The frontend lives in a separate
repository and consumes the API contract (see "Frontend contract" below).

We use **GitHub Flow**: short-lived branches → pull request → CI green → merge to `main`.

---

## 1. One-time bootstrap

CI cannot run until the repository exists on GitHub. Prepare everything locally,
push once, let the workflow report, **then** turn on branch protection.

```bash
cd ~/projects/partyplay-backend

# 1. Initialise and review what will be committed
git init -b main
git add -A
git status                      # ⚠️ backend/.env must NOT appear

# 2. Prove secrets are ignored
git check-ignore -v backend/.env          # should print a match
git status --porcelain | grep -i env || true

# 3. First commit
git commit -m "chore: initial backend import"

# 4. Create the private GitHub repo and push
gh repo create andawa01/partyplay-backend --private --source=. --remote=origin --push
# or, if the repo already exists:
# git remote add origin git@github.com:andawa01/partyplay-backend.git
# git push -u origin main
```

The first push runs `CI` on `main` (lint + test). Wait for it to pass — this is
what registers the `lint` and `test` check names with GitHub.

---

## 2. Protect `main` (after the first green run)

**Settings → Rules → New branch ruleset** (or *Branches → Add branch protection rule*):

- ✅ Require a pull request before merging
  - Required approvals: `1` (use `0` for a solo repo, but keep the PR requirement)
- ✅ Require status checks to pass before merging
  - Select **`lint`** and **`test`**
  - ✅ Require branches to be up to date before merging
- ✅ Block force pushes
- ✅ Require linear history (if you squash-merge)
- ✅ Automatically delete head branches

> If the check names are missing from the dropdown, the workflow has not reported
> yet. Push a commit or open a PR, wait for a run, then configure protection.

CLI equivalent (after the first run):

```bash
gh api -X PUT repos/andawa01/partyplay-backend/branches/main/protection \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=lint' \
  -f 'required_status_checks[contexts][]=test' \
  -f 'enforce_admins=false' \
  -f 'required_pull_request_reviews[required_approving_review_count]=1' \
  -F 'restrictions='
```

---

## 3. Daily flow

```bash
git switch main && git pull
git switch -c feat/short-description

# ... make changes ...

cd backend
.venv/bin/ruff check .
.venv/bin/pytest

cd ..
git add -A
git commit -m "feat: short description"
git push -u origin feat/short-description
gh pr create --fill
```

Merge only when `lint` and `test` are green. Prefer **squash merge** and delete
the branch.

---

## 4. What CI checks

| Job | What it does | Why |
|---|---|---|
| `lint` | `ruff check .` | Catches unused imports, import order, real bugs (bugbear), pyupgrade |
| `test` | `alembic upgrade head` → `alembic check` → `pytest` | Runs the real schema via migrations, fails on model/migration drift, then runs the suite against a real PostgreSQL 16 |

Key detail: tests **do not** build the schema with `create_all`; CI applies the
migrations first, so migration drift is caught (see gap `TEST-1` in
[`GAP_ANALYSIS.md`](./GAP_ANALYSIS.md)).

---

## 5. Run CI locally

```bash
cd backend
.venv/bin/ruff check .

# Tests need PostgreSQL on localhost:5434 (see docker-compose.yml) and the
# TEST_DATABASE_URL in backend/.env.
docker compose -f ../docker-compose.yml up -d db
.venv/bin/alembic upgrade head
.venv/bin/pytest
```

---

## 6. Secret hygiene (before every first push and on every PR)

```bash
# 1. Nothing secret is staged
git diff --cached --name-only | grep -E '\.env$|\.pem$|\.key$' && echo "STOP: secret staged"

# 2. Ignore rules are working
git check-ignore -v backend/.env

# 3. Optional: scan the history
gitleaks detect --source . --no-banner
```

`.env` and `.env.*` are ignored (except `.env.example`). The dev `JWT_SECRET`
that was shared during setup must be **rotated** before any deployment:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## 7. Frontend contract (separate repo)

The API is the source of truth. FastAPI exposes the spec at `/openapi.json`.

- Backend CI can upload `openapi.json` as an artifact (add later).
- The frontend generates a typed client (`openapi-typescript` / `orval`) from it.
- All routes stay under `/api/v1`; breaking changes require a version bump.

This gives independent repositories without losing type safety across the boundary.

---

## 8. Suggested next CI increments

Add these as separate PRs so the pipeline stays green:

1. **Coverage gate** — `pytest --cov=app --cov-fail-under=70`.
2. **Type checking** — `mypy app` (start permissive, tighten over time).
3. **Secret scanning** — `gitleaks` on PRs.
4. **Dependency audit** — `pip-audit`.
5. **Build check** — `docker build` once the app Dockerfile exists (gap `OPS-3`).
