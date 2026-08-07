# Security policy

POS Backend is pre-release local mock software. Security fixes are maintained on
the latest supported `main` revision.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include credentials,
private user data, or exploit details in public discussions. Report the issue
privately to the repository owner through an approved private GitHub channel.
Use GitHub private vulnerability reporting when it becomes available.

Include the affected revision, route or component, reproduction conditions,
likely impact, and a sanitized proof of concept when safe.

## Secret handling

Database URLs, Redis URLs, JWT secrets, private API keys, signing keys, and
service credentials must remain outside source control. Revoke and rotate any
credential that is accidentally exposed, then follow the approved
incident-response process.
