# Security & Data Governance

FinProof handles financial documents, so security is a design property,
not an afterthought. This summarizes the posture; the architectural rationale is
in [ARCHITECTURE.md §9](./ARCHITECTURE.md#9-security--data-governance).

## Secrets

- **Write-only API keys.** Provider keys are supplied from the dashboard, held
  only in the in-process runtime control plane, and **never serialized back out**.
  `GET /config` returns `keys_configured` booleans — never a key value. A key can
  be replaced, never read.
- **No keys in the environment by design.** The app does not read provider keys
  from env vars; an ambient `OPENAI_API_KEY` in the shell can never silently
  change what the agent does. The system boots to deterministic **mock mode** and
  stays there until an operator configures a provider through the UI.
- **No secrets in the repo or image.** `.env` is git-ignored and ops-only
  (thresholds, concurrency, retry policy, the optional admin token). The Docker
  image ships no credentials.

## Access control

- **`LEDGER_ADMIN_TOKEN`** (optional) gates the key-bearing control-plane writes
  (`PUT /config`, `POST /config/test`, `POST /providers/{id}/models`) via an
  `X-Admin-Token` header. It is a lightweight stand-in for the RBAC a production
  control plane would enforce; set it on any shared/public deployment.
- **Human-in-the-loop gate.** The `QUARANTINE → VERIFIED` transition requires an
  explicit human resolution. The system never auto-approves its own uncertainty.

## Data handling

- **Minimal egress.** The only outbound call is to the selected model provider,
  and only the document content needed for extraction is sent.
- **PII redaction before egress (F12).** Document *text* is scrubbed
  deterministically before it becomes a live-provider payload: account/card
  numbers (any ≥9-digit run), PAN, IFSC, Aadhaar-style groups, voter-ID EPICs,
  phone numbers, and emails are masked keeping only the last 4 characters.
  Amounts survive by construction. Controlled by `LEDGER_REDACT_PII`
  (default on).
- **Pixels cannot be masked — know what you're sending.** Images and scanned
  PDFs are sent to the configured provider **as-is** when a live provider is
  selected; redaction only applies to text. In **mock mode nothing ever leaves
  the process** — pixel-based documents are quarantined locally with a reason
  instead of being uploaded anywhere. Treat enabling a live provider as consent
  to send the uploaded documents to that vendor.
- **Traces carry derived fields, not raw documents.** Trace/AgentOps payloads hold
  computed values and evidence references (crop refs / source rows), not raw image
  bytes in cleartext.
- **Documents at rest.** Locally they stay on disk; the production path keeps
  blobs in S3 with SSE-KMS and short-lived presigned URLs, so the API never holds
  the raw document.
- **Money is exact.** Amounts are `Decimal` end-to-end — no float rounding on
  currency.

## Hardening posture

- Errors return structured JSON, never a bare stack trace (no internal detail
  leakage to the browser).
- A provider misconfiguration **degrades to mock** instead of erroring, so a bad
  key is a safe no-op, not an outage.
- Run state is bounded (capped `OrderedDict`s) so a long-lived server cannot grow
  unboundedly across many runs.

## Deployment notes

- Set `LEDGER_ADMIN_TOKEN` before exposing the dashboard publicly.
- Put the service behind HTTPS (the recommended free hosts terminate TLS for you).
- Prefer per-user provider keys over a shared one for any multi-tenant use.

## Reporting a vulnerability

This is a hiring-challenge project, not a production service. If you find a
security issue, please **open a private security advisory** on the GitHub
repository (Security → *Report a vulnerability*) rather than a public issue.
There is no formal SLA, but reports are appreciated and will be acknowledged.
