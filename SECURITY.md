# Security policy

## Reporting a vulnerability

Please **do not** open a public issue. Report it privately through GitHub: **Security → Report a vulnerability** on this repository ([direct link](https://github.com/pvc2000-creator/sourcebound/security/advisories/new)).

If that option is not shown, open a public issue that only asks for a private contact (no details), and the maintainer will reach out.

Include what you found, how to reproduce it, and the impact you expect. You will get an acknowledgement as soon as the maintainer sees it; fixes for confirmed issues are released as quickly as possible and credited unless you prefer otherwise.

## Supported versions

Only the latest release on `main` receives security fixes.

## Threat model in brief

Sourcebound treats everything that comes from documents, web pages and AI models as untrusted data:

| Risk | Defence |
| --- | --- |
| Cross-site scripting | All rendering via DOM text nodes; strict Content-Security-Policy with no inline scripts or styles; Markdown exports escape markup |
| Prompt injection | Sources screened and flagged; evidence delimited and `<`/`>` neutralised; model output schema-validated, never executed, verified sentence by sentence |
| Server-side request forgery | http/https on 80/443 only, no URL credentials, every resolved address must be public, connection pinned to the validated IP, redirects re-validated, size/time/type caps, robots.txt honoured |
| Unsafe uploads / path traversal | In-memory parsing, extension and magic-byte checks, size and page limits, client file names reduced to display titles |
| SQL injection | Parameterised queries; ids validated by pattern |
| Resource exhaustion | Body size cap, per-workspace rate limits, one active run per workspace, server-wide concurrency and queue limits, per-run time/tool/model budgets |
| Secret leakage | API key only from the environment; never logged, returned or included in errors |

### Known limitation

Workspaces are identified by a random 128-bit key stored in the browser. This scopes data but is not authentication: anyone with the key can read that workspace. Do not run a shared public instance for confidential documents.
