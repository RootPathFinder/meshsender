import meshtastic
import meshtastic.serial_interface
from pubsub import pub
from PIL import Image, ImageDraw
import sys
import time
import io
import argparse
import zlib
import os
import threading
import http.server
import socketserver

# --- CONFIGURATION ---
PORT_NUM = 256 
CHUNK_SIZE = 200 
WEB_PORT = 5678
GALLERY_DIR = "gallery"
image_buffer = {}

if not os.path.exists(GALLERY_DIR):
    os.makedirs(GALLERY_DIR)

# --- WEB SERVER LOGIC ---
class GalleryHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/image.jpg':
            images = sorted([f for f in os.listdir(GALLERY_DIR) if f.endswith('.jpg')], reverse=True)
            if images: self.path = f"/{GALLERY_DIR}/{images[0]}"
            else:
                self.send_error(404, "No images yet.")
                return
            return http.server.SimpleHTTPRequestHandler.do_GET(self)
        
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            images = sorted([f for f in os.listdir(GALLERY_DIR) if f.endswith('.jpg')], reverse=True)[:20]
            html = "<html><head><title>Mesh Gallery</title><style>"
            html += "body{font-family:sans-serif; background:#121212; color:white; text-align:center; padding:20px;}"
            html += ".grid{display:grid; grid-template-columns:repeat(auto-fill, minmax(180px, 1fr)); gap:15px;}"
            html += "img{width:100%; border:3px solid #333; border-radius:10px;} .card{background:#1e1e1e; padding:10px; border-radius:10px;}"
            html += "a{color:#00e5ff; text-decoration:none;}</style></head><body>"
            html += "<h1>📡 Meshtastic Live Gallery</h1><div class='grid'>"
            for img in images:
                html += f"<div class='card'><a href='/{GALLERY_DIR}/{img}'><img src='/{GALLERY_DIR}/{img}'></a><br><small>{img}</small></div>"
            html += "</div></body></html>"
            self.wfile.write(html.encode())
            return
        return http.server.SimpleHTTPRequestHandler.do_GET(self)

def start_web_server():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", WEB_PORT), GalleryHandler) as httpd:
        httpd.serve_forever()

# --- UTILS ---
def draw_progress_bar(current_idx, total_chunks, start_time, current_bytes, total_bytes, retries=0):
    elapsed = time.time() - start_time
    # Avoid division by zero on very first packet
    elapsed = max(elapsed, 0.001) 
    percent = float(current_idx) / total_chunks
    bps = current_bytes / elapsed
    length = 15
    arrow = '-' * int(round(percent * length) - 1) + '>'
    spaces = ' ' * (length - len(arrow))
    sys.stdout.write(f"\rProgress: [{arrow + spaces}] {int(percent * 100)}% | {current_bytes}/{total_bytes}B | {bps:.1f}B/s | R:{retries}   ")
    sys.stdout.flush()

def add_diagnostic_overlay(img, stats_text):
    draw = ImageDraw.Draw(img, "RGBA")
    ts = time.strftime("%H:%M")
    full_text = f"{ts} | {stats_text}"
    box_width = len(full_text) * 6
    draw.rectangle([0, img.height - 14, box_width, img.height], fill=(0, 0, 0, 160))
    draw.text((3, img.height - 13), full_text, fill=(255, 255, 255, 255))
    return img.convert("RGB")

# --- MESH LOGIC ---
def on_receive(packet, interface):
    global image_buffer
    try:
        if 'decoded' in packet:
            decoded = packet['decoded']
            if decoded.get('portnum') == PORT_NUM or decoded.get('portnum') == 'PRIVATE_APP':
                data = decoded.get('payload')
                
                # New Header Structure:
                # [0]: Total Chunks (1 byte)
                # [1]: Chunk Index (1 byte)
                # [2-5]: CRC32 (4 bytes)
                # [6-9]: Total Byte Size (4 bytes)
                total_chunks = data[0]
                chunk_index = data[1]
                crc_val = int.from_bytes(data[2:6], byteorder='big')
                reported_total_size = int.from_bytes(data[6:10], byteorder='big')
                payload = data[10:]
                sender = packet.get('fromId', 'unknown')
                
                if sender in image_buffer:
                    if image_buffer[sender]['crc'] != crc_val:
                        del image_buffer[sender]

                if sender not in image_buffer:
                    image_buffer[sender] = {
                        'chunks': [None]*total_chunks, 
                        'start': time.time(), 
                        'crc': crc_val, 
                        'bytes': 0,
                        'total_size': reported_total_size
                    }
                    print(f"\n[!] Incoming Image from {sender} ({reported_total_size} bytes)")
                
                if image_buffer[sender]['chunks'][chunk_index] is None:
                    image_buffer[sender]['chunks'][chunk_index] = payload
                    image_buffer[sender]['bytes'] += len(payload)
                
                count = sum(1 for x in image_buffer[sender]['chunks'] if x is not None)
                draw_progress_bar(count, total_chunks, image_buffer[sender]['start'], image_buffer[sender]['bytes'], image_buffer[sender]['total_size'])

                if None not in image_buffer[sender]['chunks']:
                    full = b"".join(image_buffer[sender]['chunks'])
                    if zlib.crc32(full) & 0xFFFFFFFF == image_buffer[sender]['crc']:
                        img = Image.open(io.BytesIO(full))
                        fname = f"{GALLERY_DIR}/img_{int(time.time())}.jpg"
                        img.save(fname)
                        duration = time.time() - image_buffer[sender]['start']
                        print(f"\n[SUCCESS] {len(full)} bytes in {duration:.1f}s")
                    else:
                        print(f"\n[X] CRC mismatch! Image corrupted.")
                    del image_buffer[sender]
    except Exception as e:
        print(f"\n[!] Receive error: {e}")

def send_image(interface, target_id, file_path, res, qual):
    try:
        img = Image.open(file_path)
        img.thumbnail((res, res)) 
        
        tmp = io.BytesIO()
        img.save(tmp, format='JPEG', quality=qual)
        size_kb = len(tmp.getvalue()) / 1024
        
        stats_info = f"{res}px {qual}Q {size_kb:.1f}KB"
        img = add_diagnostic_overlay(img, stats_info)
        
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=qual)
        data = buf.getvalue()
        total_size = len(data)
        
        print(f"\n[*] SENDING: {file_path}")
        print(f"[*] TOTAL PAYLOAD: {total_size} bytes")
        
        crc_val = zlib.crc32(data) & 0xFFFFFFFF
        # Adjust actual_chunk to account for the larger 10-byte header
        actual_chunk = CHUNK_SIZE - 10 
        chunks = [data[i:i + actual_chunk] for i in range(0, total_size, actual_chunk)]
        
        start_time = time.time()
        total_retries = 0

        for i, chunk in enumerate(chunks):
            # Construct Header: Chunks(1) + Index(1) + CRC(4) + TotalSize(4)
            header = bytes([len(chunks), i]) + crc_val.to_bytes(4, 'big') + total_size.to_bytes(4, 'big')
            p_data = header + chunk
            
            success = False
            retry_count = 0
            max_retries = 5
            
            while not success and retry_count < max_retries:
                try:
                    interface.sendData(p_data, destinationId=target_id, portNum=PORT_NUM, wantAck=True)
                    success = True
                except Exception as e:
                    retry_count += 1
                    total_retries += 1
                    print(f"\n[!] Chunk {i+1}/{len(chunks)} failed (attempt {retry_count}/{max_retries}): {e}")
                    if retry_count < max_retries:
                        time.sleep(3)
            
            if not success:
                print(f"\n[X] Failed to send chunk {i+1} after {max_retries} attempts. Aborting.")
                return

            sent_bytes = sum(len(c) for c in chunks[:i+1])
            draw_progress_bar(i+1, len(chunks), start_time, sent_bytes, total_size, total_retries)
            time.sleep(6)
            
        duration = time.time() - start_time
        avg_speed = total_size / duration
        print(f"\n\n--- TRANSFER SUMMARY ---")
        print(f"Final Size: {total_size} bytes")
        print(f"Time Taken: {duration:.1f} seconds")
        print(f"Avg Speed : {avg_speed:.2f} B/s")
        print(f"Retries   : {total_retries}")
        print(f"------------------------\n")

    except Exception as e: print(f"\n[X] Error: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["send", "receive"])
    parser.add_argument("target", nargs="?"); parser.add_argument("file", nargs="?")
    parser.add_argument("--res", type=int, default=80); parser.add_argument("--qual", type=int, default=15)
    parser.add_argument("--port", help="Serial port (e.g., /dev/ttyUSB0)")
    args = parser.parse_args()

    try:
        print("[*] Connecting to Meshtastic device...")
        # Add connection timeout and optional port specification
        if args.port:
            iface = meshtastic.serial_interface.SerialInterface(devPath=args.port, connectNow=True)
        else:
            iface = meshtastic.serial_interface.SerialInterface(connectNow=True)
        print("[+] Connected successfully")
        
        if args.mode == "receive":
            threading.Thread(target=start_web_server, daemon=True).start()
            pub.subscribe(on_receive, "meshtastic.receive")
            print(f"[*] Receiver Active. Web Port: {WEB_PORT}")
            while True: time.sleep(1)
        elif args.mode == "send":
            send_image(iface, args.target, args.file, args.res, args.qual)
    except Exception as e:
        print(f"[X] Connection Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
