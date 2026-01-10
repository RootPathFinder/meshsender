import time
import subprocess
import sys
import os
import argparse
import numpy as np
import cv2
from picamera2 import Picamera2

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Relative paths (can be customized as needed)
IMAGE_PATH_TEMP = os.path.join(SCRIPT_DIR, "captured_image_temp.jpg")  # Temporary capture
IMAGE_PATH = os.path.join(SCRIPT_DIR, "captured_image.webp")  # Final WebP
THUMBNAIL_PATH = os.path.join(SCRIPT_DIR, "captured_image_thumb.jpg")
SENDER_SCRIPT = os.path.join(SCRIPT_DIR, "meshsender.py")

# Use the current Python interpreter
PYTHON_BIN = sys.executable

def analyze_image_quality(image):
    """
    Analyze image for over-exposure and color balance issues.
    Returns: dict with metrics and recommended color gains
    """
    # Split into RGB channels
    b, g, r = cv2.split(image)
    
    # 1. Over-exposure detection (check for blown highlights)
    overexposed_pixels = np.sum(image > 240)  # Pixels near white
    total_pixels = image.size
    overexposure_pct = (overexposed_pixels / total_pixels) * 100
    
    # 2. Under-exposure detection (check for crushed shadows)
    underexposed_pixels = np.sum(image < 15)  # Pixels near black
    underexposure_pct = (underexposed_pixels / total_pixels) * 100
    
    # 3. Color balance analysis
    mean_r = np.mean(r)
    mean_g = np.mean(g)
    mean_b = np.mean(b)
    
    # Calculate color cast (deviation from neutral gray)
    avg = (mean_r + mean_g + mean_b) / 3
    r_ratio = mean_r / avg if avg > 0 else 1.0
    g_ratio = mean_g / avg if avg > 0 else 1.0
    b_ratio = mean_b / avg if avg > 0 else 1.0
    
    # 4. Determine color cast type
    color_cast = "neutral"
    if b_ratio > 1.15:
        color_cast = "blue"
    elif r_ratio > 1.15:
        color_cast = "red"
    elif (r_ratio > 1.1 and g_ratio > 1.1):
        color_cast = "yellow"
    
    # 5. Calculate recommended color gains to correct balance
    # If red is too high, REDUCE red gain. If blue is too high, REDUCE blue gain.
    # Gains work by amplifying that channel, so to reduce red cast, lower red gain
    recommended_red_gain = 1.0 / r_ratio if r_ratio > 0 else 1.0
    recommended_blue_gain = 1.0 / b_ratio if b_ratio > 0 else 1.0
    
    # Normalize to green (keep gains relative to each other)
    if g_ratio > 0:
        recommended_red_gain = recommended_red_gain * g_ratio
        recommended_blue_gain = recommended_blue_gain * g_ratio
    
    # Clamp gains to reasonable values
    recommended_red_gain = np.clip(recommended_red_gain, 0.5, 2.5)
    recommended_blue_gain = np.clip(recommended_blue_gain, 0.5, 2.5)
    
    return {
        'overexposure_pct': overexposure_pct,
        'underexposure_pct': underexposure_pct,
        'mean_brightness': avg,
        'color_cast': color_cast,
        'r_ratio': r_ratio,
        'g_ratio': g_ratio,
        'b_ratio': b_ratio,
        'recommended_red_gain': recommended_red_gain,
        'recommended_blue_gain': recommended_blue_gain
    }

def auto_adjust_exposure(picam2, target_brightness=90, max_iterations=5):
    """
    Automatically adjust exposure, gain, and color balance based on preview images.
    Target brightness: 0-255 (90 is good for night vision)
    """
    exposure = 200000  # Start with 200ms
    gain = 4.0
    red_gain = 1.0  # Start neutral
    blue_gain = 1.0  # Start neutral
    
    print("[*] Auto-adjusting exposure and color balance...")
    
    for i in range(max_iterations):
        # Set current settings
        picam2.set_controls({
            "AnalogueGain": gain,
            "ExposureTime": exposure,
            "AwbEnable": False,
            "ColourGains": (red_gain, blue_gain),
            "AeEnable": False
        })
        
        time.sleep(0.5)  # Let camera adjust
        
        # Capture preview
        preview = picam2.capture_array()
        
        # Analyze image quality
        analysis = analyze_image_quality(preview)
        
        print(f"  Iteration {i+1}:")
        print(f"    Brightness: {analysis['mean_brightness']:.1f}/255")
        print(f"    Exposure: {exposure/1000:.1f}ms, Gain: {gain:.1f}")
        print(f"    Color Cast: {analysis['color_cast']} (R:{analysis['r_ratio']:.2f} G:{analysis['g_ratio']:.2f} B:{analysis['b_ratio']:.2f})")
        print(f"    Over-exposed: {analysis['overexposure_pct']:.1f}%, Under-exposed: {analysis['underexposure_pct']:.1f}%")
        
        # Check for over-exposure
        if analysis['overexposure_pct'] > 5.0:
            print(f"    WARNING: Over-exposure detected! Reducing exposure...")
            exposure = int(exposure * 0.6)
            gain = max(1.0, gain * 0.8)
            continue
        
        # Adjust color gains based on analysis
        if i < max_iterations - 1:  # Don't adjust on last iteration
            # Gradually move toward recommended gains (don't jump all at once)
            red_gain = red_gain * 0.6 + analysis['recommended_red_gain'] * 0.4
            blue_gain = blue_gain * 0.6 + analysis['recommended_blue_gain'] * 0.4
            print(f"    Adjusting color: Red gain {red_gain:.2f}, Blue gain {blue_gain:.2f}")
        
        # Check if brightness is acceptable
        if abs(analysis['mean_brightness'] - target_brightness) < 15:
            print(f"[+] Optimal settings found!")
            print(f"    Final: Exposure={exposure/1000:.1f}ms, Gain={gain:.1f}")
            print(f"    Color Gains: Red={red_gain:.2f}, Blue={blue_gain:.2f}")
            return exposure, gain, red_gain, blue_gain
        
        # Adjust exposure and gain based on brightness
        if analysis['mean_brightness'] < target_brightness - 15:
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
    print(f"    Color Gains: Red={red_gain:.2f}, Blue={blue_gain:.2f}")
    return exposure, gain, red_gain, blue_gain

def capture_night_image():
    picam2 = Picamera2()
    
    # Configure for low-res preview first
    preview_config = picam2.create_preview_configuration(
        main={"format": "RGB888", "size": (640, 480)},
        controls={"FrameDurationLimits": (100000, 1000000)}
    )
    picam2.configure(preview_config)
    picam2.start()
    
    # Auto-adjust exposure and color balance
    optimal_exposure, optimal_gain, optimal_red_gain, optimal_blue_gain = auto_adjust_exposure(picam2)
    
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
        "ColourGains": (optimal_red_gain, optimal_blue_gain),
        "AeEnable": False,
        "Brightness": 0.0,
        "Contrast": 1.1
    })
    
    print("[*] Camera warming up for final capture...")
    time.sleep(1.5)
    
    print("[*] Capturing final image...")
    picam2.capture_file(IMAGE_PATH_TEMP)  # Capture as JPEG first
    picam2.stop()
    print(f"[+] Image captured to {IMAGE_PATH_TEMP}")
    
    # Generate thumbnail from captured JPEG
    print("[*] Generating preview thumbnail...")
    try:
        from PIL import Image
        # Open the captured JPEG
        img = Image.open(IMAGE_PATH_TEMP)
        print(f"[+] Opened captured image: {img.size} {img.format}")
        
        # Create thumbnail - resize to 320x240 max
        img_thumb = img.copy()
        img_thumb.thumbnail((320, 240), Image.Resampling.LANCZOS)
        img_thumb.save(THUMBNAIL_PATH, format='JPEG', quality=50)
        print(f"[+] Thumbnail saved to {THUMBNAIL_PATH}")
        print(f"    Thumbnail file size: {os.path.getsize(THUMBNAIL_PATH)} bytes")
        
        # Convert original to WebP for sending
        img.save(IMAGE_PATH, format='WEBP', quality=80)
        print(f"[+] WebP image saved to {IMAGE_PATH}")
        print(f"    WebP file size: {os.path.getsize(IMAGE_PATH)} bytes")
    except Exception as e:
        print(f"[X] Image processing failed: {e}")
        import traceback
        traceback.print_exc()
        return  # Don't continue if image processing failed
    
    # Save camera metadata for overlay
    import json
    metadata = {
        'exposure': optimal_exposure / 1000,  # Convert to ms
        'gain': optimal_gain,
        'red_gain': optimal_red_gain,
        'blue_gain': optimal_blue_gain
    }
    metadata_file = IMAGE_PATH + '.meta'
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f)

def send_to_mesh(target_node, res, qual):
    print(f"[*] Sending to Node: {target_node}")
    print(f"    Thumbnail path: {THUMBNAIL_PATH}")
    print(f"    Thumbnail exists: {os.path.exists(THUMBNAIL_PATH)}")
    print(f"    Main image path: {IMAGE_PATH}")
    print(f"    Main image exists: {os.path.exists(IMAGE_PATH)}")
    
    # Send thumbnail first (for preview)
    thumbnail_sent = False
    if os.path.exists(THUMBNAIL_PATH):
        print(f"[*] Sending thumbnail preview ({os.path.getsize(THUMBNAIL_PATH)} bytes)...")
        cmd = [
            PYTHON_BIN, SENDER_SCRIPT, "send", 
            target_node, THUMBNAIL_PATH, 
            "--res", "320", "--qual", "50"
        ]
        print(f"    Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            print("[X] Error: Thumbnail transmission failed.")
        else:
            print("[+] Thumbnail sent successfully.")
            thumbnail_sent = True
            # Wait a moment between sends to ensure receiver processes first transfer
            print("[*] Waiting 2 seconds before sending main image...")
            time.sleep(2)
    else:
        print(f"[!] Thumbnail not found at {THUMBNAIL_PATH}")
    
    # Then send the full resolution image
    if os.path.exists(IMAGE_PATH):
        print(f"[*] Sending full resolution image ({os.path.getsize(IMAGE_PATH)} bytes)...")
        cmd = [
            PYTHON_BIN, SENDER_SCRIPT, "send", 
            target_node, IMAGE_PATH, 
            "--res", res, "--qual", qual
        ]
        print(f"    Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode == 0:
            print("[+] Full image transmission finished successfully.")
        else:
            print("[X] Error: Full image transmission failed.")
    else:
        print(f"[!] Main image not found at {IMAGE_PATH}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Capture and send image via Meshtastic")
    parser.add_argument("target_id", help="Target node ID (e.g., !da56b70c)")
    parser.add_argument("--res", default="720", help="Image resolution (default: 720)")
    parser.add_argument("--qual", default="70", help="JPEG quality (default: 70)")
    parser.add_argument("--no-send", action="store_true", help="Capture only, skip mesh send")
    
    args = parser.parse_args()
    
    try:
        capture_night_image()
        if not args.no_send:
            send_to_mesh(args.target_id, args.res, args.qual)
    except Exception as e:
        print(f"[X] An error occurred: {e}")
