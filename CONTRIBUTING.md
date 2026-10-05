# Contributing to velib

## Setup

```bash
uv sync --locked --all-groups
source .venv/bin/activate
pre-commit install -t pre-commit -t commit-msg
git config commit.template "$PWD/.gitmessage"
```

## Workflow

1. Pick or create an issue.
2. Create a branch from the issue UI: `{issue_id}-{short-slug}`.
3. Implement, commit atomically (one logical task per commit).
4. Before opening a PR:
   ```bash
   git fetch && git rebase origin/main && git push -f
   pre-commit run --all-files
   make test
   ```
5. Open a PR, request a reviewer, tick the checklist.

## Commit messages -- Conventional Commits (enforced via `commit-msg` hook)

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`.

Subject rules: imperative present ("add" not "added"), lowercase first letter,
no trailing period, under 60 chars. Body: explain **why**. Footer: `Closes #123`
or `BREAKING CHANGE: ...`.

## Code conventions

- `from __future__ import annotations` at the top of every module.
- Google-style docstrings, no types inside them.
- 100-char line length, enforced by ruff.
- `pathlib` only, never `os.path`.
- `polars` over `pandas` for new code.
- `logging` / `loguru` / `rich`, never `print`.
- `pytest` with `tmp_path` fixtures; shared fixtures in `conftest.py`.
- No hardcoded hyperparameters or paths -- use hydra or env vars.

## Review checklist

Before marking a PR as ready:

- [ ] Commits are atomic and follow Conventional Commits.
- [ ] `make lint` passes locally.
- [ ] `make test` passes locally, new behavior has tests.
- [ ] Types everywhere that matters; `from __future__ import annotations`.
- [ ] Docstrings on public functions/classes/modules (Google style).
- [ ] No hardcoded paths or hyperparameters.
- [ ] No `print`, no `os.path`.
- [ ] README + CONTRIBUTING updated if behavior changed.
- [ ] `.env` is **not** staged.
- [ ] No leftover `TODO` without an owner and context.
