# Deployment

> Status: these instructions use the same commands that are tested locally and in CI, but no hosted deployment has been verified yet. If you deploy Sourcebound, please share what worked.

Sourcebound needs a host that runs a persistent Python web process and, to keep data across restarts, a writable disk. Serverless platforms that freeze processes between requests (or have no persistent disk) are a poor fit, because research runs execute in background threads and data lives in SQLite.

## Settings for any public instance

```bash
SOURCEBOUND_RETENTION_HOURS=24       # auto-delete old data
SOURCEBOUND_MAX_UPLOAD_MB=10
SOURCEBOUND_RUNS_PER_MINUTE=6
SOURCEBOUND_MAX_CONCURRENT_RUNS=4
```

Only set `SOURCEBOUND_LLM_*` variables if you are willing to pay for the model usage that visitors generate (or point it at a model you host). Leave `SOURCEBOUND_ALLOW_URL_FETCH=false` if the host's network policy forbids outbound requests.

Remember that workspace keys are not accounts: a shared instance is not a place for confidential documents.

## Render (web service)

1. Create a **Web Service** from your fork of the repository, runtime **Python 3**.
2. **Build command:** `pip install -r requirements.txt`
3. **Start command:** `python -m sourcebound --host 0.0.0.0` (Render sets `PORT`, which Sourcebound reads).
4. Add a **persistent disk** mounted at, for example, `/var/data`, and set `SOURCEBOUND_DB_PATH=/var/data/sourcebound.db`. Check Render's current plans: at the time of writing, persistent disks need a paid instance type. Without one, data resets whenever the service restarts or redeploys.
5. Set the environment variables above in the dashboard (never commit them).
6. After the deploy, open `/api/health` and run the sample library workflow.

## Linux VM with systemd

```bash
sudo useradd --system --create-home sourcebound
sudo -u sourcebound git clone https://github.com/pvc2000-creator/sourcebound.git /home/sourcebound/app
cd /home/sourcebound/app
sudo -u sourcebound python3 -m venv .venv
sudo -u sourcebound .venv/bin/pip install -r requirements.txt
```

`/etc/systemd/system/sourcebound.service`:

```ini
[Unit]
Description=Sourcebound
After=network.target

[Service]
User=sourcebound
WorkingDirectory=/home/sourcebound/app
Environment=SOURCEBOUND_DB_PATH=/home/sourcebound/data/sourcebound.db
Environment=SOURCEBOUND_RETENTION_HOURS=24
ExecStart=/home/sourcebound/app/.venv/bin/python -m sourcebound --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now sourcebound` and put a TLS-terminating reverse proxy (Caddy, nginx) in front of `127.0.0.1:8000`.

Run **one** process per database file: rate limits and the run queue live in memory.
