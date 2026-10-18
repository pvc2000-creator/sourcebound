# Setup

## Requirements

- Python 3.11 or newer and `pip`

## Install and run

```bash
git clone https://github.com/pvc2000-creator/sourcebound.git
cd sourcebound
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m sourcebound
```

On **Windows PowerShell**, run one command per line (`&&` is not supported in Windows PowerShell 5) and call pip through Python:

```powershell
git clone https://github.com/pvc2000-creator/sourcebound.git
cd sourcebound
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m sourcebound
```

Open <http://127.0.0.1:8000>. The database is created at `data/sourcebound.db`.

## Configuration

Copy `.env.example` to `.env`, edit it, then load it into the shell before starting:

```bash
cp .env.example .env
set -a && . ./.env && set +a
python -m sourcebound
```

The full variable list is in the [README](../README.md#configuration). Sourcebound reads environment variables only; it does not load `.env` by itself.

## Using a local model with Ollama

```bash
ollama pull llama3.1
export SOURCEBOUND_LLM_PROVIDER=openai_compatible
export SOURCEBOUND_LLM_BASE_URL=http://localhost:11434/v1
export SOURCEBOUND_LLM_MODEL=llama3.1
python -m sourcebound
```

Smaller models follow the JSON format less reliably. When a draft fails verification, Sourcebound retries once and then falls back to quoting the evidence, so results stay trustworthy either way.

## Development tools

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
npm install --global typescript@6.0.3
./scripts/check.sh
```

Regenerate the README screenshots from a live server with:

```bash
python scripts/capture_screenshots.py
```
