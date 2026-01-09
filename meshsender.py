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
        
        if self.path == '/progress':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            
            # Get current transfer status and clean up stale transfers
            TIMEOUT_SECONDS = 60
            progress_data = []
            stale_senders = []
            
            for sender, data in image_buffer.items():
                received = sum(1 for x in data['chunks'] if x is not None)
                total = len(data['chunks'])
                percent = int((received / total) * 100) if total > 0 else 0
                elapsed = time.time() - data['start']
                time_since_update = time.time() - data.get('last_update', data['start'])
                bps = data['bytes'] / elapsed if elapsed > 0 else 0
                
                # Check for timeout
                status = data.get('status', 'active')
                if time_since_update > TIMEOUT_SECONDS and status == 'active':
                    data['status'] = 'timeout'
                    status = 'timeout'
                    print(f"\n[X] Transfer from {sender} timed out (no data for {TIMEOUT_SECONDS}s)")
                
                # Mark for cleanup if timed out for too long (2 minutes)
                if time_since_update > 120:
                    stale_senders.append(sender)
                    continue
                
                progress_data.append({
                    'sender': sender,
                    'percent': percent,
                    'received': received,
                    'total': total,
                    'bytes': data['bytes'],
                    'total_bytes': data['total_size'],
                    'speed': f"{bps:.1f} B/s" if status == 'active' else 'Stalled',
                    'elapsed': f"{elapsed:.1f}s",
                    'status': status
                })
            
            # Clean up very old stale transfers
            for sender in stale_senders:
                del image_buffer[sender]
            
            import json
            self.wfile.write(json.dumps(progress_data).encode())
            return
        
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            images = sorted([f for f in os.listdir(GALLERY_DIR) if f.endswith('.jpg')], reverse=True)[:20]
            html = """
<html>
<head>
    <title>Mesh Gallery</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #0a0e27 0%, #1a1f3a 100%);
            color: #e0e6ed;
            min-height: 100vh;
            padding: 20px;
        }
        
        .container { max-width: 1400px; margin: 0 auto; }
        
        header {
            text-align: center;
            margin-bottom: 40px;
            padding: 30px 0;
        }
        
        h1 {
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 10px;
        }
        
        .subtitle {
            color: #8b92a7;
            font-size: 1rem;
        }
        
        #progress-area {
            margin-bottom: 40px;
        }
        
        .progress-item {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }
        
        .progress-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
        }
        
        .sender-badge {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 6px 16px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 0.9rem;
        }
        
        .sender-badge.timeout {
            background: linear-gradient(135deg, #ff6b6b 0%, #c92a2a 100%);
        }
        
        .progress-item.timeout {
            border-color: rgba(255, 107, 107, 0.3);
            animation: none;
        }
        
        .progress-item.timeout .progress-fill {
            background: linear-gradient(90deg, #ff6b6b 0%, #c92a2a 100%);
            box-shadow: 0 0 20px rgba(255, 107, 107, 0.5);
        }
        
        .progress-stats {
            display: flex;
            gap: 20px;
            flex-wrap: wrap;
            font-size: 0.85rem;
            color: #b8c1d8;
            margin-bottom: 12px;
        }
        
        .stat-item {
            display: flex;
            align-items: center;
            gap: 6px;
        }
        
        .stat-label { color: #8b92a7; }
        .stat-value { color: #e0e6ed; font-weight: 600; }
        
        .progress-bar {
            background: rgba(255, 255, 255, 0.08);
            height: 8px;
            border-radius: 10px;
            overflow: hidden;
            position: relative;
        }
        
        .progress-fill {
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            height: 100%;
            border-radius: 10px;
            transition: width 0.3s ease;
            box-shadow: 0 0 20px rgba(102, 126, 234, 0.5);
        }
        
        .progress-text {
            text-align: right;
            margin-top: 8px;
            font-size: 0.9rem;
            font-weight: 600;
            color: #667eea;
        }
        
        h2 {
            font-size: 1.5rem;
            margin-bottom: 24px;
            color: #e0e6ed;
        }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
            gap: 24px;
        }
        
        .card {
            background: rgba(255, 255, 255, 0.05);
            backdrop-filter: blur(10px);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 16px;
            overflow: hidden;
            transition: all 0.3s ease;
            box-shadow: 0 4px 16px rgba(0, 0, 0, 0.2);
        }
        
        .card:hover {
            transform: translateY(-8px);
            box-shadow: 0 12px 32px rgba(102, 126, 234, 0.3);
            border-color: rgba(102, 126, 234, 0.5);
        }
        
        .card img {
            width: 100%;
            height: 240px;
            object-fit: cover;
            display: block;
        }
        
        .card-info {
            padding: 16px;
            text-align: center;
        }
        
        .card-info small {
            color: #8b92a7;
            font-size: 0.85rem;
        }
        
        a {
            color: inherit;
            text-decoration: none;
        }
        
        .hidden { display: none; }
        
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        .pulsing { animation: pulse 2s ease-in-out infinite; }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #8b92a7;
        }
        
        .empty-state svg {
            width: 80px;
            height: 80px;
            margin-bottom: 20px;
            opacity: 0.3;
        }
    </style>
    <script>
        function updateProgress() {
            fetch('/progress').then(r => r.json()).then(data => {
                const area = document.getElementById('progress-area');
                if (data.length === 0) {
                    area.classList.add('hidden');
                    return;
                }
                area.classList.remove('hidden');
                area.innerHTML = '<h2>📥 Incoming Transfers</h2>' + data.map(p => `
                    <div class="progress-item ${p.status === 'timeout' ? 'timeout' : 'pulsing'}">
                        <div class="progress-header">
                            <span class="sender-badge ${p.status === 'timeout' ? 'timeout' : ''}">
                                ${p.status === 'timeout' ? '⚠️' : '📡'} ${p.sender}
                                ${p.status === 'timeout' ? '<span style="font-size:0.75rem; opacity:0.8;"> (TIMED OUT)</span>' : ''}
                            </span>
                        </div>
                        <div class="progress-stats">
                            <div class="stat-item">
                                <span class="stat-label">Chunks:</span>
                                <span class="stat-value">${p.received}/${p.total}</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Data:</span>
                                <span class="stat-value">${(p.bytes/1024).toFixed(1)}KB/${(p.total_bytes/1024).toFixed(1)}KB</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Speed:</span>
                                <span class="stat-value">${p.speed}</span>
                            </div>
                            <div class="stat-item">
                                <span class="stat-label">Time:</span>
                                <span class="stat-value">${p.elapsed}</span>
                            </div>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: ${p.percent}%"></div>
                        </div>
                        <div class="progress-text">${p.percent}%</div>
                    </div>
                `).join('');
            });
        }
        setInterval(updateProgress, 1000);
        updateProgress();
    </script>
</head>
<body>
    <div class="container">
        <header>
            <h1>📡 Mesh Gallery</h1>
            <div class="subtitle">Meshtastic Image Receiver</div>
        </header>
        
        <div id="progress-area" class="hidden"></div>
        
        <h2>Recent Images</h2>
"""
            if images:
                html += "<div class='grid'>"
                for img in images:
                    html += f"""
                        <div class='card'>
                            <a href='/{GALLERY_DIR}/{img}'>
                                <img src='/{GALLERY_DIR}/{img}' alt='{img}'>
                                <div class='card-info'>
                                    <small>{img}</small>
                                </div>
                            </a>
                        </div>
                    """
                html += "</div>"
            else:
                html += """
                    <div class='empty-state'>
                        <svg fill='currentColor' viewBox='0 0 20 20'>
                            <path d='M4 3a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V5a2 2 0 00-2-2H4zm12 12H4l4-8 3 6 2-4 3 6z'/>
                        </svg>
                        <h3>No images yet</h3>
                        <p>Images will appear here when received via mesh</p>
                    </div>
                """
            html += """
    </div>
</body>
</html>
"""
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

def add_diagnostic_overlay(img, stats_text, metadata=None):
    draw = ImageDraw.Draw(img, "RGBA")
    ts = time.strftime("%m/%d/%y %H:%M")
    
    # Build text lines
    lines = [f"{ts} | {stats_text}"]
    
    # Add camera metadata if provided
    if metadata:
        if 'exposure' in metadata and 'gain' in metadata:
            lines.append(f"Exp:{metadata['exposure']:.0f}ms G:{metadata['gain']:.1f} R:{metadata.get('red_gain', 1.0):.2f} B:{metadata.get('blue_gain', 1.0):.2f}")
    
    # Calculate box dimensions
    line_height = 12
    box_height = len(lines) * line_height + 2
    box_width = max(len(line) * 6 for line in lines) + 6
    
    # Draw background box
    draw.rectangle([0, img.height - box_height, box_width, img.height], fill=(0, 0, 0, 160))
    
    # Draw text lines
    for i, line in enumerate(lines):
        draw.text((3, img.height - box_height + 2 + i * line_height), line, fill=(255, 255, 255, 255))
    
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
                        'last_update': time.time(),
                        'crc': crc_val, 
                        'bytes': 0,
                        'total_size': reported_total_size,
                        'status': 'active'
                    }
                    print(f"\n[!] Incoming Image from {sender} ({reported_total_size} bytes)")
                
                if image_buffer[sender]['chunks'][chunk_index] is None:
                    image_buffer[sender]['chunks'][chunk_index] = payload
                    image_buffer[sender]['bytes'] += len(payload)
                    image_buffer[sender]['last_update'] = time.time()
                    image_buffer[sender]['status'] = 'active'
                
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

def send_image(interface, target_id, file_path, res, qual, metadata=None):
    try:
        img = Image.open(file_path)
        img.thumbnail((res, res)) 
        
        tmp = io.BytesIO()
        img.save(tmp, format='JPEG', quality=qual)
        size_kb = len(tmp.getvalue()) / 1024
        
        stats_info = f"{res}px {qual}Q {size_kb:.1f}KB"
        img = add_diagnostic_overlay(img, stats_info, metadata)
        
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
            # Check if metadata file exists
            metadata = None
            metadata_file = args.file + '.meta'
            if os.path.exists(metadata_file):
                try:
                    import json
                    with open(metadata_file, 'r') as f:
                        metadata = json.load(f)
                except Exception:
                    pass
            send_image(iface, args.target, args.file, args.res, args.qual, metadata)
    except Exception as e:
        print(f"[X] Connection Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
