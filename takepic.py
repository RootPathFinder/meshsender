import time
import subprocess
from picamera2 import Picamera2

# --- CONFIGURATION ---
TARGET_NODE = "!da56b70c"
IMAGE_PATH = "/home/dave/small.jpg"
PYTHON_BIN = "/home/dave/mesh-env/bin/python"
SENDER_SCRIPT = "/home/dave/meshsender.py"
RES = "720"
QUAL = "70"

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
    # ExposureTime 1,000,000 = 1 full second
    picam2.set_controls({
        "AnalogueGain": 12.0,
       # "ExposureTime": 10000,
        "AwbEnable": False,
        "ColourGains": (1.2, 0.9)
    })

    print("[*] Camera warming up for long exposure...")
    time.sleep(2) # Essential for the sensor to settle at 1fps
    
    print("[*] Capturing image...")
    picam2.capture_file(IMAGE_PATH)
    picam2.stop()
    print(f"[+] Image saved to {IMAGE_PATH}")

def send_to_mesh():
    print(f"[*] Sending to Node: {TARGET_NODE}")
    cmd = [
        PYTHON_BIN, SENDER_SCRIPT, "send", 
        TARGET_NODE, IMAGE_PATH, 
        "--res", RES, "--qual", QUAL
    ]
    
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("[+] Transmission finished successfully.")
    else:
        print("[X] Error: Meshtastic transmission failed.")

if __name__ == "__main__":
    try:
        capture_night_image()
        send_to_mesh()
    except Exception as e:
        print(f"[X] An error occurred: {e}")
