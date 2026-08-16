# Secrets management

Never commit database URLs, Redis URLs, JWT secrets, private API keys, signing
keys, certificates, service-account files, or external-service credentials.

Local secrets belong in ignored local environment files. Safe example files may
be committed only with non-sensitive placeholders. Future CI secrets belong in
scoped GitHub repository or Environment Secrets. Future hosting secrets belong
in the selected platform's secret manager after hosting is separately approved.

Runtime database URLs, Redis URLs, and JWT secrets are held in masked secret
types and excluded from settings representations. They must not appear in logs,
exceptions, HTTP responses, OpenAPI output, snapshots, or diagnostic commands.
Validation errors identify only the affected variable and the required format.

Every secret must have a documented owner, purpose, least-privilege scope, and
rotation plan. Avoid duplicating credentials across development, staging, and
production.

If a secret is exposed, stop distribution, revoke or rotate the credential, and
follow the approved incident-response process. Removing a value from the latest
commit alone does not invalidate an exposed credential.
