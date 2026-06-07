# Storage-less Social Video Downloader

This project is split for deployment:

- `public/` is the static frontend that Netlify can host.
- `main.py` is the FastAPI backend that must run on a Python-capable host.

Netlify cannot run this FastAPI + `yt-dlp` streaming backend directly. Netlify Functions currently support JavaScript, TypeScript, and Go, and streamed function responses are limited, which is not suitable for video proxying.

## Local Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Deploy Backend

Deploy the Python API to a backend host that supports long-running HTTP streaming, such as Render, Railway, Fly.io, Koyeb, a VPS, or a container host.

Use this start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set this optional environment variable on the backend:

```text
ALLOWED_ORIGINS=https://your-netlify-site.netlify.app
```

Use `*` during testing if you do not know the final frontend URL yet.

## Deploy Frontend To Netlify

1. Deploy this repository to Netlify.
2. Netlify will publish the `public` directory using `netlify.toml`.
3. In Netlify, set an environment variable:

```text
API_BASE_URL=https://your-fastapi-backend.example.com
```

4. Redeploy the Netlify site.

The frontend will call:

- `POST {API_BASE_URL}/extract`
- `GET {API_BASE_URL}/proxy-stream`

The old `Unexpected token '<', '<!DOCTYPE'...` error happens when the frontend calls a missing API path and Netlify returns `index.html`. The frontend now detects that situation and shows a clear configuration error instead.
