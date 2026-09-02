# Secrets management

Never commit database URLs, Redis URLs, JWT secrets, private API keys, signing
keys, certificates, service-account files, or external-service credentials.

Local secrets belong in ignored local environment files. Safe example files may
be committed only with non-sensitive placeholders. Future CI secrets belong in
scoped GitHub repository or Environment Secrets. Future hosting secrets belong
in the selected platform's secret manager after hosting is separately approved.

Runtime database URLs, Redis URLs, JWT secrets, registration HMAC keys, and
authentication outbox encryption keys are held in masked secret types and
excluded from settings representations. Authentication records persist only
key IDs/versions, HMAC digests, and authenticated ciphertext. Secrets and raw
verification material must not appear in logs,
exceptions, HTTP responses, OpenAPI output, snapshots, or diagnostic commands.
Validation errors identify only the affected variable and the required format.

Generate the challenge and outbox keys independently; never reuse either as a
database, JWT, or provider credential. Rotation requires a reviewed key-ring
reader for every still-live stored version before advancing the configured
writer version. Local verification output is deliberate ephemeral secret
material and must not be pasted into tickets or reports.

Every secret must have a documented owner, purpose, least-privilege scope, and
rotation plan. Avoid duplicating credentials across development, staging, and
production.

If a secret is exposed, stop distribution, revoke or rotate the credential, and
follow the approved incident-response process. Removing a value from the latest
commit alone does not invalidate an exposed credential.
