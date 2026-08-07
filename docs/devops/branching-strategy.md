# Branching strategy

POS Backend uses a simple promotion path:

```text
feature/* or fix/*
        ↓
     develop
        ↓
     staging
        ↓
       main
```

- `feature/*` and `fix/*` contain bounded implementation work.
- `develop` integrates completed work for shared development validation.
- `staging` holds production-like release candidates.
- `main` represents production-ready code. Production deployment is not yet configured.

Changes move through pull requests and must pass `POS Backend CI / validate`.
The solo founder is not required to obtain an external approval. Force pushes
and deletion of shared branches should remain blocked when repository rules are
configured.
