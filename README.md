# LitAloud

A Raspberry Pi-based assistive reading device that turns any physical book into an audiobook — point the camera at a page, and LitAloud reads it aloud using OCR, AI correction, and text-to-speech.

## Setup
1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and add your Groq API key
3. Add your own `firebase_key.json` (Firebase service account credentials) to the project root
4. Download the Piper voice model into `models/` (see models/README.md)
5. Run: `python src/main.py`

## Hardware
- Raspberry Pi 4
- USB webcam
- Capture button (GPIO 25)
- Rotary encoder for speed control (GPIO 22/23)
