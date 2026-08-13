# LitAloud

 LitAloud is a project that turns text into speech. It uses a camera to capture a photo of text, OCR to extract text from the image, AI to correct any mistakes, and TTS to convert it to audio, all run on a raspberry pi. Here you can find the code we ran on the raspberry pi as well as the code we used to create the mobile app.

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
