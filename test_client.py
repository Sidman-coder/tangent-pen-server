"""
Sends a test recording to the TANGENT pen upload server, the same way the
pen firmware does.

Usage:
    python test_client.py <server_url> <pen_key> [--file path/to/recN.wav]

Examples:
    python test_client.py http://localhost:5000 <your-key>
    python test_client.py http://localhost:5000 <your-key> --file test_audio/rec1.wav
"""

import argparse
import os
import struct

import requests

SAMPLE_RATE = 16000
BITS_PER_SAMPLE = 16
CHANNELS = 1
DURATION_SECONDS = 1


def build_silent_wav() -> bytes:
    """One second of 16kHz / 16-bit / mono silence with a standard 44-byte header."""
    block_align = CHANNELS * BITS_PER_SAMPLE // 8
    byte_rate = SAMPLE_RATE * block_align
    data = b"\x00" * (byte_rate * DURATION_SECONDS)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(data),
        b"WAVE",
        b"fmt ",
        16,  # fmt chunk size for PCM
        1,  # audio format: PCM
        CHANNELS,
        SAMPLE_RATE,
        byte_rate,
        block_align,
        BITS_PER_SAMPLE,
        b"data",
        len(data),
    )
    assert len(header) == 44
    return header + data


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a test recording to the pen server.")
    parser.add_argument("server_url")
    parser.add_argument("pen_key")
    parser.add_argument(
        "--file",
        help="upload this WAV instead of generated silence; sent under its own "
        "filename, so it must be named recN.wav",
    )
    args = parser.parse_args()

    if args.file:
        with open(args.file, "rb") as fh:
            upload = (os.path.basename(args.file), fh.read(), "audio/wav")
    else:
        upload = ("rec1.wav", build_silent_wav(), "audio/wav")

    response = requests.post(
        args.server_url.rstrip("/") + "/upload",
        files={"file": upload},
        headers={"X-Pen-Key": args.pen_key},
        timeout=30,
    )

    print(f"Status: {response.status_code}")
    print(f"Body:   {response.text}")


if __name__ == "__main__":
    main()
