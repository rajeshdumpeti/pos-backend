# Frontend handoff: environment-aware registration policy

This is the canonical Milestone 07-03A handoff for Authentication Contract
v1.1.0. The backend is the only authority for whether legal consent applies.

## Policy-first registration

Fetch `GET /api/v1/auth/registration-policy` before enabling registration. The
response is never cacheable and is one of these states:

```ts
type RegistrationPolicy =
  | {
      legal_consent_required: true;
      legal_policy_status: "active";
      terms_version: string;
      terms_url: string;
      privacy_version: string;
      privacy_url: string;
    }
  | {
      legal_consent_required: false;
      legal_policy_status: "deferred_nonproduction";
      terms_version: null;
      terms_url: null;
      privacy_version: null;
      privacy_url: null;
    };
```

When consent is required, display links to both backend-provided documents,
require explicit acceptance, and submit the returned versions unchanged. When
consent is deferred, omit the consent UI and all four legal request fields.
Never invent a version, acceptance, URL, or policy choice.

```ts
type RegistrationRequest = {
  display_name: string;
  email: string;
  password: string;
  terms_accepted?: true;
  privacy_accepted?: true;
  terms_version?: string;
  privacy_version?: string;
};
```

The four optional properties are conditionally all required or all omitted.
`legal_consent_required` maps to HTTP 400 when required acceptance is absent;
`legal_consent_not_applicable` maps to HTTP 400 when any legal property is sent
in deferred mode. `legal_acceptance_outdated` maps to HTTP 409 with
`refresh_registration_policy`; refetch and require a fresh decision.
`authentication_configuration_unavailable` maps to HTTP 503 and requires an
explicit retry without a hardcoded fallback.

## Verification continuation

Registration success remains HTTP 202 `PendingVerificationResponse`. Keep its
flow token only in transient memory, never in a URL, log, analytics, or durable
storage. Confirmation, resend, and change-email contracts are unchanged. A
replacement response supersedes the prior flow token immediately. Verification
does not establish a session.

Email-provider credentials and delivery are backend-only. Mobile calls only the
POS Authentication API. Deferred policy never bypasses email verification.

## Environment and release boundary

`deferred_nonproduction` is explicitly limited to local, development, and test
backend environments. Frontend builds never select or infer it. Staging and
production fail closed unless mode is `required` and approved non-placeholder
versions and public HTTPS document URLs are configured.

Before staging or production: publish and legally review the Terms and Privacy
documents, assign official versions, configure their approved HTTPS URLs, use
required mode, validate the protected environment, and complete App Store and
Google Play legal-readiness review.

## Local manual sequence

1. Configure a local database and the existing Authentication keys using local
   secret-management instructions.
2. Set `AUTH_LEGAL_CONSENT_MODE=deferred_nonproduction`; leave legal versions
   and URLs unset.
3. Start the API and fetch registration policy; confirm deferred status and null
   legal fields.
4. Register without legal fields and confirm the usual pending-verification
   response.
5. Inspect the encrypted local outbox only through the approved local CLI, then
   confirm, resend, and change email through their existing API contracts.
6. Check logs contain request IDs and safe categories only—never request bodies,
   email addresses, passwords, codes, flow tokens, or legal payloads.

Automated tests use network-free local delivery or mocked provider transport.
They never make live Resend calls.
