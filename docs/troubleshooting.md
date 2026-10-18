# Troubleshooting

## The server does not start

- **`Configuration error: …`** — the message names the variable and the allowed values. Unset it to use the default.
- **`Address already in use`** — choose another port: `python -m sourcebound --port 8080`.
- **`ModuleNotFoundError: starlette`** (or uvicorn, pypdf) — activate your virtual environment and run `pip install -r requirements.txt`.

## Adding sources

| Message | Meaning and fix |
| --- | --- |
| Unsupported file type | Only PDF, TXT, Markdown (.md) and HTML are accepted. Export other formats to PDF or text. |
| This file has a .pdf name but is not a PDF | The file was renamed; export a real PDF. |
| No selectable text was found in this PDF | Scanned document. Run OCR first, e.g. `ocrmypdf in.pdf out.pdf`. |
| This PDF is password-protected | Remove the password and upload again. |
| Files must be under N MB / The request is too large | Raise `SOURCEBOUND_MAX_UPLOAD_MB` (max 50) or split the file. |
| You can keep up to N sources | Delete sources or raise `SOURCEBOUND_MAX_SOURCES`. |
| Links to private, local or reserved network addresses are not allowed | Intentional SSRF protection. Upload internal documents as files. |
| This website's robots.txt asks automated tools not to read that page | Respecting the site's wishes; save the page as PDF/HTML yourself if you have the right to. |
| Could not connect to … | The site is down, blocks the server's network, or the server needs an outbound proxy (not supported for URL fetching). |

## Research results

- **"No answer"** — the sources do not contain the question's key words. Rephrase with the terms your documents use, or add better sources. The agent log shows every query it tried.
- **A sentence seems off-topic** — extractive mode quotes the best-scoring sentences, which sometimes includes a neighbour of the right one. AI mode writes a focused answer.
- **A conflict that is not really a conflict** — the detector pairs figures whose surrounding words overlap. Check both quotes; the report never decides which source is right.
- **"A research run is already in progress"** — each workspace runs one question at a time. Wait for it to finish.
- **"Too many research runs in a minute"** — the per-workspace limit (`SOURCEBOUND_RUNS_PER_MINUTE`).

## AI model mode

- **The "Write with AI model" switch is missing** — set `SOURCEBOUND_LLM_PROVIDER=openai_compatible`, `SOURCEBOUND_LLM_BASE_URL` and `SOURCEBOUND_LLM_MODEL`, then restart.
- **Runs fall back to extractive** — open *How the agent worked*. It shows the provider error:
  - *rejected the API key (HTTP 401/403)* — check `SOURCEBOUND_LLM_API_KEY`;
  - *not found (HTTP 404)* — the base URL must end in `/v1` for most providers, and the model name must exist (`ollama list`);
  - *could not be reached* — the server cannot reach the provider; check the URL, firewall or that Ollama is running;
  - *did not pass citation checks* — the model wrote claims that its cited evidence does not support. Try a larger model.

## Browser issues

- **The page stays on "Checking engine…"** — JavaScript failed to load. Check the browser console; extensions that rewrite pages can conflict with the Content-Security-Policy.
- **Sources disappear after clearing site data** — the workspace key lives in `localStorage`. Clearing it starts a new, empty workspace (old data stays on the server until retention or manual deletion).
