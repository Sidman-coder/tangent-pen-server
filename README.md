# TANGENT pen upload server

A small Flask server that receives audio recordings from the TANGENT pen.
The pen POSTs a WAV file to `/upload` with a shared secret in the `X-Pen-Key`
header. `GET /health` returns `{"status": "alive"}` so you can check the
server is up.

## Setting the shared secret

The server will not start without a `PEN_KEY` environment variable. Generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the same key in the pen firmware. Never commit it; `.env` is gitignored,
and `.env.example` shows the format.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Set `PEN_KEY` and start the server (it listens on port 5000):

```powershell
# PowerShell
$env:PEN_KEY = "your-key-here"
python server.py
```

```bash
# macOS / Linux / Git Bash
PEN_KEY=your-key-here python server.py
```

## Test it

In a second terminal:

```bash
python test_client.py http://localhost:5000 your-key-here
```

You should see `Status: 200`. A wrong key gives `401`.

## Push to GitHub

```bash
git init
git add .
git commit -m "Pen upload server"
git branch -M main
git remote add origin https://github.com/<you>/tangent-pen-server.git
git push -u origin main
```

Check `git status` before committing. There should be no `.env` file or
`received_recordings/` folder in the list.

## Deploy to Railway

1. In Railway, create a new project and choose **Deploy from GitHub repo**, then
   pick this repository. Railway detects Python from `requirements.txt` and
   starts the server with the `Procfile` (`gunicorn server:app`).
2. Open the service's **Variables** tab and add `PEN_KEY` with your generated
   key. Railway redeploys when you save it.
3. Under **Settings → Networking**, click **Generate Domain** to get a public URL.
4. Visit `https://<your-domain>/health` in a browser. You should see `{"status":"alive"}`.
5. Run the test client against the live server:

   ```bash
   python test_client.py https://<your-domain> your-key-here
   ```

Note: Railway's filesystem is temporary. Files in `received_recordings/` are
lost on each redeploy, so pass recordings on to transcription or storage
rather than keeping them there.
