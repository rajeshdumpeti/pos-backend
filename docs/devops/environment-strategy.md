# Environment strategy

POS Backend recognizes five configuration contexts:
POS Backend recognizes four configuration contexts:

| Context     | Current mapping                                                    |
| ----------- | ------------------------------------------------------------------ |
| Local       | Developer-owned local configuration and the in-memory mock service |
| Development | Changes integrated on `develop`                                    |
| Test        | Isolated automated or local validation settings                     |
| Staging     | Release candidates promoted to `staging`                           |
| Production  | Production-ready code represented by `main`                        |

No cloud service, real environment URL, production database, or production
deployment is configured. Hosting and runtime environment integration are
deferred to separately approved architecture and release work.

Environment-specific configuration must be supplied through ignored local files
or future approved secret-management systems. Safe example files may document
variable names without containing real credentials.

Runtime environment names are normalized to lowercase and validated. Local,
development, and test may use the current wildcard CORS default. Staging and
production require explicit HTTP(S) origins, but no credentials, hosted runtime,
or deployment configuration exists for those environments yet.
