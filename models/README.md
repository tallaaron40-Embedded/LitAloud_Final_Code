# Voice Model

LitAloud uses Piper's `en_US-lessac-medium` voice for offline text-to-speech fallback (used when the edge-tts network call fails or times out).

## Setup

1. Download both files from the [Piper voices repo](https://github.com/rhasspy/piper/blob/master/VOICES.md):
   - `en_US-lessac-medium.onnx`
   - `en_US-lessac-medium.onnx.json`

   Direct download links:
   - https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
   - https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

2. Place both files directly in this `models/` folder.

3. Confirm `PIPER_MODEL_PATH` in `src/main.py` (or `config.py`) points to the correct path.

> These files are intentionally excluded from git via `.gitignore` — they're too large to track and are easy to re-download.
