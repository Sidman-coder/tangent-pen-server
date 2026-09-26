# TANGENT pen upload server

A small Flask server that receives audio recordings from the TANGENT pen.
The pen POSTs a WAV file to `/upload` with a shared secret in the `X-Pen-Key`
header. Each accepted recording is saved and then transcribed by Deepgram in a
background thread, so the pen gets its `200` straight away.

| Endpoint | Auth | What it does |
|---|---|---|
| `POST /upload` | `X-Pen-Key` | Validates and saves a `recN.wav`, then queues transcription. `200` once saved, whatever happens with transcription. |
| `GET /recent` | `X-Pen-Key` | Last 10 transcription results, newest first. |
| `GET /health` | none | `{"status": "alive"}` |

## Environment variables

| Name | Required | Purpose |
|---|---|---|
| `PEN_KEY` | yes, the server won't start without it | Shared secret the pen sends in `X-Pen-Key` |
| `DEEPGRAM_API_KEY` | no | Deepgram key for transcription. If unset, the server logs a warning, still accepts uploads, and marks each result `skipped`. |

Generate a `PEN_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Put the same key in the pen firmware. Get a Deepgram key from
console.deepgram.com → API Keys. Never commit either; `.env` is gitignored and
`.env.example` shows the format.

## Transcription results

Next to each saved `received_recordings/<timestamp>_recN.wav` the server writes
`<timestamp>_recN.json`:

```json
{
  "original_filename": "rec1.wav",
  "saved_name": "20260926T162013Z_rec1.wav",
  "saved_at": "20260926T162013Z",
  "status": "ok",
  "transcript": "Remind me to call the dentist tomorrow at 3PM.",
  "confidence": 1.0
}
```

`status` is one of:

- `pending`: transcription is still running.
- `ok`: transcript found.
- `empty`: Deepgram heard no speech (e.g. silence). This is normal, not an error.
- `failed`: Deepgram error, timeout or network problem. See `error`.
- `skipped`: no `DEEPGRAM_API_KEY` is set.

This is temporary storage until results move to Supabase.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

Set the keys and start the server (it listens on port 5000):

```powershell
# PowerShell
$env:PEN_KEY = "your-key-here"
$env:DEEPGRAM_API_KEY = "your-deepgram-key"   # optional
python server.py
```

```bash
# macOS / Linux / Git Bash
PEN_KEY=your-key-here DEEPGRAM_API_KEY=your-deepgram-key python server.py
```

If Deepgram calls fail locally with `CERTIFICATE_VERIFY_FAILED`, antivirus
HTTPS scanning (e.g. Avast Web Shield) is usually re-signing traffic. Point
`REQUESTS_CA_BUNDLE` at a bundle that includes the antivirus root, or turn off
HTTPS scanning. This doesn't affect Railway.

## Test it

In a second terminal:

```bash
# one second of silence as rec1.wav -> expect Status 200, then status "empty"
python test_client.py http://localhost:5000 your-key-here

# a real recording (must be a 16kHz / 16-bit / mono WAV named recN.wav)
python test_client.py http://localhost:5000 your-key-here --file test_audio/rec1.wav
```

A wrong key gives `401`. Then check the transcripts:

```powershell
# PowerShell
Invoke-RestMethod http://localhost:5000/recent -Headers @{ "X-Pen-Key" = "your-key-here" }
```

```bash
# macOS / Linux / Git Bash
curl -H "X-Pen-Key: your-key-here" http://localhost:5000/recent
```

To make a spoken test file on Windows (`test_audio/*.wav` is gitignored):

```powershell
Add-Type -AssemblyName System.Speech
New-Item -ItemType Directory -Force test_audio | Out-Null
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile("$PWD\test_audio\rec1.wav", $fmt)
$synth.Speak("remind me to call the dentist tomorrow at three pm")
$synth.Dispose()
```

## Push to GitHub

```bash
git init
git add .
git commit -m "Pen upload server"
git branch -M main
git remote add origin https://github.com/<you>/tangent-pen-server.git
git push -u origin main
```

Check `git status` before committing. There should be no `.env` file,
`received_recordings/` folder or `test_audio/*.wav` in the list.

## Deploy to Railway

1. In Railway, create a new project and choose **Deploy from GitHub repo**, then
   pick this repository. Railway detects Python from `requirements.txt` and
   starts the server with the `Procfile`.
2. Open the service's **Variables** tab and add `PEN_KEY` with your generated
   key and `DEEPGRAM_API_KEY` with your Deepgram key. Railway redeploys when
   you save.
3. Under **Settings → Networking**, click **Generate Domain** to get a public URL.
4. Visit `https://<your-domain>/health` in a browser. You should see `{"status":"alive"}`.
5. Run the test client against the live server, then check `/recent`:

   ```bash
   python test_client.py https://<your-domain> your-key-here --file test_audio/rec1.wav
   curl -H "X-Pen-Key: your-key-here" https://<your-domain>/recent
   ```

### Why the Procfile uses one threaded worker

`gunicorn server:app --worker-class gthread --workers 1 --threads 4`

Transcription runs on a thread pool inside the gunicorn worker process, after
the response is sent. One `gthread` worker keeps a single process (and one
pool, capped at 2 concurrent Deepgram calls), with 4 threads so several pens
can upload at once. Don't add `--preload`: the pool must be created inside the
worker, not in the master before it forks.

Note: Railway's filesystem is temporary. Recordings and result JSON files in
`received_recordings/` are lost on each redeploy. A transcription still
running during a redeploy is lost too, and its result stays `pending`.
