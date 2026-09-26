"""
TANGENT pen upload server.

Accepts audio recordings POSTed by the pen firmware, checks a shared-secret
header so random internet traffic can't hit the endpoint, validates the
file matches the agreed audio spec, saves it, and (eventually) hands it to
the speech-to-text pipeline.

Matches the spec from the hardware side:
  - POST /upload, multipart/form-data
  - one file field named "file"
  - filename pattern recN.wav (e.g. rec3.wav)
  - content-type audio/wav
  - audio itself: 16kHz, 16-bit, mono, standard 44-byte WAV header
  - responds 200 on success, non-200 on failure (firmware only checks status code)
"""

import os
import re
import struct
from datetime import datetime, timezone

from flask import Flask, request, jsonify

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


@app.route("/upload", methods=["POST"])
def upload():
    # 1. Auth check first, before touching the file at all.
    pen_key = request.headers.get("X-Pen-Key")
    if not pen_key or pen_key != PEN_KEY:
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

    print(f"Received: {saved_name} ({len(file_bytes)} bytes)")

    # TODO: hand `filepath` (or file_bytes directly) to the Deepgram
    # transcription step here. For a first version, doing this inline is
    # fine; once it's slow enough to matter, move it to a background queue
    # (e.g. Celery, RQ, or a simple thread) so this request returns fast
    # and the pen isn't left waiting on a slow network round trip.

    return jsonify(status="ok"), 200


@app.route("/health", methods=["GET"])
def health():
    """Simple endpoint to confirm the server is up — hit this in a browser
    after deploying to sanity-check before testing with real hardware."""
    return jsonify(status="alive"), 200


if __name__ == "__main__":
    # Railway (and most PaaS platforms) inject the port to bind to via $PORT.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
