import time
import subprocess
import sys
import os
import argparse
import numpy as np
from picamera2 import Picamera2

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Relative paths (can be customized as needed)
IMAGE_PATH = os.path.join(SCRIPT_DIR, "captured_image.jpg")
SENDER_SCRIPT = os.path.join(SCRIPT_DIR, "meshsender.py")

# Use the current Python interpreter
PYTHON_BIN = sys.executable

def auto_adjust_exposure(picam2, target_brightness=90, max_iterations=5):
    """
    Automatically adjust exposure and gain based on preview images.
    Target brightness: 0-255 (90 is good for night vision)
    """
    exposure = 200000  # Start with 200ms
    gain = 4.0
    
    print("[*] Auto-adjusting exposure...")
    
    for i in range(max_iterations):
        # Set current settings
        picam2.set_controls({
            "AnalogueGain": gain,
            "ExposureTime": exposure,
            "AwbEnable": False,
            "AeEnable": False
        })
        
        time.sleep(0.5)  # Let camera adjust
        
        # Capture preview
        preview = picam2.capture_array()
        
        # Calculate mean brightness (grayscale)
        gray = np.mean(preview, axis=2)  # Average RGB channels
        mean_brightness = np.mean(gray)
        
        print(f"  Iteration {i+1}: Brightness={mean_brightness:.1f}, Exposure={exposure/1000:.1f}ms, Gain={gain:.1f}")
        
        # Check if we're close enough
        if abs(mean_brightness - target_brightness) < 15:
            print(f"[+] Optimal settings found: Exposure={exposure/1000:.1f}ms, Gain={gain:.1f}")
            return exposure, gain
        
        # Adjust exposure and gain
        if mean_brightness < target_brightness - 15:
            # Too dark - increase exposure first, then gain
            if exposure < 800000:
                exposure = int(exposure * 1.5)
            elif gain < 8.0:
                gain = min(8.0, gain * 1.3)
        else:
            # Too bright - decrease gain first, then exposure
            if gain > 2.0:
                gain = max(2.0, gain * 0.7)
            elif exposure > 50000:
                exposure = int(exposure * 0.7)
    
    print(f"[+] Using adjusted settings: Exposure={exposure/1000:.1f}ms, Gain={gain:.1f}")
    return exposure, gain

def capture_night_image():
    picam2 = Picamera2()
    
    # Configure for low-res preview first
    preview_config = picam2.create_preview_configuration(
        main={"format": "RGB888", "size": (640, 480)},
        controls={"FrameDurationLimits": (100000, 1000000)}
    )
    picam2.configure(preview_config)
    picam2.start()
    
    # Auto-adjust exposure
    optimal_exposure, optimal_gain = auto_adjust_exposure(picam2)
    
    picam2.stop()
    
    # Now configure for full resolution
    config = picam2.create_still_configuration(
        main={"format": "RGB888", "size": (2592, 1944)},
        controls={"FrameDurationLimits": (1000000, 1000000)}
    )
    picam2.configure(config)
    picam2.start()
    
    # Apply optimal settings
    picam2.set_controls({
        "AnalogueGain": optimal_gain,
        "ExposureTime": optimal_exposure,
        "AwbEnable": False,
        "ColourGains": (1.2, 0.9),
        "AeEnable": False,
        "Brightness": 0.0,
        "Contrast": 1.1
    })
    
    print("[*] Camera warming up for final capture...")
    time.sleep(1.5)
    
    print("[*] Capturing final image...")
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
