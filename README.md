# Mobifliks Download URL Resolver

Professional resolver with:

- CLI mode for terminal workflows
- Web API + browser GUI mode for local/self-hosted usage
- Vercel deployment support (Python serverless)

## What it does

- Accepts a Mobifliks detail URL (`downloadvideo.php?...`).
- Extracts title, year, VJ name, and language from `vid_name`.
- Generates ranked direct-download URL candidates.
- Checks each candidate over HTTP and only accepts `200 OK`.

## Requirements

- Python 3.9+
- For web mode: dependencies in `requirements.txt`

## Usage

### 1) CLI usage

```bash
python3 mobifliks_url_resolver.py "https://www.mobifliks.com/downloadvideo.php?vid_id=10292&vid_name=Chappie%20(2015%20-%20VJ%20Junior%20-%20Luganda)&cat_id=4"
```

JSON output:

```bash
python3 mobifliks_url_resolver.py "<DETAIL_URL>" --json
```

Custom timeout:

```bash
python3 mobifliks_url_resolver.py "<DETAIL_URL>" --timeout 30
```

Retries per candidate:

```bash
python3 mobifliks_url_resolver.py "<DETAIL_URL>" --retries 2
```

### 2) Web API + GUI usage

Install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Start server:

```bash
.venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080 --reload
```

Open in browser:

- GUI: `http://localhost:8080/`
- Health: `http://localhost:8080/health`
- API docs: `http://localhost:8080/docs`

API example:

```bash
curl -X POST "http://localhost:8080/api/resolve" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://www.mobifliks.com/downloadvideo.php?vid_id=10292&vid_name=Chappie%20(2015%20-%20VJ%20Junior%20-%20Luganda)&cat_id=4","timeout":20,"retries":1}'
```

### 3) Deploy to Vercel

This project is configured for Vercel Python runtime using:

- `api/index.py` as serverless entrypoint
- `vercel.json` routing all paths to FastAPI

#### Steps

1. Push this folder to a GitHub repo.
2. Import the repo into Vercel.
3. Framework preset: **Other** (or auto-detect).
4. Root directory: repository root (this project folder).
5. Deploy.

After deploy:

- Home page: `https://<your-project>.vercel.app/`
- Health: `https://<your-project>.vercel.app/health`
- API docs: `https://<your-project>.vercel.app/docs`

#### Notes

- Vercel installs dependencies from `requirements.txt`.
- Keep `templates/` and `static/` in repo so the GUI renders correctly.
- Local development remains unchanged via `uvicorn app:app`.

## Exit codes

- `0`: Valid direct URL found (`200 OK`)
- `1`: No valid direct URL found
- `2`: Invalid input URL (missing/invalid `vid_name`)
