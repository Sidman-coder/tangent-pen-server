"""
TANGENT pen upload server.

Accepts audio recordings POSTed by the pen firmware, checks a shared-secret
header so random internet traffic can't hit the endpoint, validates the
file matches the agreed audio spec, saves it, and hands it to
Deepgram for transcription in a background thread.

Matches the spec from the hardware side:
  - POST /upload, multipart/form-data
  - one file field named "file"
  - filename pattern recN.wav (e.g. rec3.wav)
  - content-type audio/wav
  - audio itself: 16kHz, 16-bit, mono, standard 44-byte WAV header
  - responds 200 on success, non-200 on failure (firmware only checks status code)
"""

import glob
import json
import logging
import os
import re
import struct
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import requests
from flask import Flask, request, jsonify

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("tangent-pen")

app = Flask(__name__)

UPLOAD_FOLDER = "received_recordings"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# The shared secret the pen firmware must send back in the `X-Pen-Key` header.
# This is read from an environment variable — NEVER hardcode it in this file
# or commit it to git. See README.md for how to generate and set it.
PEN_KEY = os.environ.get("PEN_KEY")

if not PEN_KEY:
    raise RuntimeError(
        "PEN_KEY environment variable is not set. Generate one and set it "
        "in your deploy platform's environment variables before starting "
        "the server — see README.md, section 'Setting the shared secret'."
    )

FILENAME_PATTERN = re.compile(r"^rec\d+\.wav$")

# Deepgram key for transcription. Unlike PEN_KEY this is optional: without it
# the server still accepts and saves uploads, it just skips transcription.
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY")
DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
DEEPGRAM_PARAMS = {"model": "nova-3", "smart_format": "true"}
DEEPGRAM_TIMEOUT_SECONDS = 30

if not DEEPGRAM_API_KEY:
    log.warning(
        "DEEPGRAM_API_KEY is not set. Uploads will still be accepted and "
        "saved, but transcription will be skipped."
    )

# Transcription runs here, off the request thread, so /upload returns 200 as
# soon as the file is saved. Created at import time inside each gunicorn
# worker (no --preload), so the threads belong to the process serving requests.
transcription_pool = ThreadPoolExecutor(max_workers=2)

RECENT_LIMIT = 10


def is_authorized() -> bool:
    pen_key = request.headers.get("X-Pen-Key")
    return bool(pen_key) and pen_key == PEN_KEY


def is_valid_wav_header(file_bytes: bytes) -> bool:
    """
    Confirms this is a standard 44-byte PCM WAV header describing
    16kHz / 16-bit / mono audio, per the agreed spec.
    Returns False (rather than raising) on anything malformed or short.
    """
    if len(file_bytes) < 44:
        return False

    try:
        riff, _riff_size, wave = struct.unpack("<4sI4s", file_bytes[0:12])
        if riff != b"RIFF" or wave != b"WAVE":
            return False

        # The "fmt " sub-chunk. Standard 44-byte header has it starting at byte 12
        # and the fields we care about at fixed offsets within it.
        fmt_id = file_bytes[12:16]
        if fmt_id != b"fmt ":
            return False

        (
            _audio_format,
            channels,
            sample_rate,
            _byte_rate,
            _block_align,
            bits_per_sample,
        ) = struct.unpack("<HHIIHH", file_bytes[20:36])

        return channels == 1 and sample_rate == 16000 and bits_per_sample == 16
    except struct.error:
        return False


def write_result(result_path: str, result: dict) -> None:
    """Write via a temp file + rename so /recent never reads a half-written file."""
    tmp_path = result_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as out:
        json.dump(result, out, indent=2)
    os.replace(tmp_path, result_path)


def call_deepgram(file_bytes: bytes) -> dict:
    """
    Sends the WAV to Deepgram and returns the status/transcript/confidence/error
    fields for the result record. Never raises and never logs the API key.
    """
    try:
        response = requests.post(
            DEEPGRAM_URL,
            params=DEEPGRAM_PARAMS,
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "audio/wav",
            },
            data=file_bytes,
            timeout=DEEPGRAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return {"status": "failed", "error": f"request error: {type(e).__name__}: {e}"}

    if response.status_code != 200:
        return {
            "status": "failed",
            "error": f"Deepgram returned {response.status_code}: {response.text[:500]}",
        }

    try:
        alternative = response.json()["results"]["channels"][0]["alternatives"][0]
        transcript = alternative["transcript"]
        confidence = alternative.get("confidence")
    except (ValueError, KeyError, IndexError, TypeError) as e:
        return {
            "status": "failed",
            "error": f"unexpected Deepgram response ({type(e).__name__}): {response.text[:500]}",
        }

    return {
        "status": "ok" if transcript.strip() else "empty",
        "transcript": transcript,
        "confidence": confidence,
    }


def transcribe(file_bytes: bytes, result_path: str, result: dict) -> None:
    """Runs on the transcription pool. Updates the result JSON when done."""
    try:
        if not DEEPGRAM_API_KEY:
            result["status"] = "skipped"
            log.info("Transcription skipped for %s: DEEPGRAM_API_KEY not set", result["saved_name"])
        else:
            result.update(call_deepgram(file_bytes))
            if result["status"] == "ok":
                log.info(
                    "Transcribed %s (confidence %s): %s",
                    result["saved_name"], result["confidence"], result["transcript"],
                )
            elif result["status"] == "empty":
                log.info("Empty transcript for %s (no speech detected)", result["saved_name"])
            else:
                log.error("Transcription failed for %s: %s", result["saved_name"], result["error"])
    except Exception as e:  # keep the worker thread alive whatever happens
        result["status"] = "failed"
        result["error"] = f"internal error: {type(e).__name__}: {e}"
        log.exception("Transcription crashed for %s", result["saved_name"])

    write_result(result_path, result)


@app.route("/upload", methods=["POST"])
def upload():
    # 1. Auth check first, before touching the file at all.
    if not is_authorized():
        return jsonify(error="unauthorized"), 401

    # 2. File presence check.
    if "file" not in request.files:
        return jsonify(error="no file field named 'file'"), 400

    f = request.files["file"]

    # 3. Filename pattern check.
    if not f.filename or not FILENAME_PATTERN.match(f.filename):
        return jsonify(error="filename must match recN.wav, e.g. rec3.wav"), 400

    # 4. Read the bytes once so we can validate the WAV header and then save it.
    file_bytes = f.read()

    if not is_valid_wav_header(file_bytes):
        return (
            jsonify(error="file is not a 16kHz / 16-bit / mono WAV file"),
            400,
        )

    # 5. Save it. Prefix with a UTC timestamp so re-used filenames
    #    (every pen reusing rec1.wav, rec2.wav...) never collide.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    saved_name = f"{timestamp}_{f.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, saved_name)
    with open(filepath, "wb") as out:
        out.write(file_bytes)

    log.info("Received: %s (%d bytes)", saved_name, len(file_bytes))

    # 6. Transcribe in the background. The pen retries until it gets a 200,
    #    so a Deepgram problem must never change this response, or the pen
    #    re-uploads the same recording. The result JSON starts as "pending"
    #    and is overwritten when transcription finishes.
    result_path = os.path.splitext(filepath)[0] + ".json"
    result = {
        "original_filename": f.filename,
        "saved_name": saved_name,
        "saved_at": timestamp,
        "status": "pending",
        "transcript": None,
        "confidence": None,
    }
    try:
        write_result(result_path, result)
        transcription_pool.submit(transcribe, file_bytes, result_path, result)
    except Exception:
        log.exception("Could not queue transcription for %s", saved_name)

    return jsonify(status="ok"), 200


@app.route("/recent", methods=["GET"])
def recent():
    """The last 10 transcription results, newest first. Same X-Pen-Key auth."""
    if not is_authorized():
        return jsonify(error="unauthorized"), 401

    # Saved names start with a UTC timestamp, so name order is time order.
    paths = sorted(glob.glob(os.path.join(UPLOAD_FOLDER, "*.json")), reverse=True)
    results = []
    for path in paths[:RECENT_LIMIT]:
        try:
            with open(path, encoding="utf-8") as fh:
                results.append(json.load(fh))
        except (OSError, ValueError):
            log.warning("Could not read result file %s", path)
    return jsonify(results), 200


@app.route("/health", methods=["GET"])
def health():
    """Simple endpoint to confirm the server is up — hit this in a browser
    after deploying to sanity-check before testing with real hardware."""
    return jsonify(status="alive"), 200


if __name__ == "__main__":
    # Railway (and most PaaS platforms) inject the port to bind to via $PORT.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
