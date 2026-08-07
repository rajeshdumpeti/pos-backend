# CI foundation

The `POS Backend CI` workflow validates pushes and pull requests involving
`main`, `develop`, or `staging`. It can also be started manually.

The single `validate` job uses Python 3.12, restores pip's package cache,
installs the pinned direct requirements, and runs:

```text
python -m pytest
python -m compileall app
python -c "from app.main import app"
```

The expected status-check name is `POS Backend CI / validate`. The workflow has
read-only repository-content permission, cancels outdated runs for the same
branch or pull request, and has a bounded timeout.

This workflow performs validation only. It does not deploy, build a container,
start cloud services, access secrets, or configure an environment.
