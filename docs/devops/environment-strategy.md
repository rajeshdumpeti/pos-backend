# Environment strategy

POS Backend recognizes four configuration contexts:

| Context     | Current mapping                                                    |
| ----------- | ------------------------------------------------------------------ |
| Local       | Developer-owned local configuration and the in-memory mock service |
| Development | Changes integrated on `develop`                                    |
| Staging     | Release candidates promoted to `staging`                           |
| Production  | Production-ready code represented by `main`                        |

No cloud service, real environment URL, production database, or production
deployment is configured. Hosting and runtime environment integration are
deferred to separately approved architecture and release work.

Environment-specific configuration must be supplied through ignored local files
or future approved secret-management systems. Safe example files may document
variable names without containing real credentials.
