import base64
import json
import os
import socket
import subprocess
import sys
import threading
import time
import cv2
import asyncio
import edge_tts

# Firebase Admin SDK
import firebase_admin
from firebase_admin import credentials, firestore

from dotenv import load_dotenv
from gpiozero import Button, RotaryEncoder
from groq import Groq
from rapidocr_onnxruntime import RapidOCR

# ------------------------------------------------------------------------------
# Configuration & Hardware Pin Setup
# ------------------------------------------------------------------------------
CAPTURE_BUTTON_PIN = 25
ENCODER_A_PIN = 22
ENCODER_B_PIN = 23

# Audio / TTS Settings
VOLUME = 115  # make 100 for no noise
PIPER_MODEL_PATH = "en_US-lessac-medium.onnx"  # Direct path to model file
MPV_SOCKET = "/tmp/mpvsocket"
PROMPTS_DIR = "audio_prompts"

# Ensure cache folder exists
os.makedirs(PROMPTS_DIR, exist_ok=True)

# ------------------------------------------------------------------------------
# Initialization
# ------------------------------------------------------------------------------
load_dotenv()

print("Initializing hardware controls...")
capture_button = Button(CAPTURE_BUTTON_PIN)
encoder = RotaryEncoder(a=ENCODER_A_PIN, b=ENCODER_B_PIN, max_steps=0)

print("Initializing Firebase Admin SDK...")
try:
    cred = credentials.Certificate("firebase_key.json")
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("Firebase initialized successfully.")
except Exception as e:
    print(f"[!] Error initializing Firebase: {e}")
    sys.exit(1)

print("Initializing Groq API Client...")
groq_api_key = os.getenv("GROQ_API_KEY")
if not groq_api_key:
    print("[!] Warning: GROQ_API_KEY not found in environment or .env file.")
client = Groq(api_key=groq_api_key)

print("Loading RapidOCR engine...")
ocr_engine = RapidOCR()

current_speed = 1.0


# ------------------------------------------------------------------------------
# Helper Functions for Pre-recorded Prompts
# ------------------------------------------------------------------------------
def get_prompt_path(filename, text):
    """
    Checks if a prompt audio file exists. If not, generates and saves it via Piper.
    Returns the absolute path to the .wav file.
    """
    file_path = os.path.join(PROMPTS_DIR, filename)
    if not os.path.exists(file_path):
        print(f"[TTS Cache] Pre-generating prompt audio: '{filename}'...")
        subprocess.run(
            ["piper", "--model", PIPER_MODEL_PATH, "--output_file", file_path],
            input=text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return file_path


def speak_prompt(filename, text, wait=False):
    """Plays a cached instruction. Non-blocking by default unless wait=True."""
    file_path = get_prompt_path(filename, text)
    try:
        if wait:
            subprocess.run(
                ["mpv", f"--volume={VOLUME}", file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return None
        else:
            process = subprocess.Popen(
                ["mpv", f"--volume={VOLUME}", file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return process
    except Exception as e:
        print(f"[!] Error playing audio prompt: {e}")
        return None


def monitor_speed_encoder(stop_event):
    """Background thread to monitor the rotary encoder using a single persistent mpv socket connection."""
    global current_speed
    last_steps = encoder.steps

    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(MPV_SOCKET)
    except Exception as e:
        print(f"[MPV Error] Could not connect to IPC socket: {e}")
        return

    while not stop_event.is_set():
        change = encoder.steps - last_steps
        if change != 0:
            current_speed += change * 0.05
            current_speed = max(0.25, min(3.0, current_speed))
            current_speed = round(current_speed, 2)

            print(f"[Speed Adjustment] Current playback speed: {current_speed:.2f}x")

            cmd = {
                "command": [
                    "set_property",
                    "speed",
                    current_speed,
                ]
            }
            try:
                sock.send((json.dumps(cmd) + "\n").encode())
            except Exception as e:
                print(f"[MPV Error] Failed to send speed command: {e}")

            last_steps = encoder.steps

        time.sleep(0.05)

    if sock:
        try:
            sock.close()
        except Exception:
            pass


def wait_for_multi_press(timeout=1.0, max_presses=3, audio_process=None):
    """
    Waits for button press pattern after initial image trigger.
    If an audio prompt process is provided, it terminates the audio on the first button press.
    """
    capture_button.wait_for_release()

    press_count = 0
    last_press_time = None

    while True:
        if capture_button.is_pressed:
            if audio_process and audio_process.poll() is None:
                audio_process.terminate()

            press_count += 1
            last_press_time = time.time()
            capture_button.wait_for_release()

            if press_count >= max_presses:
                if audio_process and audio_process.poll() is None:
                    audio_process.terminate()
                return press_count

            time.sleep(0.05)

        if press_count > 0:
            if time.time() - last_press_time > timeout:
                return press_count

        time.sleep(0.01)


def save_scan_to_firestore(frame, corrected_text):
    """Converts image to Base64 and uploads image + text metadata to Firestore."""
    print("\n==================================================")
    print("Saving Scan to Google Firestore...")
    print("==================================================")

    try:
        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            print("[!] Error: Could not encode image frame to JPEG format.")
            return False

        base64_image = base64.b64encode(buffer).decode("utf-8")

        data = {
            "timestamp": firestore.SERVER_TIMESTAMP,
            "imageBase64": base64_image,
            "enhancedText": corrected_text,
        }

        doc_ref = db.collection("savedScans").add(data)
        print("[Success] Scan document successfully saved to Firestore!")
        print(f"Document ID: {doc_ref[1].id}")
        return True

    except Exception as e:
        print(f"[!] Error saving document to Firestore: {e}")
        return False


def play_audio():
    """Handles audio playback via mpv with play/pause button support."""
    print("\n==================================================")
    print("STEP 4: Playing Audio via mpv...")
    print("==================================================")

    target_audio = "output.mp3" if os.path.exists("output.mp3") else "output.wav"

    if os.path.exists(MPV_SOCKET):
        os.remove(MPV_SOCKET)

    mpv_process = subprocess.Popen(
        [
            "mpv",
            target_audio,
            f"--input-ipc-server={MPV_SOCKET}",
            f"--volume={VOLUME}",
            f"--speed={current_speed}",
        ]
    )

    while not os.path.exists(MPV_SOCKET):
        time.sleep(0.05)

    stop_speed_thread = threading.Event()
    speed_thread = threading.Thread(
        target=monitor_speed_encoder, args=(stop_speed_thread,), daemon=True
    )
    speed_thread.start()

    control_sock = None
    try:
        control_sock = socket.socket(socket.AF_UNIX)
        control_sock.connect(MPV_SOCKET)
    except Exception as e:
        print(f"[MPV Error] Could not connect IPC socket for play/pause: {e}")

    print(f"Playing '{target_audio}'... Press GPIO 25 button to Play/Pause. Turn knob to change speed.")

    try:
        capture_button.wait_for_release()

        while mpv_process.poll() is None:
            if capture_button.is_pressed:
                print("\n[Button Action]: Play/Pause Toggled")
                if control_sock:
                    cmd = {"command": ["cycle", "pause"]}
                    try:
                        control_sock.send((json.dumps(cmd) + "\n").encode())
                    except Exception as e:
                        print(f"[MPV Error] Failed to toggle play/pause: {e}")

                capture_button.wait_for_release()
                time.sleep(0.1)

            time.sleep(0.02)

    except KeyboardInterrupt:
        mpv_process.terminate()

    if control_sock:
        try:
            control_sock.close()
        except Exception:
            pass

    stop_speed_thread.set()
    speed_thread.join()
    if os.path.exists(MPV_SOCKET):
        os.remove(MPV_SOCKET)


# ------------------------------------------------------------------------------
# Asynchronous Background Pipeline
# ------------------------------------------------------------------------------
class BackgroundProcessor:
    def __init__(self):
        self.corrected_text = ""
        self.raw_ocr_text = ""
        self.is_ocr_done = threading.Event()
        self.is_correction_done = threading.Event()
        self.is_tts_done = threading.Event()
        self.has_text = False
        self.cancel_requested = False

    def process(self, frame):
        """Runs the entire pipeline sequentially in the background thread."""
        print("\n==================================================")
        print("STEP 1 [BG Thread]: Executing RapidOCR...")
        print("==================================================")

        ocr_output = ocr_engine(frame)

        if self.cancel_requested:
            return

        if not ocr_output or ocr_output[0] is None:
            print("[!] No text detected in the frame.")
            self.has_text = False
            self.is_ocr_done.set()
            self.is_correction_done.set()
            self.is_tts_done.set()
            return

        results, elapse_list = ocr_output
        extracted_lines = [text for _, text, _ in results]
        self.raw_ocr_text = "\n".join(extracted_lines)
        self.corrected_text = self.raw_ocr_text
        self.has_text = True

        # Print raw OCR text to terminal after Step 1
        print("\n[Raw OCR Extracted Text]:")
        print("-" * 50)
        for bbox, text, confidence in results:
            print(f"[{float(confidence):.2f}] {text}")
        print("-" * 50)

        self.is_ocr_done.set()

        if self.cancel_requested:
            return

        print("\n==================================================")
        print("STEP 2 [BG Thread]: Sending text to Groq for correction...")
        print("==================================================")

        system_instruction = (
            "You are an OCR correction tool for books. "
            "Fix only mistakes caused by OCR. "
            "OCR may occasionally skip, add, or misread letters, characters, punctuation, or spaces. "
            "Correct these errors when the intended word is clear. "
            "Do not rewrite, summarize, modernize, or change the author's wording or style. "
            "Preserve the original meaning and formatting as much as possible."
            "Your answer will directly be given to the user as output"
        )

        try:
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": self.raw_ocr_text},
                ],
                temperature=0.1,
                timeout=4.0,
            )
            if response.choices and response.choices[0].message.content:
                self.corrected_text = response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[!] Groq network/API error: {e}")

        # Print AI-corrected text to terminal after Step 2
        print("\n[Groq AI Corrected Text]:")
        print("-" * 50)
        print(self.corrected_text)
        print("-" * 50)

        self.is_correction_done.set()

        if self.cancel_requested:
            return

        print("\n==================================================")
        print("STEP 3 [BG Thread]: Synthesizing Audio via Edge/Piper TTS...")
        print("==================================================")

        for old_file in ["output.mp3", "output.wav"]:
            if os.path.exists(old_file):
                try:
                    os.remove(old_file)
                except Exception:
                    pass

        edge_success = False

        # Asynchronous helper function for Edge TTS
        async def _generate_edge_audio():
            communicate = edge_tts.Communicate(self.corrected_text, "en-US-AvaNeural")
            await communicate.save("output.mp3")

        # Create a clean dedicated event loop for this background thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            # Execute with a 15-second timeout window
            loop.run_until_complete(
                asyncio.wait_for(_generate_edge_audio(), timeout=15.0)
            )
            edge_success = True
            print("[Success] Audio synthesized natively via Edge TTS!")
        except Exception as e:
            print(f"[!] Edge TTS API Error/Timeout: {e}")
        finally:
            loop.close()

        # Fallback to local Piper TTS if Edge TTS fails or times out
        if not edge_success and not self.cancel_requested:
            print("Executing fallback: Piper TTS...")
            subprocess.run(
                ["piper", "--model", PIPER_MODEL_PATH, "--output_file", "output.wav"],
                input=self.corrected_text,
                text=True,
                capture_output=True,
            )

        self.is_tts_done.set()


# ------------------------------------------------------------------------------
# Main Application Loop
# ------------------------------------------------------------------------------
def main():
    print("\n==================================================")
    print(" System Ready")
    print(" Press [GPIO 25 Button] to capture")
    print(" Press [q] on active camera window to exit")
    print("==================================================\n")

    camera_ready_announced = False

    while True:
        # ----------------------------------------------------------------------
        # 1. Open Camera Stream & Apply 1080p @ 30 FPS Settings
        # ----------------------------------------------------------------------
        cap = cv2.VideoCapture(0)

        # Set MJPG codec format for high bandwidth / framerate support
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        # Set Resolution to 1080p (1920x1080)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        # Set Framerate to 30 FPS
        cap.set(cv2.CAP_PROP_FPS, 60)

        if not cap.isOpened():
            print("[!] Error: Could not open camera source.")
            sys.exit(1)

        captured_frame = None

        if not camera_ready_announced:
            speak_prompt("system_ready.wav", "System ready. Press button to capture.")
            camera_ready_announced = True

        # Live Camera Loop
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[!] Error: Failed to grab frame from camera.")
                break

            preview = frame.copy()
            cv2.putText(
                preview,
                "Press GPIO 25 Button to Capture | Press Q to Quit",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow("Webcam - Live Preview", preview)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("Exiting application...")
                cap.release()
                cv2.destroyAllWindows()
                return

            if capture_button.is_pressed:
                print("\n[Hardware Event] GPIO 25 Button Pressed!")
                captured_frame = frame.copy()
                break  # Exit preview loop to close camera

            time.sleep(0.01)

        # ----------------------------------------------------------------------
        # 2. Release Camera IMMEDIATELY after frame capture
        # ----------------------------------------------------------------------
        cap.release()
        cv2.destroyAllWindows()  # Close preview window during processing

        if captured_frame is None:
            continue

        # ----------------------------------------------------------------------
        # 3. Process Image in Background (Camera is completely OFF now)
        # ----------------------------------------------------------------------
        print("Starting background processing (Camera deactivated)...")
        processor = BackgroundProcessor()

        bg_thread = threading.Thread(target=processor.process, args=(captured_frame,))
        bg_thread.daemon = True
        bg_thread.start()

        has_read_once = False
        has_saved = False
        just_saved = False

        # Menu Interactions Loop
        while True:
            print("\n" + "=" * 70)
            read_prompt = (
                "Press the button once to read the text again."
                if has_read_once
                else "Press the button once to start reading the text."
            )
            print(f"- {read_prompt}")
            print("- Press button twice to exit and get ready to take new image.")
            if not has_saved:
                print("- Press button thrice to save the scan.")
            print("=" * 70 + "\n")

            if not has_saved:
                prompt_proc = speak_prompt(
                    "menu_full.wav",
                    "Press once to read. Press twice to exit. Press three times to save.",
                )
            elif just_saved:
                prompt_proc = speak_prompt(
                    "save_success.wav",
                    "Scan saved successfully. Press once to read. Press twice to exit.",
                )
                just_saved = False
            else:
                prompt_proc = speak_prompt(
                    "menu_saved.wav",
                    "Press once to read. Press twice to exit.",
                )

            max_allowed = 2 if has_saved else 3
            clicks = wait_for_multi_press(
                timeout=1.0,
                max_presses=max_allowed,
                audio_process=prompt_proc,
            )

            if clicks == 1:
                # Single Press: Read Audio
                if not processor.is_tts_done.is_set():
                    print("\n[Action Wait]: Audio not ready yet. Playing waiting message...")
                    speak_prompt("processing.wav", "Processing, please wait.", wait=True)
                    processor.is_tts_done.wait()

                if not processor.has_text:
                    print("[!] Aborting read: No text detected in image.")
                    speak_prompt("no_text.wav", "No text detected. Press button to scan new image.")
                    break

                print("\n[Button Action]: Single Press Detected -> Reading text aloud...")
                play_audio()
                has_read_once = True

            elif clicks == 2:
                # Double Press: Return to preview / camera state
                print("\n[Button Action]: Double Press Detected -> Aborting & ready for new scan.")
                processor.cancel_requested = True
                speak_prompt("exiting.wav", "Exited. Press button to capture.")
                break

            elif clicks >= 3:
                # Triple Press: Save Scan
                if not has_saved:
                    if not processor.is_correction_done.is_set():
                        print("\n[Action Wait]: Text correction not ready yet. Playing waiting message...")
                        speak_prompt("processing.wav", "Processing, please wait.", wait=True)
                        processor.is_correction_done.wait()

                    if not processor.has_text:
                        print("[!] Aborting save: No text detected in image.")
                        speak_prompt("no_text.wav", "No text detected. Press button to scan new image.")
                        break

                    print("\n[Button Action]: Triple Press Detected -> Saving scan to Firestore.")
                    if save_scan_to_firestore(captured_frame, processor.corrected_text):
                        has_saved = True
                        just_saved = True
                else:
                    print("\n[Notice]: This scan has alrecady been saved!")
                    speak_prompt("already_saved.wav", "Scan was already saved.")

        print("\nReady for next capture.\n")
        time.sleep(0.5)


if __name__ == "__main__":
    main()



