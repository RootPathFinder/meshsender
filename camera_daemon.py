#!/usr/bin/env python3
"""
Camera Daemon - Runs continuously on trail camera
Listens for mesh commands and handles motion detection
"""

import meshtastic
import meshtastic.serial_interface
from pubsub import pub
import time
import sys
import os
import subprocess
import threading
import numpy as np
import cv2
from picamera2 import Picamera2
import importlib.util

# Import meshsender on_ack callback for ACK message handling
spec = importlib.util.spec_from_file_location("meshsender_module", os.path.join(os.path.dirname(os.path.abspath(__file__)), "meshsender.py"))
meshsender_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(meshsender_module)

# Import takepic functions for exposure adjustment
spec_takepic = importlib.util.spec_from_file_location("takepic_module", os.path.join(os.path.dirname(os.path.abspath(__file__)), "takepic.py"))
takepic_module = importlib.util.module_from_spec(spec_takepic)
spec_takepic.loader.exec_module(takepic_module)

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAKEPIC_SCRIPT = os.path.join(SCRIPT_DIR, "takepic.py")
SENDER_SCRIPT = os.path.join(SCRIPT_DIR, "meshsender.py")
IMAGE_PATH_TEMP = os.path.join(SCRIPT_DIR, "captured_image_temp.jpg")
IMAGE_PATH = os.path.join(SCRIPT_DIR, "captured_image.webp")
PYTHON_BIN = sys.executable
EXPOSURE_REFRESH_INTERVAL = 180  # Refresh exposure settings every 3 minutes

# Global state
motion_detection_enabled = False
last_capture_time = 0
motion_cooldown = 30  # Seconds between motion-triggered captures
picam2 = None
last_frame = None
iface = None  # Global Meshtastic interface
target_id = None  # Default target for image transmission
exposure_refresh_stop = threading.Event()  # Signal to stop exposure refresh thread
camera_lock = threading.Lock()  # Protect camera access

# Frame buffering for motion capture
frame_buffer = None  # Holds the most recent full-res frame
frame_buffer_lock = threading.Lock()  # Protect frame buffer access
capture_frame_on_motion = False  # Flag to capture next motion-detected frame

def initialize_camera():
    """Initialize camera for motion detection"""
    global picam2
    try:
        picam2 = Picamera2()
        config = picam2.create_preview_configuration(
            main={"size": (640, 480), "format": "RGB888"}
        )
        picam2.configure(config)
        picam2.start()
        time.sleep(2)  # Camera warm-up
        print("[+] Camera initialized for motion detection")
        return True
    except Exception as e:
        print(f"[X] Camera initialization failed: {e}")
        return False

def capture_full_resolution_frame():
    """
    Capture a high-resolution frame and save it to disk for sending.
    This runs in the main motion detection camera - no subprocess needed.
    """
    global picam2, frame_buffer, frame_buffer_lock
    
    if not picam2:
        return False
    
    try:
        print("[*] Capturing high-res frame from buffer...")
        
        # Get the buffered preview frame dimensions
        if frame_buffer is None:
            print("[!] No buffered frame available, capturing now...")
            return False
        
        # For speed, just use PIL to resize and save the buffered frame
        # This avoids starting a new camera instance
        from PIL import Image
        import json
        
        # Convert buffered RGB frame to PIL Image
        frame_pil = Image.fromarray(frame_buffer, 'RGB')
        
        # Load cached exposure settings for metadata
        metadata_file = IMAGE_PATH + '.meta'
        metadata = {'exposure': 0, 'gain': 0, 'red_gain': 1.0, 'blue_gain': 1.0}
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
            except:
                pass
        
        # Save as WebP (quality 80 for good balance)
        frame_pil.save(IMAGE_PATH, format='WEBP', quality=80)
        print(f"[+] Captured frame saved to {IMAGE_PATH} ({os.path.getsize(IMAGE_PATH)} bytes)")
        
        # Save metadata
        with open(metadata_file, 'w') as f:
            json.dump(metadata, f)
        
        return True
    
    except Exception as e:
        print(f"[X] Frame capture error: {e}")
        import traceback
        traceback.print_exc()
        return False

def periodic_exposure_refresh():
    """
    Background thread that periodically refreshes exposure settings while motion detection is active.
    This ensures cached settings stay current with lighting conditions.
    """
    global picam2, motion_detection_enabled, exposure_refresh_stop, camera_lock
    
    print("[*] Exposure refresh thread started")
    
    while not exposure_refresh_stop.is_set():
        # Wait for interval, but allow early wake-up if stop is signaled
        if exposure_refresh_stop.wait(EXPOSURE_REFRESH_INTERVAL):
            break
        
        # Only refresh if motion detection is active
        if not motion_detection_enabled or picam2 is None:
            continue
        
        try:
            print("[*] Periodic exposure refresh starting...")
            
            with camera_lock:
                if picam2 is None or not motion_detection_enabled:
                    continue
                
                # Capture a preview and analyze exposure
                try:
                    preview = picam2.capture_array()
                    analysis = takepic_module.analyze_image_quality(preview)
                    
                    print(f"[*] Exposure check: Brightness={analysis['mean_brightness']:.1f}/255")
                    print(f"    Color: {analysis['color_cast']} (R:{analysis['r_ratio']:.2f} B:{analysis['b_ratio']:.2f})")
                    
                    # Use takepic's auto_adjust to get new optimal settings
                    # Run just 2 quick iterations to update for lighting changes
                    picam2_temp = picam2
                    new_exposure, new_gain, new_red_gain, new_blue_gain = takepic_module.auto_adjust_exposure(
                        picam2_temp, 
                        target_brightness=90, 
                        max_iterations=2  # Quick 2-iteration update
                    )
                    
                    # Save new settings
                    import json
                    metadata_file = IMAGE_PATH + '.meta'
                    metadata = {
                        'exposure': new_exposure / 1000,  # Convert to ms
                        'gain': new_gain,
                        'red_gain': new_red_gain,
                        'blue_gain': new_blue_gain
                    }
                    with open(metadata_file, 'w') as f:
                        json.dump(metadata, f)
                    
                    print(f"[+] Exposure settings updated and cached")
                    print(f"    Exposure={new_exposure/1000:.1f}ms, Gain={new_gain:.1f}")
                    
                except Exception as inner_e:
                    print(f"[!] Exposure refresh analysis error: {inner_e}")
                    continue
        
        except Exception as e:
            print(f"[!] Exposure refresh error: {e}")
            import traceback
            traceback.print_exc()

def capture_and_send(target_id, reason="command", res=720, qual=70, fast_mode=False):
    """
    Capture and send image via mesh.
    For motion-triggered captures, uses buffered frame from camera (instant).
    """
    global frame_buffer, frame_buffer_lock, iface, last_capture_time, picam2
    
    print(f"\n[*] Triggering capture ({reason}) - {res}px @ Q{qual}...")
    last_capture_time = time.time()
    
    try:
        # For motion events, use buffered frame (instant - no subprocess!)
        # For commands, still capture from camera
        if reason == "motion" and frame_buffer is not None:
            print("[*] Using buffered frame for instant capture...")
            if not capture_full_resolution_frame():
                print("[!] Failed to use buffered frame")
                return False
        else:
            print("[*] Capturing fresh frame from camera...")
            if not capture_full_resolution_frame():
                print("[!] Failed to capture frame")
                return False
        
        # Send the image
        if not iface:
            print(f"[X] No Meshtastic interface available")
            return False
        
        print(f"[*] Sending to {target_id}...")
        
        # Send the WebP image
        if os.path.exists(IMAGE_PATH):
            file_size = os.path.getsize(IMAGE_PATH)
            print(f"[*] Sending image ({file_size} bytes)...")
            success = meshsender_module.send_image(iface, target_id, IMAGE_PATH, res=res, qual=qual)
        else:
            print(f"[X] Image not found at {IMAGE_PATH}")
            success = False
        
        if success:
            print(f"[+] Capture and send completed successfully")
            return True
        else:
            print(f"[X] Send failed")
            return False
    
    except Exception as e:
        print(f"[X] Capture error: {e}")
        import traceback
        traceback.print_exc()
        return False

def detect_motion():
    """Detect motion using frame differencing"""
    global last_frame, picam2, frame_buffer, frame_buffer_lock
    
    if not picam2:
        return False
    
    try:
        # Capture current frame
        frame = picam2.capture_array()
        
        # Buffer this frame for potential motion capture
        with frame_buffer_lock:
            frame_buffer = frame.copy()
        
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        
        # Initialize reference frame
        if last_frame is None:
            last_frame = gray
            return False
        
        # Compute difference
        frame_delta = cv2.absdiff(last_frame, gray)
        thresh = cv2.threshold(frame_delta, 25, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)
        
        # Calculate percentage of changed pixels
        changed_pixels = np.sum(thresh > 0)
        total_pixels = thresh.size
        change_percent = (changed_pixels / total_pixels) * 100
        
        # Trigger if significant change (> 0.5% of frame for faster detection)
        if change_percent > 0.5:
            print(f"[!] Motion detected! ({change_percent:.2f}% change)")
            return True
        
        # Only update reference frame when NO motion is detected
        # This prevents the background model from adapting to moving objects
        # Use very slow adaptation (95% old, 5% new) during quiet periods
        last_frame = cv2.addWeighted(last_frame, 0.95, gray, 0.05, 0)
        
        return False
        
    except Exception as e:
        print(f"[X] Motion detection error: {e}")
        return False

def motion_detection_loop(target_id):
    """Continuous motion detection loop"""
    global motion_detection_enabled, last_capture_time, picam2, exposure_refresh_stop, camera_lock
    
    print("[*] Motion detection loop started")
    check_counter = 0
    exposure_refresh_thread = None
    
    while True:
        time.sleep(0.1)  # Check 10 times per second for faster motion detection
        check_counter += 1
        
        # Show status every 60 seconds (600 checks at 0.1s interval)
        if check_counter % 600 == 0:
            status = "ACTIVE" if motion_detection_enabled else "disabled"
            print(f"[*] Motion detection: {status}")
        
        # Initialize camera and start exposure refresh thread when motion detection is enabled
        if motion_detection_enabled and picam2 is None:
            print("[*] Motion detection enabled - initializing camera...")
            initialize_camera()
            
            # Start exposure refresh thread if not already running
            if exposure_refresh_thread is None or not exposure_refresh_thread.is_alive():
                exposure_refresh_stop.clear()
                exposure_refresh_thread = threading.Thread(target=periodic_exposure_refresh, daemon=True)
                exposure_refresh_thread.start()
                print("[*] Started periodic exposure refresh thread")
        
        # Stop camera when motion detection is disabled to save power
        if not motion_detection_enabled and picam2 is not None:
            print("[*] Motion detection disabled - stopping camera to save power...")
            
            # Stop the exposure refresh thread
            if exposure_refresh_thread is not None and exposure_refresh_thread.is_alive():
                print("[*] Stopping exposure refresh thread...")
                exposure_refresh_stop.set()
                exposure_refresh_thread.join(timeout=5)
            
            try:
                picam2.stop()
                picam2.close()
                picam2 = None
            except Exception as e:
                print(f"[!] Error stopping camera: {e}")
            continue
        
        if not motion_detection_enabled:
            continue
        
        # Check cooldown
        if time.time() - last_capture_time < motion_cooldown:
            continue
        
        # Detect motion
        if detect_motion():
            # Use fast_mode=True for motion captures: uses cached exposure settings, skips auto-adjust
            # Run async to keep motion detection loop responsive
            threading.Thread(
                target=capture_and_send, 
                args=(target_id, "motion", 720, 70, True), 
                daemon=True
            ).start()

def on_command(packet, interface):
    """Handle incoming mesh commands"""
    global motion_detection_enabled, target_id
    
    try:
        if 'decoded' in packet and 'text' in packet['decoded']:
            # Only respond to DIRECT messages (not channel broadcasts)
            to_id = packet.get('toId')
            if not to_id or to_id == '^all':
                return  # Ignore channel/broadcast messages
            
            text = packet['decoded']['text'].strip().upper()  # Convert to uppercase for case-insensitive matching
            sender = packet.get('fromId', 'unknown')
            
            # Ignore transfer protocol messages (REQ/ACK/OK)
            if text.startswith(('REQ:', 'ACK:', 'OK:')):
                return  # These are meshsender protocol messages, not commands
            
            print(f"\n[CMD] Direct message from {sender}: '{text}'")
            
            # Parse commands (now case-insensitive)
            if text.startswith("CAPTURE"):
                # Parse optional parameters: CAPTURE:res:qual
                parts = text.split(':')
                res = 720  # Default
                qual = 70  # Default
                
                if len(parts) >= 2:
                    try:
                        res = int(parts[1])
                    except ValueError:
                        interface.sendText(f"❌ Invalid resolution: {parts[1]}", destinationId=sender)
                        return
                
                if len(parts) >= 3:
                    try:
                        qual = int(parts[2])
                    except ValueError:
                        interface.sendText(f"❌ Invalid quality: {parts[2]}", destinationId=sender)
                        return
                
                print(f"[*] Remote capture requested: {res}px @ Q{qual}")
                # Send to default target_id, not back to command sender
                threading.Thread(target=capture_and_send, args=(target_id, "command", res, qual), daemon=True).start()
                interface.sendText(f"📸 Capture started ({res}px Q{qual})", destinationId=sender)
            
            elif text == "MOTION_ON":
                if not motion_detection_enabled:
                    motion_detection_enabled = True
                    print("[+] Motion detection ENABLED - camera will start on next check")
                    interface.sendText(f"✓ Motion detection ON", destinationId=sender)
                else:
                    interface.sendText(f"ℹ Motion already enabled", destinationId=sender)
            
            elif text == "MOTION_OFF":
                if motion_detection_enabled:
                    motion_detection_enabled = False
                    print("[-] Motion detection DISABLED - camera will stop on next check")
                    interface.sendText(f"✓ Motion detection OFF", destinationId=sender)
                else:
                    interface.sendText(f"ℹ Motion already disabled", destinationId=sender)
            
            elif text == "STATUS":
                motion_status = "ON" if motion_detection_enabled else "OFF"
                uptime = int(time.time() - start_time)
                status_msg = f"📊 Motion:{motion_status} | Uptime:{uptime}s"
                interface.sendText(status_msg, destinationId=sender)
                print(f"[*] Status sent to {sender}")
            
            elif text == "HELP":
                help_msg = "Commands: CAPTURE[:res[:qual]], MOTION_ON, MOTION_OFF, STATUS"
                interface.sendText(help_msg, destinationId=sender)
    
    except Exception as e:
        print(f"[!] Command handler error: {e}")

def main():
    global start_time, iface, target_id
    
    if len(sys.argv) < 2:
        print("Usage: camera_daemon.py <default_target_id>")
        print("  Will listen for commands and send motion-triggered images to target")
        sys.exit(1)
    
    target_id = sys.argv[1]
    start_time = time.time()
    
    print("=" * 50)
    print("Trail Camera Daemon")
    print("=" * 50)
    print(f"Target Node: {target_id}")
    print(f"Motion Detection: {'ENABLED' if motion_detection_enabled else 'DISABLED'}")
    print("\nCommands:")
    print("  CAPTURE[:res[:qual]]  - Take photo immediately (e.g., CAPTURE:320:40)")
    print("  MOTION_ON             - Enable motion detection")
    print("  MOTION_OFF            - Disable motion detection")
    print("  STATUS                - Get camera status")
    print("=" * 50)
    print("[*] Camera will start automatically when motion detection is enabled")
    
    # Connection loop with auto-reconnect
    motion_thread = None
    
    try:
        while True:
            try:
                # Connect/reconnect to Meshtastic
                if iface is None:
                    print("\n[*] Connecting to Meshtastic device...")
                    iface = meshtastic.serial_interface.SerialInterface(connectNow=True)
                    print("[+] Connected successfully")
                    
                    # Subscribe to incoming messages
                    pub.subscribe(on_command, "meshtastic.receive")
                    pub.subscribe(meshsender_module.on_ack, "meshtastic.receive")
                    
                    # Start motion detection thread (if not already running)
                    if motion_thread is None or not motion_thread.is_alive():
                        motion_thread = threading.Thread(
                            target=motion_detection_loop, 
                            args=(target_id,), 
                            daemon=True
                        )
                        motion_thread.start()
                    
                    print(f"\n[*] Camera daemon active. Waiting for commands...")
                    print(f"[*] Motion detection is currently {'ENABLED' if motion_detection_enabled else 'DISABLED'}")
                    print(f"[*] Send 'MOTION_ON' to enable auto-capture")
                    print(f"[*] Send 'HELP' via mesh to see available commands\n")
                
                # Check connection health
                time.sleep(5)
                
                # Test if interface is still alive
                if iface and not hasattr(iface, '_timeout'):
                    # Interface seems dead, trigger reconnect
                    raise Exception("Interface disconnected")
                    
            except KeyboardInterrupt:
                raise  # Pass through to outer handler
                
            except Exception as e:
                print(f"\n[!] Connection error: {e}")
                print("[*] Attempting to reconnect in 10 seconds...")
                
                # Cleanup old interface
                if iface:
                    try:
                        iface.close()
                    except:
                        pass
                    iface = None
                
                time.sleep(10)
    
    except KeyboardInterrupt:
        print("\n\n[*] Shutting down camera daemon...")
        if iface:
            try:
                iface.close()
            except:
                pass
        if picam2:
            picam2.stop()
        sys.exit(0)
    
    except Exception as e:
        print(f"\n[X] Fatal error: {e}")
        if iface:
            try:
                iface.close()
            except:
                pass
        if picam2:
            picam2.stop()
        sys.exit(1)

if __name__ == "__main__":
    main()
