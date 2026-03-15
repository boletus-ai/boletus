# Contributing to Boletus

## Dev Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest
```

To run a specific test file:

```bash
pytest tests/test_example.py
```

## Commit Format

Prefix every commit message with a tag:

- `[feat]` -- new feature
- `[fix]` -- bug fix
- `[refactor]` -- code restructuring, no behavior change
- `[docs]` -- documentation only
- `[test]` -- adding or updating tests
- `[chore]` -- maintenance, deps, CI, etc.

Example: `[fix] resolve race condition in task manager`

## Pull Request Process

1. Create a feature branch off `dev`.
2. Keep PRs small and focused on a single change.
3. Ensure all tests pass (`pytest`).
4. Write a clear PR description explaining **what** and **why**.
5. Request review from at least one maintainer.
6. Squash-merge into `dev` once approved.
