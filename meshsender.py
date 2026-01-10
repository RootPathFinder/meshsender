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
USE_WEBP = True  # WebP provides ~30% better compression than JPEG
COMPRESS_PAYLOAD = True  # Compress chunks with zlib (if beneficial)
CHUNK_DELAY = 4  # Default delay between chunks in seconds (can be overridden via CLI)
MIN_CHUNK_DELAY = 1  # Minimum allowed chunk delay
MAX_CHUNK_DELAY = 10  # Maximum allowed chunk delay
ADAPTIVE_DELAY = True  # Automatically adjust delay based on success rate
ADAPTIVE_SUCCESS_THRESHOLD_LOW = 0.9  # Increase delay if success rate below this (90%)
ADAPTIVE_SUCCESS_THRESHOLD_HIGH = 0.98  # Decrease delay if success rate above this (98%)
ADAPTIVE_DELAY_INCREASE_FACTOR = 1.2  # Multiply delay by this when success is low
ADAPTIVE_DELAY_DECREASE_FACTOR = 0.95  # Multiply delay by this when success is high
STALL_CHECK_INTERVAL = 15  # Check for stalled transfers every N seconds (was 10)
STALL_REQUEST_TIMEOUT = 20  # Request missing chunks after N seconds of no updates
TRANSFER_TIMEOUT = 60  # Mark transfer as timed out after N seconds (adaptive)
CHUNK_OVERHEAD_BUFFER = 2  # Seconds of overhead per chunk for timeout calculation
TIMEOUT_MULTIPLIER = 1.5  # Multiply expected duration for adaptive timeout
MAX_RETRIES = 3  # Maximum retry attempts per chunk (was 5)
INITIAL_RETRY_DELAY = 3  # Initial retry delay in seconds
VERBOSE = False  # Verbose logging mode
DEBUG = False  # Debug logging mode (even more verbose)
image_buffer = {}
ack_messages = {}  # Store ACK messages from receivers: {sender_id: {transfer_id: [chunk_indices]}}
completed_transfers = {}  # Track completed transfers: {sender_transfer_id: timestamp}

if not os.path.exists(GALLERY_DIR):
    os.makedirs(GALLERY_DIR)

# --- WEB SERVER LOGIC ---
class GalleryHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress HTTP logs to keep console clean
        pass
    
    def log_error(self, format, *args):
        # Suppress BrokenPipeError and other client disconnects
        if 'Broken pipe' in str(args) or 'ConnectionResetError' in str(args):
            return
        http.server.SimpleHTTPRequestHandler.log_error(self, format, *args)
    
    def do_GET(self):
        if self.path == '/image.jpg':
            images = sorted([f for f in os.listdir(GALLERY_DIR) if f.endswith(('.jpg', '.webp'))], reverse=True)
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
                    show_missing_chunks(sender)  # Show which chunks are missing
                
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
            images = sorted([f for f in os.listdir(GALLERY_DIR) if f.endswith(('.jpg', '.webp'))], reverse=True)[:20]
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
            margin-bottom: 40px;            min-height: 100px;
            transition: opacity 0.3s ease;
        }
        
        #progress-area.hidden {
            opacity: 0;
            min-height: 0;
            margin-bottom: 0;
            pointer-events: none;            min-height: 100px;
            transition: opacity 0.3s ease;
        }
        
        #progress-area.hidden {
            opacity: 0;
            min-height: 0;
            margin-bottom: 0;
            pointer-events: none;
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
        
        .hidden { 
            opacity: 0 !important;
            min-height: 0 !important;
            margin-bottom: 0 !important;
        }
        
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
        let lastProgressHTML = '';
        
        function updateProgress() {
            fetch('/progress').then(r => r.json()).then(data => {
                const area = document.getElementById('progress-area');
                const container = document.getElementById('progress-container');
                
                if (data.length === 0) {
                    area.classList.add('hidden');
                    return;
                }
                
                // Build new HTML  
                const newHTML = data.map(p => {
                    const emoji = p.status === 'timeout' ? '&#9888;' : '&#128225;';
                    const timeoutClass = p.status === 'timeout' ? 'timeout' : 'pulsing';
                    const badgeClass = p.status === 'timeout' ? 'timeout' : '';
                    const timeoutLabel = p.status === 'timeout' ? '<span style="font-size:0.75rem; opacity:0.8;"> (TIMED OUT)</span>' : '';
                    
                    return `
                    <div class="progress-item ${timeoutClass}" data-sender="${p.sender}">
                        <div class="progress-header">
                            <span class="sender-badge ${badgeClass}">
                                ${emoji} ${p.sender}${timeoutLabel}
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
                    `;
                }).join('');
                
                // Only update if content changed to prevent flashing
                if (newHTML !== lastProgressHTML) {
                    container.innerHTML = newHTML;
                    lastProgressHTML = newHTML;
                }
                
                area.classList.remove('hidden');
            });
        }
        
        setInterval(updateProgress, 1000);
        updateProgress();
    </script>
</head>
<body>
    <div class="container">
        <header>
            <h1>Mesh Gallery</h1>
            <div class="subtitle">Meshtastic Image Receiver</div>
        </header>
        
        <div id="progress-area" class="hidden">
            <h2>Incoming Transfers</h2>
            <div id="progress-container"></div>
        </div>
        
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
def log_verbose(msg):
    """Print verbose log message"""
    if VERBOSE or DEBUG:
        print(msg)

def log_debug(msg):
    """Print debug log message"""
    if DEBUG:
        print(msg)

def show_missing_chunks(sender):
    """Debug function to show which chunks are missing"""
    if sender in image_buffer:
        missing = [i for i, chunk in enumerate(image_buffer[sender]['chunks']) if chunk is None]
        if missing:
            total = len(image_buffer[sender]['chunks'])
            received = total - len(missing)
            print(f"\n[!] Transfer incomplete: {received}/{total} chunks received")
            print(f"[!] Missing chunks: {missing[:20]}")  # Show first 20 missing
            if len(missing) > 20:
                print(f"[!] ... and {len(missing) - 20} more")

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
def on_ack(packet, interface):
    """Handle ACK messages from receiver"""
    global ack_messages
    try:
        if 'decoded' in packet:
            decoded = packet['decoded']
            
            # Check if it's a text message
            if 'text' in decoded:
                text = decoded.get('text', '')
                sender = packet.get('fromId', 'unknown')
                
                # Parse ACK messages: "ACK:transfer_id:chunk,list" or "OK:transfer_id"
                if text.startswith('ACK:'):
                    parts = text.split(':')
                    if len(parts) >= 3:
                        transfer_id = int(parts[1], 16)
                        chunk_list = [int(x) for x in parts[2].split(',') if x]
                        
                        if sender not in ack_messages:
                            ack_messages[sender] = {}
                        ack_messages[sender][transfer_id] = chunk_list
                        print(f"\n[ACK] Received from {sender}: {len(chunk_list)} chunks for transfer {transfer_id:08x}")
                
                elif text.startswith('REQ:'):
                    parts = text.split(':')
                    if len(parts) >= 3:
                        transfer_id = int(parts[1], 16)
                        requested_chunks = [int(x) for x in parts[2].split(',') if x]
                        
                        if sender not in ack_messages:
                            ack_messages[sender] = {}
                        ack_messages[sender][transfer_id] = {'type': 'REQ', 'chunks': requested_chunks}
                        print(f"\n[REQ] Receiver requesting {len(requested_chunks)} chunks for transfer {transfer_id:08x}")
                
                elif text.startswith('OK:'):
                    parts = text.split(':')
                    if len(parts) >= 2:
                        transfer_id = int(parts[1], 16)
                        if sender not in ack_messages:
                            ack_messages[sender] = {}
                        ack_messages[sender][transfer_id] = 'COMPLETE'
                        print(f"\n[OK] Transfer {transfer_id:08x} confirmed complete by {sender}")
    except Exception as e:
        print(f"\n[!] ACK parse error: {e}")

def check_stalled_transfers(interface):
    """Background thread to check for stalled transfers and request missing chunks"""
    while True:
        time.sleep(STALL_CHECK_INTERVAL)  # Check periodically
        
        for buffer_key in list(image_buffer.keys()):
            if buffer_key not in image_buffer:
                continue
                
            transfer = image_buffer[buffer_key]
            elapsed_since_update = time.time() - transfer['last_update']
            
            # If transfer has stalled and chunks are missing, request them
            if elapsed_since_update > STALL_REQUEST_TIMEOUT and None in transfer['chunks']:
                missing_indices = [i for i, c in enumerate(transfer['chunks']) if c is None]
                if missing_indices:
                    req_msg = f"REQ:{transfer['transfer_id']:08x}:{','.join(map(str, missing_indices))}"
                    interface.sendText(req_msg, destinationId=transfer['sender'])
                    print(f"\n[REQ] Requesting {len(missing_indices)} missing chunks: {missing_indices[:10]}{'...' if len(missing_indices) > 10 else ''}")
                    transfer['last_update'] = time.time()  # Reset timer
            
            # Adaptive timeout based on transfer size and expected completion time
            expected_duration = len(transfer['chunks']) * (CHUNK_DELAY + CHUNK_OVERHEAD_BUFFER)  # Estimate with buffer
            adaptive_timeout = max(TRANSFER_TIMEOUT, min(expected_duration * TIMEOUT_MULTIPLIER, 300))  # Cap at 5 minutes
            
            # Timeout transfer after adaptive timeout of no new data
            if elapsed_since_update > adaptive_timeout:
                count = sum(1 for x in transfer['chunks'] if x is not None)
                total = len(transfer['chunks'])
                missing = [i for i, c in enumerate(transfer['chunks']) if c is None]
                
                print(f"\n[X] Transfer from {buffer_key} timed out (no data for {adaptive_timeout:.0f}s)")
                print(f"\n[!] Transfer incomplete: {count}/{total} chunks received")
                if missing:
                    print(f"[!] Missing chunks: {missing[:20]}{'...' if len(missing) > 20 else ''}")
                
                del image_buffer[buffer_key]

def on_receive(packet, interface):
    global image_buffer
    try:
        if 'decoded' in packet:
            decoded = packet['decoded']
            
            if decoded.get('portnum') == PORT_NUM or decoded.get('portnum') == 'PRIVATE_APP':
                data = decoded.get('payload')
                
                # Validate minimum header size
                if len(data) < 15:
                    print(f"\n[!] Packet too small: {len(data)} bytes (need 15)")
                    return
                
                # Header Structure:
                # [0-3]: Transfer ID (4 bytes)
                # [4]: Total Chunks (1 byte)
                # [5]: Chunk Index (1 byte)
                # [6]: Compressed Flag (1 byte)
                # [7-10]: CRC32 (4 bytes)
                # [11-14]: Total Byte Size (4 bytes)
                transfer_id = int.from_bytes(data[0:4], byteorder='big')
                total_chunks = data[4]
                chunk_index = data[5]
                compressed_flag = data[6]
                crc_val = int.from_bytes(data[7:11], byteorder='big')
                reported_total_size = int.from_bytes(data[11:15], byteorder='big')
                payload = data[15:]
                
                # Validate parsed values
                if total_chunks == 0 or total_chunks > 255:
                    print(f"\n[!] Invalid total_chunks: {total_chunks}")
                    return
                if chunk_index >= total_chunks:
                    print(f"\n[!] Invalid chunk_index: {chunk_index} >= {total_chunks}")
                    return
                if reported_total_size > 10000000:  # 10MB sanity check
                    print(f"\n[!] Unrealistic size: {reported_total_size} bytes")
                    return
                
                sender = packet.get('fromId', 'unknown')
                buffer_key = f"{sender}_{transfer_id}"
                
                # Check if this transfer was already completed (ignore retransmissions)
                if buffer_key in completed_transfers:
                    elapsed = time.time() - completed_transfers[buffer_key]
                    if elapsed < 300:  # Keep completed transfers for 5 minutes
                        # Resend OK confirmation
                        ok_msg = f"OK:{transfer_id:08x}"
                        interface.sendText(ok_msg, destinationId=sender)
                        return
                    else:
                        # Clean up old completion record
                        del completed_transfers[buffer_key]
                
                # Check if this is a new transfer
                if buffer_key not in image_buffer:
                    # Clean up old transfers from this sender
                    old_keys = [k for k in image_buffer.keys() if k.startswith(sender + '_')]
                    if old_keys:
                        for old_key in old_keys:
                            old_chunks = sum(1 for x in image_buffer[old_key]['chunks'] if x is not None)
                            print(f"\n[!] Discarding old transfer {old_key} ({old_chunks}/{len(image_buffer[old_key]['chunks'])} chunks)")
                            del image_buffer[old_key]
                    
                    image_buffer[buffer_key] = {
                        'sender': sender,
                        'transfer_id': transfer_id,
                        'chunks': [None]*total_chunks, 
                        'start': time.time(), 
                        'last_update': time.time(),
                        'crc': crc_val, 
                        'bytes': 0,
                        'total_size': reported_total_size,
                        'status': 'active',
                        'compressed': bool(compressed_flag)
                    }
                    comp_str = ' (compressed)' if compressed_flag else ''
                    print(f"\n[!] Incoming Image from {sender} (ID: {transfer_id:08x}, {reported_total_size} bytes{comp_str})")
                    print(f"    Total chunks: {total_chunks}")
                    log_debug(f"[DEBUG] New transfer: buffer_key={buffer_key}, CRC={crc_val:08x}")
                
                # Store the chunk (allow overwrites for retransmissions)
                if image_buffer[buffer_key]['chunks'][chunk_index] is None:
                    print(f"  [RCV] Chunk {chunk_index}/{total_chunks-1} ({len(payload)} bytes)")
                    image_buffer[buffer_key]['bytes'] += len(payload)
                    log_verbose(f"[VERBOSE] New chunk received: index={chunk_index}, size={len(payload)}")
                else:
                    print(f"  [RETRY] Chunk {chunk_index}/{total_chunks-1} ({len(payload)} bytes)")
                    log_verbose(f"[VERBOSE] Duplicate chunk (retransmission): index={chunk_index}")
                
                image_buffer[buffer_key]['chunks'][chunk_index] = payload
                image_buffer[buffer_key]['last_update'] = time.time()
                image_buffer[buffer_key]['status'] = 'active'
                
                count = sum(1 for x in image_buffer[buffer_key]['chunks'] if x is not None)
                draw_progress_bar(count, total_chunks, image_buffer[buffer_key]['start'], image_buffer[buffer_key]['bytes'], image_buffer[buffer_key]['total_size'])

                # Check if transfer appears complete (or stalled)
                elapsed_since_update = time.time() - image_buffer[buffer_key]['last_update']
                
                # If we haven't received a chunk in the stall timeout and some are missing, request them
                if elapsed_since_update > STALL_REQUEST_TIMEOUT and None in image_buffer[buffer_key]['chunks']:
                    missing_indices = [i for i, c in enumerate(image_buffer[buffer_key]['chunks']) if c is None]
                    if missing_indices:
                        req_msg = f"REQ:{transfer_id:08x}:{','.join(map(str, missing_indices))}"
                        interface.sendText(req_msg, destinationId=sender)
                        print(f"\n[REQ] Requesting {len(missing_indices)} missing chunks: {missing_indices[:10]}{'...' if len(missing_indices) > 10 else ''}")
                        image_buffer[buffer_key]['last_update'] = time.time()  # Reset timer

                if None not in image_buffer[buffer_key]['chunks']:
                    print(f"\n[+] All {total_chunks} chunks received!")
                    full = b"".join(image_buffer[buffer_key]['chunks'])
                    
                    # Verify CRC on assembled chunks BEFORE decompressing
                    if zlib.crc32(full) & 0xFFFFFFFF != image_buffer[buffer_key]['crc']:
                        print(f"\n[X] CRC mismatch! Image corrupted.")
                        del image_buffer[buffer_key]
                        return
                    
                    # Decompress if needed (AFTER CRC check)
                    if image_buffer[buffer_key].get('compressed', False):
                        try:
                            full = zlib.decompress(full)
                            print(f"\n[+] Decompressed payload")
                        except Exception as e:
                            print(f"\n[X] Decompression failed: {e}")
                            del image_buffer[buffer_key]
                            return
                    
                    try:
                        img = Image.open(io.BytesIO(full))
                        
                        # Detect format and save accordingly
                        img_format = img.format if img.format else 'JPEG'
                        ext = 'webp' if img_format == 'WEBP' else 'jpg'
                        fname = f"{GALLERY_DIR}/img_{int(time.time())}.{ext}"
                        img.save(fname)
                        duration = time.time() - image_buffer[buffer_key]['start']
                        print(f"\n[SUCCESS] {len(full)} bytes in {duration:.1f}s")
                        print(f"[+] Saved to: {fname}")
                        
                        # Mark as completed
                        completed_transfers[buffer_key] = time.time()
                        
                        # Send OK confirmation to sender (multiple times for reliability)
                        ok_msg = f"OK:{transfer_id:08x}"
                        for _ in range(3):
                            interface.sendText(ok_msg, destinationId=sender)
                            time.sleep(0.5)
                        print(f"[+] Sent OK confirmation to {sender} (3x)")
                    except Exception as e:
                        print(f"\n[X] Failed to save image: {e}")
                        import traceback
                        traceback.print_exc()
                    
                    del image_buffer[buffer_key]
    except Exception as e:
        print(f"\n[!] Receive error: {e}")

def send_image(interface, target_id, file_path, res, qual, metadata=None, chunk_delay=None):
    """
    Send an image over the mesh network
    
    Args:
        interface: Meshtastic interface
        target_id: Target node ID
        file_path: Path to image file
        res: Image resolution
        qual: Image quality
        metadata: Optional metadata dict
        chunk_delay: Delay between chunks in seconds (uses CHUNK_DELAY if None)
    """
    # Use provided chunk_delay or fall back to global CHUNK_DELAY
    if chunk_delay is None:
        chunk_delay = CHUNK_DELAY
    
    # Validate chunk delay
    chunk_delay = max(MIN_CHUNK_DELAY, min(MAX_CHUNK_DELAY, chunk_delay))
    
    try:
        img = Image.open(file_path)
        img.thumbnail((res, res)) 
        
        # Determine best format
        use_webp = USE_WEBP
        
        # Save to JPEG first
        tmp_jpeg = io.BytesIO()
        img.save(tmp_jpeg, format='JPEG', quality=qual, optimize=True, progressive=True)
        jpeg_size = len(tmp_jpeg.getvalue())
        
        # Only try WebP if enabled and likely to be beneficial
        if use_webp:
            tmp_webp = io.BytesIO()
            img.save(tmp_webp, format='WEBP', quality=qual, method=6)  # method=6 is slower but better compression
            webp_size = len(tmp_webp.getvalue())
            
            if webp_size < jpeg_size:
                format_name = 'WEBP'
                size_kb = webp_size / 1024
                print(f"[+] WebP is {((jpeg_size-webp_size)/jpeg_size*100):.1f}% smaller than JPEG")
            else:
                format_name = 'JPEG'
                size_kb = jpeg_size / 1024
                use_webp = False
                print(f"[*] JPEG is smaller, using JPEG")
        else:
            format_name = 'JPEG'
            size_kb = jpeg_size / 1024
        
        stats_info = f"{res}px {qual}Q {size_kb:.1f}KB {format_name}"
        img = add_diagnostic_overlay(img, stats_info, metadata)
        
        # Save final image with optimizations
        buf = io.BytesIO()
        if use_webp:
            img.save(buf, format='WEBP', quality=qual, method=6)
        else:
            img.save(buf, format='JPEG', quality=qual, optimize=True, progressive=True)
        
        data = buf.getvalue()
        total_size = len(data)
        
        # Generate unique transfer ID
        transfer_id = int(time.time() * 1000) & 0xFFFFFFFF
        
        print(f"\n[*] SENDING: {file_path}")
        print(f"[*] Transfer ID: {transfer_id:08x}")
        print(f"[*] TOTAL PAYLOAD: {total_size} bytes")
        
        crc_val = zlib.crc32(data) & 0xFFFFFFFF
        print(f"[*] CRC: {crc_val:08x}")
        
        # Try compressing the entire payload
        compressed_data = None
        if COMPRESS_PAYLOAD and total_size > 500:  # Only compress if worth the overhead
            compressed_data = zlib.compress(data, level=9)
            print(f"[*] Compressed: {len(data)} -> {len(compressed_data)} bytes")
            if len(compressed_data) < total_size * 0.95:  # Only use if >5% savings
                compression_ratio = (1 - len(compressed_data) / total_size) * 100
                print(f"[+] Compression: {total_size} -> {len(compressed_data)} bytes ({compression_ratio:.1f}% savings)")
                data = compressed_data
                total_size = len(compressed_data)
                # Recalculate CRC for compressed data
                crc_val = zlib.crc32(data) & 0xFFFFFFFF
                print(f"[+] New CRC after compression: {crc_val:08x}")
            else:
                print(f"[-] Compression not beneficial ({len(compressed_data)} >= {total_size * 0.95:.0f}), skipping")
                compressed_data = None
        
        # Adjust actual_chunk to account for the header (15 bytes: transfer_id(4) + metadata(11))
        actual_chunk = CHUNK_SIZE - 15
        chunks = [data[i:i + actual_chunk] for i in range(0, total_size, actual_chunk)]
        
        print(f"[*] Creating {len(chunks)} chunks of {actual_chunk} bytes each")
        print(f"[*] Total data to send: {total_size} bytes")
        
        start_time = time.time()
        total_retries = 0
        failed_chunks = 0
        successful_chunks = 0
        ack_received = set()  # Track which chunks have been acknowledged
        
        # Adaptive delay tracking
        current_delay = chunk_delay

        for i, chunk in enumerate(chunks):
            # Construct Header: TransferID(4) + TotalChunks(1) + Index(1) + Compressed(1) + CRC(4) + TotalSize(4)
            compressed_flag = 1 if compressed_data else 0
            header = transfer_id.to_bytes(4, 'big') + bytes([len(chunks), i, compressed_flag]) + crc_val.to_bytes(4, 'big') + total_size.to_bytes(4, 'big')
            p_data = header + chunk
            
            log_debug(f"[DEBUG] Sending chunk {i+1}/{len(chunks)}: header={len(header)}B, payload={len(chunk)}B, total={len(p_data)}B")
            
            success = False
            retry_count = 0
            max_retries = MAX_RETRIES
            
            while not success and retry_count < max_retries:
                try:
                    interface.sendData(p_data, destinationId=target_id, portNum=PORT_NUM, wantAck=True)
                    success = True
                    successful_chunks += 1
                    log_verbose(f"[VERBOSE] Chunk {i+1}/{len(chunks)} sent successfully")
                except Exception as e:
                    retry_count += 1
                    total_retries += 1
                    failed_chunks += 1
                    # Exponential backoff: 3s, 6s, 12s
                    retry_delay = INITIAL_RETRY_DELAY * (2 ** (retry_count - 1))
                    print(f"\n[!] Chunk {i+1}/{len(chunks)} failed (attempt {retry_count}/{max_retries}): {e}")
                    if retry_count < max_retries:
                        print(f"[*] Retrying in {retry_delay}s...")
                        time.sleep(retry_delay)
            
            if not success:
                print(f"\n[X] Failed to send chunk {i+1} after {max_retries} attempts. Aborting.")
                return

            # Track actual payload bytes sent (matching how receiver counts)
            sent_bytes = sum(len(c) for c in chunks[:i+1])
            draw_progress_bar(i+1, len(chunks), start_time, sent_bytes, total_size, total_retries)
            
            # Adaptive delay based on success rate (if enabled)
            if ADAPTIVE_DELAY and i > 0:
                success_rate = successful_chunks / (successful_chunks + failed_chunks) if (successful_chunks + failed_chunks) > 0 else 1.0
                old_delay = current_delay
                if success_rate < ADAPTIVE_SUCCESS_THRESHOLD_LOW:  # If less than 90% success, increase delay
                    current_delay = min(MAX_CHUNK_DELAY, current_delay * ADAPTIVE_DELAY_INCREASE_FACTOR)
                elif success_rate > ADAPTIVE_SUCCESS_THRESHOLD_HIGH and current_delay > MIN_CHUNK_DELAY:  # If great success, can reduce slightly
                    current_delay = max(MIN_CHUNK_DELAY, current_delay * ADAPTIVE_DELAY_DECREASE_FACTOR)
                
                if abs(current_delay - old_delay) > 0.01:
                    log_verbose(f"[VERBOSE] Adaptive delay adjusted: {old_delay:.2f}s -> {current_delay:.2f}s (success rate: {success_rate:.1%})")
            
            log_debug(f"[DEBUG] Waiting {current_delay:.2f}s before next chunk")
            time.sleep(current_delay)
        
        print(f"\n[*] Initial send complete. Waiting for receiver...")
        
        # Wait for receiver to request missing chunks or send OK
        wait_round = 0
        max_wait_rounds = 10  # Wait up to 150 seconds (10 * 15s)
        transfer_complete = False
        
        while wait_round < max_wait_rounds and not transfer_complete:
            time.sleep(15)
            
            # Check if we got a message from target
            if target_id in ack_messages and transfer_id in ack_messages[target_id]:
                status = ack_messages[target_id][transfer_id]
                
                if status == 'COMPLETE':
                    print(f"\n[✓] Transfer confirmed complete by receiver!")
                    transfer_complete = True
                    break
                
                # Check if it's a REQ message with requested chunks
                if isinstance(status, dict) and status.get('type') == 'REQ':
                    requested_chunks = status.get('chunks', [])
                    print(f"\n[*] Sending {len(requested_chunks)} requested chunks: {requested_chunks[:10]}{'...' if len(requested_chunks) > 10 else ''}")
                    
                    for chunk_idx in requested_chunks:
                        if chunk_idx >= len(chunks):
                            continue
                        
                        chunk = chunks[chunk_idx]
                        compressed_flag = 1 if compressed_data else 0
                        header = transfer_id.to_bytes(4, 'big') + bytes([len(chunks), chunk_idx, compressed_flag]) + crc_val.to_bytes(4, 'big') + total_size.to_bytes(4, 'big')
                        p_data = header + chunk
                        
                        try:
                            interface.sendData(p_data, destinationId=target_id, portNum=PORT_NUM, wantAck=True)
                            total_retries += 1
                            print(f"  [RETRY] Sent chunk {chunk_idx}/{len(chunks)-1}")
                        except Exception as e:
                            print(f"  [!] Failed to resend chunk {chunk_idx}: {e}")
                        
                        time.sleep(CHUNK_DELAY)
                    
                    # Clear the REQ so we don't process it again
                    del ack_messages[target_id][transfer_id]
            else:
                print(f"\n[*] Waiting... ({wait_round + 1}/{max_wait_rounds})")
            
            wait_round += 1
        
        if not transfer_complete:
            print(f"\n[!] Transfer may be incomplete (no OK confirmation received)")
        
        duration = time.time() - start_time
        avg_speed = total_size / duration
        print(f"\n\n--- TRANSFER SUMMARY ---")
        print(f"Chunks Sent: {len(chunks)}")
        print(f"Final Size: {total_size} bytes")
        print(f"Time Taken: {duration:.1f} seconds")
        print(f"Avg Speed : {avg_speed:.2f} B/s")
        print(f"Retries   : {total_retries}")
        print(f"------------------------\n")

    except Exception as e:
        import traceback
        print(f"\n[X] Error: {e}")
        traceback.print_exc()

def main():
    parser = argparse.ArgumentParser(description="Meshsender - Send and receive images over Meshtastic LoRa mesh")
    parser.add_argument("mode", choices=["send", "receive"], help="Send or receive mode")
    parser.add_argument("target", nargs="?", help="Target node ID (for send mode)")
    parser.add_argument("file", nargs="?", help="Image file to send (for send mode)")
    parser.add_argument("--res", type=int, default=80, help="Image resolution in pixels (default: 80)")
    parser.add_argument("--qual", type=int, default=15, help="Image quality 1-100 (default: 15)")
    parser.add_argument("--port", help="Serial port (e.g., /dev/ttyUSB0)")
    parser.add_argument("--chunk-delay", type=float, default=CHUNK_DELAY, 
                        help=f"Delay between chunks in seconds (default: {CHUNK_DELAY}, range: {MIN_CHUNK_DELAY}-{MAX_CHUNK_DELAY})")
    parser.add_argument("--no-adaptive", action="store_true", 
                        help="Disable adaptive chunk delay adjustment")
    parser.add_argument("--fast", action="store_true",
                        help="Fast mode: use minimum chunk delay (1s) and disable adaptive delay")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging mode")
    parser.add_argument("--debug", action="store_true",
                        help="Debug logging mode (very verbose)")
    args = parser.parse_args()
    
    # Update global logging settings
    global VERBOSE, DEBUG
    if args.debug:
        DEBUG = True
        VERBOSE = True
        print("[*] Debug mode enabled")
    elif args.verbose:
        VERBOSE = True
        print("[*] Verbose mode enabled")
    
    # Handle fast mode
    if args.fast:
        args.chunk_delay = MIN_CHUNK_DELAY
        args.no_adaptive = True
        print(f"[*] Fast mode enabled: chunk_delay={MIN_CHUNK_DELAY}s, adaptive=off")
    
    # Update global adaptive delay setting
    global ADAPTIVE_DELAY
    if args.no_adaptive:
        ADAPTIVE_DELAY = False

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
            threading.Thread(target=check_stalled_transfers, args=(iface,), daemon=True).start()
            pub.subscribe(on_receive, "meshtastic.receive")
            print(f"[*] Receiver Active. Web Port: {WEB_PORT}")
            while True: time.sleep(1)
        elif args.mode == "send":
            # Subscribe to receive ACK messages
            pub.subscribe(on_ack, "meshtastic.receive")
            
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
            send_image(iface, args.target, args.file, args.res, args.qual, metadata, chunk_delay=args.chunk_delay)
    except Exception as e:
        print(f"[X] Connection Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
