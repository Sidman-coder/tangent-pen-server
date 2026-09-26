"""
Sends a test recording to the TANGENT pen upload server, the same way the
pen firmware does.

Usage:
    python test_client.py <server_url> <pen_key>

Example:
    python test_client.py http://localhost:5000 <your-key>
"""

import struct
import sys

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
    if len(sys.argv) != 3:
        print("Usage: python test_client.py <server_url> <pen_key>")
        sys.exit(1)

    url = sys.argv[1].rstrip("/") + "/upload"
    pen_key = sys.argv[2]

    response = requests.post(
        url,
        files={"file": ("rec1.wav", build_silent_wav(), "audio/wav")},
        headers={"X-Pen-Key": pen_key},
        timeout=30,
    )

    print(f"Status: {response.status_code}")
    print(f"Body:   {response.text}")


if __name__ == "__main__":
    main()
