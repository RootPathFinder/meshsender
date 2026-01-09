import time
import subprocess
import sys
import os
import argparse
from picamera2 import Picamera2

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Relative paths (can be customized as needed)
IMAGE_PATH = os.path.join(SCRIPT_DIR, "captured_image.jpg")
SENDER_SCRIPT = os.path.join(SCRIPT_DIR, "meshsender.py")

# Use the current Python interpreter
PYTHON_BIN = sys.executable

def capture_night_image():
    picam2 = Picamera2()
    
    # 1. Configure for Ultra-Low Light
    # We force a slow frame rate (1fps) to allow the long exposure
    config = picam2.create_preview_configuration(
        main={"format": "BGR888", "size": (2592, 1944)},
        controls={"FrameDurationLimits": (1000000, 1000000)} 
    )
    picam2.configure(config)
    picam2.start()

    # 2. Set Manual Gains and Shutter
    # ExposureTime in microseconds: 100000 = 100ms, 500000 = 0.5s
    picam2.set_controls({
        "AnalogueGain": 4.0,        # Reduced from 10.0 to avoid washout
        "ExposureTime": 200000,      # 200ms exposure (was 10ms)
        "AwbEnable": False,          # Manual white balance to fix blue tint
        "ColourGains": (1.2, 0.9),   # Original color balance
        "AeEnable": False,           # Disable auto exposure (manual control)
        "Brightness": 0.0,           # Neutral brightness
        "Contrast": 1.1              # Slight contrast boost
    })

    print("[*] Camera warming up for long exposure...")
    time.sleep(2) # Essential for the sensor to settle at 1fps
    
    print("[*] Capturing image...")
    picam2.capture_file(IMAGE_PATH)
    picam2.stop()
    print(f"[+] Image saved to {IMAGE_PATH}")

def send_to_mesh(target_node, res, qual):
    print(f"[*] Sending to Node: {target_node}")
    cmd = [
        PYTHON_BIN, SENDER_SCRIPT, "send", 
        target_node, IMAGE_PATH, 
        "--res", res, "--qual", qual
    ]
    
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("[+] Transmission finished successfully.")
    else:
        print("[X] Error: Meshtastic transmission failed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture and send image via Meshtastic")
    parser.add_argument("target_id", help="Target node ID (e.g., !da56b70c)")
    parser.add_argument("--res", default="720", help="Image resolution (default: 720)")
    parser.add_argument("--qual", default="70", help="JPEG quality (default: 70)")
    
    args = parser.parse_args()
    
    try:
        capture_night_image()
        send_to_mesh(args.target_id, args.res, args.qual)
    except Exception as e:
        print(f"[X] An error occurred: {e}")
