# Server-to-GitHub Backup and Local Synchronization

This is the repeatable workflow used on September 15-16, 2026. The production CyberPanel server was treated as the newest version, published to GitHub, and then synchronized to the local Windows checkout.

## Final Result

- The server code replaced the outdated GitHub `main` branch.
- A recovery branch remains available: `server-backup-2026-09-15`.
- The local checkout now matches commit `5a0be18`.
- `.env`, `db.sqlite3`, `venv`, uploaded data, and local documents were preserved locally.
- Django's system check passed.

## 1. Inspect the Repository

Always inspect the current state before changing branches or pulling:

```bash
git status --short --branch
git remote -v
git log --oneline --decorate -5
```

This identifies the current branch, remote, local changes, and whether the checkout is behind the server.

## 2. Fix the Server Git Ownership Warning

CyberPanel reported `detected dubious ownership` because the directory owner differed from the user running Git. Mark the deployment directory as trusted:

```bash
git config --global --add safe.directory /home/menardtrading.com/public_html/menardtrading
```

Then configure the branch and remote:

```bash
cd /home/menardtrading.com/public_html/menardtrading/
git branch -M main
git remote add origin git@github.com:issamwema8-code/menardtrading.git
```

If `origin` already exists, use:

```bash
git remote set-url origin git@github.com:issamwema8-code/menardtrading.git
```

## 3. Configure SSH Authentication

The first push failed with `Permission denied (publickey)`. Generate a server key and add only its public key to GitHub:

```bash
ssh-keygen -t ed25519 -C "menardtrading-production"
cat ~/.ssh/id_ed25519.pub
ssh -T git@github.com
```

The successful response is similar to:

```text
Hi issamwema8-code! You've successfully authenticated, but GitHub does not provide shell access.
```

Never upload or share `~/.ssh/id_ed25519`; only `id_ed25519.pub` belongs in GitHub.

## 4. Exclude Deployment-Only Files

The original push was rejected because it contained a 349 MB `menardprod.zip` and a large OpenCV binary inside `venv/`. These should not be committed. Add these rules to `.gitignore`:

```gitignore
.env
venv/
__pycache__/
*.pyc
db.sqlite3
media/
staticfiles/
*.log
menardprod.zip
```

`.gitignore` only affects future staging. It does not remove files already present in Git history.

## 5. Start a Clean Git History

Old Git history contained the large files, so the Git metadata was moved outside the project directory. This is critical: a backup `.git` directory left inside the project will itself be staged and uploaded.

```bash
cd /home/menardtrading.com/public_html/menardtrading/
mv .git-production-history ../menardtrading-git-history-backup-2026-09-16
mv .git ../menardtrading-git-clean-attempt-2026-09-16
git init
git branch -M main
git remote add origin git@github.com:issamwema8-code/menardtrading.git
```

Stage the current source and verify that forbidden paths are absent:

```bash
git add -A
git ls-files | grep -E '(^|/)(\.git|\.git-production-history|venv|media|staticfiles)/|menardprod\.zip|(^|/)\.env$'
```

The verification command should produce no output.

## 6. Remove the Exposed Brevo Secret

GitHub Push Protection detected a hardcoded Sendinblue/Brevo key in `menard_core/settings.py`. Replace the hardcoded value with an environment lookup:

```python
"PASSWORD": os.getenv("BREVO_SMTP_PASSWORD", ""),
```

Keep the real value only in the ignored server `.env` file:

```dotenv
BREVO_SMTP_PASSWORD=your-new-brevo-key
```

Check source files without searching `.env`:

```bash
grep -RIlE 'xsmtpsib-|xkeysib-' \
  --exclude-dir=.git \
  --exclude-dir=venv \
  --exclude=.env \
  .
```

The command should produce no output. Since the secret was already in the local commit, amend it:

```bash
git add menard_core/settings.py
git commit --amend --no-edit
git show HEAD:menard_core/settings.py | grep -nE 'xsmtpsib-|xkeysib-'
```

Revoke and replace any credential that was ever committed, even if GitHub rejected the push.

## 7. Push a Recovery Branch

Push the clean server snapshot to a separate branch first:

```bash
git push -u origin HEAD:server-backup-2026-09-15
```

Confirm the branch on GitHub before replacing `main`.

## 8. Replace GitHub `main`

If the first force push reports `stale info`, fetch the current remote state:

```bash
git fetch origin main
```

Then update `main` with the current server commit:

```bash
git push origin HEAD:main --force-with-lease
```

`--force-with-lease` is safer than plain `--force` because it refuses to overwrite a remote change that was not fetched.

## 9. Synchronize the Local Windows Checkout

Fetch the server-backed branch:

```powershell
git fetch origin main
git log --oneline --decorate -3 origin/main
git diff --stat HEAD..origin/main
```

Before resetting tracked files, copy local-only files to a backup directory. In this session the backup was:

```text
C:\Users\admin\Desktop\menardtrading-local-only-backup-2026-09-16
```

Reset tracked code to the server version:

```powershell
git reset --hard origin/main
```

Restore local-only items such as `content/`, Word documents, spreadsheets, and `.env.example` afterward. Keep workstation-only files out of the shared repository using `.git/info/exclude` when they should remain local.

The local `.env`, database, and virtual environment were checked afterward:

```powershell
Test-Path .env
Test-Path db.sqlite3
Test-Path venv
```

## 10. Validate the Result

Confirm that Git is clean and Django loads correctly:

```powershell
git status --short --branch
git diff --exit-code
git diff --cached --exit-code
.\venv\Scripts\python.exe manage.py check
```

The expected final status is:

```text
## main...origin/main
```

## Lessons

1. Decide which copy is authoritative before using `pull` or `push`.
2. Inspect `git status`, `git remote -v`, and `git log` first.
3. Do not commit `.env`, `venv`, databases, uploads, generated static files, or deployment archives.
4. `.gitignore` cannot remove secrets or large files from existing history.
5. Move old Git metadata outside the project before creating a clean repository.
6. Keep a backup branch before replacing a remote `main` branch.
7. Fetch before using `--force-with-lease`.
8. Rotate credentials that have been exposed.
9. Keep local application data separate from shared source code.

## Useful Diagnostics

```bash
git status --short --branch
git branch -vv
git remote -v
git log --oneline --decorate --all -10
git check-ignore -v path/to/file
git ls-files | grep -E '(^|/)(venv|media|staticfiles)/|\.env$|menardprod\.zip'
```
