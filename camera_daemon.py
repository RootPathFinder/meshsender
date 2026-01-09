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
PYTHON_BIN = sys.executable

# Global state
motion_detection_enabled = False
last_capture_time = 0
motion_cooldown = 30  # Seconds between motion-triggered captures
picam2 = None
last_frame = None

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

def capture_and_send(target_id, reason="command"):
    """Trigger a capture and send via takepic.py"""
    global last_capture_time
    
    print(f"\n[*] Triggering capture ({reason})...")
    last_capture_time = time.time()
    
    try:
        # Call takepic.py to capture and send
        cmd = [PYTHON_BIN, TAKEPIC_SCRIPT, target_id, "--res", "720", "--qual", "70"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            print(f"[+] Capture completed successfully")
            return True
        else:
            print(f"[X] Capture failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"[X] Capture error: {e}")
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
    
    while True:
        time.sleep(0.5)  # Check twice per second
        check_counter += 1
        
        # Show status every 60 seconds
        if check_counter % 120 == 0:
            status = "ACTIVE" if motion_detection_enabled else "disabled"
            print(f"[*] Motion detection: {status}")
        
        if not motion_detection_enabled:
            continue
        
        # Check cooldown
        if time.time() - last_capture_time < motion_cooldown:
            continue
        
        # Detect motion
        if detect_motion():
            capture_and_send(target_id, reason="motion")

def on_command(packet, interface):
    """Handle incoming mesh commands"""
    global motion_detection_enabled
    
    try:
        if 'decoded' in packet and 'text' in packet['decoded']:
            # Only respond to DIRECT messages (not channel broadcasts)
            to_id = packet.get('toId')
            if not to_id or to_id == '^all':
                return  # Ignore channel/broadcast messages
            
            text = packet['decoded']['text'].strip()
            sender = packet.get('fromId', 'unknown')
            
            print(f"\n[CMD] Direct message from {sender}: '{text}'")
            
            # Parse commands
            if text == "CAPTURE":
                print("[*] Remote capture requested")
                threading.Thread(target=capture_and_send, args=(sender,), daemon=True).start()
                interface.sendText(f"📸 Capture started", destinationId=sender)
            
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
                help_msg = "Commands: CAPTURE, MOTION_ON, MOTION_OFF, STATUS"
                interface.sendText(help_msg, destinationId=sender)
    
    except Exception as e:
        print(f"[!] Command handler error: {e}")

def main():
    global start_time
    
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
    print("  CAPTURE     - Take photo immediately")
    print("  MOTION_ON   - Enable motion detection")
    print("  MOTION_OFF  - Disable motion detection")
    print("  STATUS      - Get camera status")
    print("=" * 50)
    
    # Initialize camera for motion detection
    if not initialize_camera():
        print("[X] Failed to initialize camera. Exiting.")
        sys.exit(1)
    
    # Connection loop with auto-reconnect
    iface = None
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
