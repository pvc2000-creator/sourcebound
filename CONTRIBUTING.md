# Contributing to Sourcebound

Thanks for helping. Sourcebound's promise is simple: answers come only from the user's sources, and every claim can be checked. Changes should keep that promise and keep the free, no-API-key mode working.

## Getting set up

```bash
git clone https://github.com/pvc2000-creator/sourcebound.git
cd sourcebound
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium
npm install --global typescript@6.0.3
./scripts/check.sh
```

`./scripts/check.sh` runs formatting, lint, strict type checks (Python and the front end), the unit and integration suite and the browser tests. It must pass before a pull request is merged; CI runs the same checks.

## Where things live

| Path | What it does |
| --- | --- |
| `sourcebound/agent.py` | Planning, the act–evaluate–retry loop, conflict detection, writing and verification |
| `sourcebound/retrieval.py` | BM25 index and sentence scoring |
| `sourcebound/text.py` | Tokenising, sentence splitting, figure extraction, injection screen |
| `sourcebound/ingest.py` | File parsing and chunking |
| `sourcebound/netfetch.py` | SSRF-safe URL fetching |
| `sourcebound/llm.py` | OpenAI-compatible model adapter |
| `sourcebound/store.py` | SQLite persistence |
| `sourcebound/app.py` | HTTP API, security headers, rate limits |
| `sourcebound/static/` | The whole front end (no build step) |
| `tests/` | `unittest` suites; `tests/e2e/` holds Playwright browser tests |

See [docs/architecture.md](docs/architecture.md) for the design.

## Guidelines

- **Tests first for behaviour changes.** Agent changes should include a test that shows the improvement on a realistic question, and must not break the existing ones.
- **Never weaken a security control to make something work.** If a check is in the way, open an issue to discuss it.
- **Front end:** render data with `textContent` / DOM nodes only; no `innerHTML`, inline scripts or inline styles (the CSP forbids them). Keep `app.js` passing `tsc --checkJs` in strict mode.
- **Dependencies:** the runtime has four. Adding one needs a good reason; pin it in `requirements.txt`.
- **User-facing text:** plain, specific, sentence case. Errors say what went wrong and how to fix it.
- **Commits:** small and focused, with a message that explains *why*.

## Reporting bugs and ideas

Use the issue templates. Please remove private document text, API keys and workspace ids from logs. Security problems go through [SECURITY.md](SECURITY.md), not public issues.
