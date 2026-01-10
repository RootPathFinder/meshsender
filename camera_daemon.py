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

# Configuration
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAKEPIC_SCRIPT = os.path.join(SCRIPT_DIR, "takepic.py")
SENDER_SCRIPT = os.path.join(SCRIPT_DIR, "meshsender.py")
IMAGE_PATH = os.path.join(SCRIPT_DIR, "captured_image.jpg")
PYTHON_BIN = sys.executable

# Global state
motion_detection_enabled = False
last_capture_time = 0
motion_cooldown = 30  # Seconds between motion-triggered captures
picam2 = None
last_frame = None
iface = None  # Global Meshtastic interface
target_id = None  # Default target for image transmission

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

def capture_and_send(target_id, reason="command", res=720, qual=70):
    """Trigger a capture and send via takepic.py"""
    global last_capture_time, picam2, last_frame, iface
    
    print(f"\n[*] Triggering capture ({reason}) - {res}px @ Q{qual}...")
    last_capture_time = time.time()
    
    try:
        # Release camera for takepic.py to use
        if picam2:
            print("[*] Releasing camera...")
            picam2.stop()
            picam2.close()
            picam2 = None
            last_frame = None
            time.sleep(1)  # Give camera time to fully release
        
        # Call takepic.py to capture only (no send, daemon will send)
        cmd = [PYTHON_BIN, TAKEPIC_SCRIPT, target_id, "--res", str(res), "--qual", str(qual), "--no-send"]
        print(f"[*] Capturing...")
        
        result = subprocess.run(cmd, timeout=300)
        
        if result.returncode != 0:
            print(f"[X] Capture failed with exit code: {result.returncode}")
            initialize_camera()
            return False
        
        # Now send the image using daemon's Meshtastic interface
        if not iface:
            print(f"[X] No Meshtastic interface available")
            initialize_camera()
            return False
            
        print(f"[*] Sending to {target_id}...")
        
        # Import and use send_image from meshsender
        import importlib.util
        spec = importlib.util.spec_from_file_location("meshsender", SENDER_SCRIPT)
        meshsender = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(meshsender)
        
        # Send using daemon's interface
        success = meshsender.send_image(iface, target_id, IMAGE_PATH, res=res, qual=qual)
        
        # Reinitialize camera for motion detection
        print("[*] Reinitializing camera...")
        initialize_camera()
        
        if success:
            print(f"[+] Capture and send completed successfully")
            return True
        else:
            print(f"[X] Send failed")
            return False
    except subprocess.TimeoutExpired:
        print(f"[X] Process timed out after 300 seconds")
        initialize_camera()  # Ensure camera restarts even on timeout
        return False
    except Exception as e:
        print(f"[X] Capture error: {e}")
        import traceback
        traceback.print_exc()
        initialize_camera()  # Ensure camera restarts even on error
        return False

def detect_motion():
    """Detect motion using frame differencing"""
    global last_frame, picam2
    
    if not picam2:
        return False
    
    try:
        # Capture current frame
        frame = picam2.capture_array()
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
        
        # Update reference frame slowly (moving average)
        last_frame = cv2.addWeighted(last_frame, 0.9, gray, 0.1, 0)
        
        # Trigger if significant change (> 1% of frame)
        if change_percent > 1.0:
            print(f"[!] Motion detected! ({change_percent:.2f}% change)")
            return True
        
        return False
        
    except Exception as e:
        print(f"[X] Motion detection error: {e}")
        return False

def motion_detection_loop(target_id):
    """Continuous motion detection loop"""
    global motion_detection_enabled, last_capture_time
    
    print("[*] Motion detection loop started")
    check_counter = 0
    
    # Adaptive check interval based on activity
    check_interval = 1.0  # Start with 1 second (was 0.5s - more power efficient)
    no_motion_count = 0
    
    while True:
        time.sleep(check_interval)
        check_counter += 1
        
        # Show status every 60 seconds
        if check_counter % int(60 / check_interval) == 0:
            status = "ACTIVE" if motion_detection_enabled else "disabled"
            print(f"[*] Motion detection: {status} (interval: {check_interval}s)")
        
        if not motion_detection_enabled:
            # When disabled, check less frequently to save power
            check_interval = 2.0
            continue
        
        # Check cooldown
        if time.time() - last_capture_time < motion_cooldown:
            continue
        
        # Detect motion
        if detect_motion():
            capture_and_send(target_id, reason="motion")
            check_interval = 0.5  # Check more frequently after motion detected
            no_motion_count = 0
        else:
            no_motion_count += 1
            # Gradually increase interval if no motion (power saving)
            if no_motion_count > 10 and check_interval < 2.0:
                check_interval = min(2.0, check_interval + 0.1)

def on_command(packet, interface):
    """Handle incoming mesh commands"""
    global motion_detection_enabled, target_id
    
    try:
        if 'decoded' in packet and 'text' in packet['decoded']:
            # Only respond to DIRECT messages (not channel broadcasts)
            to_id = packet.get('toId')
            if not to_id or to_id == '^all':
                return  # Ignore channel/broadcast messages
            
            text = packet['decoded']['text'].strip()
            sender = packet.get('fromId', 'unknown')
            
            # Ignore transfer protocol messages (REQ/ACK/OK)
            if text.startswith(('REQ:', 'ACK:', 'OK:')):
                return  # These are meshsender protocol messages, not commands
            
            print(f"\n[CMD] Direct message from {sender}: '{text}'")
            
            # Parse commands
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
                    print("[+] Motion detection ENABLED")
                    interface.sendText(f"✓ Motion detection ON", destinationId=sender)
                else:
                    interface.sendText(f"ℹ Motion already enabled", destinationId=sender)
            
            elif text == "MOTION_OFF":
                if motion_detection_enabled:
                    motion_detection_enabled = False
                    print("[-] Motion detection DISABLED")
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
    
    # Initialize camera for motion detection
    if not initialize_camera():
        print("[X] Failed to initialize camera. Exiting.")
        sys.exit(1)
    
    # Connection loop with auto-reconnect
    motion_thread = None
    reconnect_delay = 10  # Start with 10 second delay
    max_reconnect_delay = 300  # Max 5 minutes between reconnect attempts
    
    try:
        while True:
            try:
                # Connect/reconnect to Meshtastic
                if iface is None:
                    print("\n[*] Connecting to Meshtastic device...")
                    iface = meshtastic.serial_interface.SerialInterface(connectNow=True)
                    print("[+] Connected successfully")
                    
                    # Reset reconnect delay on successful connection
                    reconnect_delay = 10
                    
                    # Subscribe to incoming messages
                    pub.subscribe(on_command, "meshtastic.receive")
                    
                    # Send ready broadcast to channel 0
                    try:
                        my_node = iface.getMyNodeInfo()
                        node_id = my_node.get('user', {}).get('id', 'unknown')
                        ready_msg = f"📷 Trail camera ready | Motion: {'ON' if motion_detection_enabled else 'OFF'} | Node: {node_id}"
                        iface.sendText(ready_msg, channelIndex=0)
                        print(f"[+] Sent ready broadcast to channel 0")
                    except Exception as e:
                        print(f"[!] Could not send broadcast: {e}")
                    
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
                print(f"[*] Attempting to reconnect in {reconnect_delay}s...")
                
                # Cleanup old interface
                if iface:
                    try:
                        iface.close()
                    except:
                        pass
                    iface = None
                
                time.sleep(reconnect_delay)
                
                # Exponential backoff: 10s, 20s, 40s, 80s, up to 5 minutes
                reconnect_delay = min(max_reconnect_delay, reconnect_delay * 2)
    
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
