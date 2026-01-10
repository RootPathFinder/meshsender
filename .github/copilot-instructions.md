# Meshsender AI Agent Instructions

## Project Overview

Meshsender is a low-bandwidth LoRa mesh image transmission system using Meshtastic. It fragments JPEG/WebP images into small chunks (~200 bytes) with CRC32 integrity verification, ACK handling, and retry logic for reliable delivery over constrained LoRa networks.

**Core Use Case**: Trail camera capturing night images on Raspberry Pi and transmitting over LoRa mesh to remote gallery viewer.

## Architecture

### Three-Component System

1. **meshsender.py** - Core sender/receiver with chunking protocol and web gallery
   - Bidirectional: runs in `send` or `receive` mode
   - Receiver mode starts HTTP server on port 5678 showing last 20 images
   - Protocol: 15-byte header (TransferID[4] + TotalChunks[1] + Index[1] + Compressed[1] + CRC[4] + TotalSize[4]) + payload

2. **takepic.py** - Raspberry Pi camera capture with auto-exposure and color correction
   - Standalone: captures image, then calls `meshsender.py send`
   - Auto-adjusts exposure/gain over 5 preview iterations targeting 90/255 brightness
   - Detects/corrects color casts (blue/red/yellow) common in night/IR cameras

3. **camera_daemon.py** - Trail camera daemon with motion detection and remote commands
   - Runs continuously, listens for mesh commands (`CAPTURE`, `MOTION_ON`, `STATUS`)
   - Motion detection via OpenCV frame differencing (>2% change threshold)
   - Reuses Meshtastic interface for sending (not subprocess) to avoid connection conflicts

### Data Flow

```
Sender: Image -> Resize -> WebP/JPEG (smaller) -> Optional zlib -> Chunks -> LoRa (4s delay between)
Receiver: Chunks -> CRC verification -> Decompress -> Save gallery/ -> Web view
ACK Protocol: Receiver sends OK:<transfer_id> 3x when complete, sender retries on REQ:<chunks>
```

## Critical Implementation Patterns

### 1. Meshtastic Interface Management

**Always use a single interface per process** - creating multiple SerialInterfaces causes USB serial conflicts.

```python
# CORRECT (daemon pattern):
iface = meshtastic.serial_interface.SerialInterface(connectNow=True)
send_image(iface, target_id, image_path, res, qual)  # Pass interface

# WRONG (causes "could not open port" errors):
subprocess.run([PYTHON_BIN, SENDER_SCRIPT, "send", ...])  # Opens 2nd interface
```

In [camera_daemon.py](camera_daemon.py#L76-L96), see how it dynamically imports meshsender and calls `send_image()` directly with the daemon's `iface` instead of subprocess.

### 2. Camera Resource Cleanup

**Always release Pi camera before subprocess or secondary usage**:

```python
picam2.stop()
picam2.close()
picam2 = None
time.sleep(1)  # Critical: camera needs time to fully release
```

Example in [camera_daemon.py](camera_daemon.py#L59-L65) before calling takepic.py. Without this, you get "camera already in use" errors.

### 3. Image Buffer Management (Transfer Tracking)

The `image_buffer` dict tracks incomplete transfers by `buffer_key = f"{sender}_{transfer_id:08x}"`:

```python
image_buffer[buffer_key] = {
    'chunks': [None] * total_chunks,
    'total_size': total_size,
    'bytes': 0,
    'start': time.time(),
    'last_update': time.time(),  # Updated on each chunk
    'status': 'active'  # or 'timeout'
}
```

See [meshsender.py](meshsender.py#L634-L648). The web `/progress` endpoint reads this to show live transfer status. Stale transfers (>60s no updates) are marked timeout, >120s are deleted.

### 4. Compression Decision Logic

Only compress if >5% savings (see [meshsender.py](meshsender.py#L783-L790)):

```python
compressed_data = zlib.compress(data, level=9)
if len(compressed_data) < total_size * 0.95:
    data = compressed_data
    crc_val = zlib.crc32(data) & 0xFFFFFFFF  # Recalculate CRC!
```

Compressed flag in byte[6] of header tells receiver to decompress.

### 5. Auto-Exposure Algorithm

Key insight from [takepic.py](takepic.py#L79-L121): Low-res preview iterations (640x480) tune exposure before high-res capture (2592x1944). Targets 90/255 brightness for night vision:

```python
# Over-exposure kills first (>5% blown highlights)
if analysis['overexposure_pct'] > 5.0:
    exposure = int(exposure * 0.6)
    gain = max(1.0, gain * 0.8)
    
# Then adjust brightness
if mean_brightness < target - 15:
    exposure = int(exposure * 1.5)  # Increase exposure first
    gain = min(8.0, gain * 1.3)     # Then gain
```

Color correction uses inverse ratios: if blue channel is 1.15x too high, apply blue_gain = 1/1.15 = 0.87.

### 6. Daemon Command Protocol

Only respond to DIRECT messages (not broadcasts) - see [camera_daemon.py](camera_daemon.py#L210-L213):

```python
to_id = packet.get('toId')
if not to_id or to_id == '^all':
    return  # Ignore channel messages
```

This prevents camera triggering on public chat. Commands: `CAPTURE`, `MOTION_ON`, `MOTION_OFF`, `STATUS`, `HELP`.

## Development Workflows

### Testing Image Transfer

```bash
# Terminal 1 (receiver):
python meshsender.py receive
# Opens http://localhost:5678 (auto-created gallery/)

# Terminal 2 (sender):
python meshsender.py send '!da56b70c' photo.jpg --res 320 --qual 40
# Adjust --res (80-720) and --qual (15-70) to balance quality vs transfer time
```

### Raspberry Pi Setup

```bash
# Install dependencies (Linux only for picamera2):
pip install -r requirements.txt

# Test camera capture only (no send):
python takepic.py '!target_id' --no-send --res 720 --qual 70

# Run daemon (listens for commands, auto-reconnects):
python camera_daemon.py '!da56b70c'
```

### Debugging Transfer Issues

1. **Check missing chunks**: `show_missing_chunks(sender)` in receiver (see [meshsender.py](meshsender.py#L477-L484))
2. **Verify CRC**: Compare sender CRC output with receiver's assembled image CRC
3. **Monitor ACKs**: Look for `[ACK]`, `[REQ]`, or `[OK]` messages in sender terminal
4. **Timeout handling**: Receiver marks transfers timeout after 60s no data ([meshsender.py](meshsender.py#L88-L93))

## Configuration Constants

Key tuning parameters in [meshsender.py](meshsender.py#L16-L22):

```python
PORT_NUM = 256        # Meshtastic port number
CHUNK_SIZE = 200      # Max LoRa payload (includes 15-byte header)
CHUNK_DELAY = 4       # Seconds between chunks (reduce for faster send, risk collisions)
USE_WEBP = True       # ~30% better compression than JPEG
COMPRESS_PAYLOAD = True  # zlib compression (use if >5% savings)
```

**Do not** increase CHUNK_SIZE beyond 237 bytes (Meshtastic max payload) or transfers will fail silently.

## Common Pitfalls

1. **Multiple Meshtastic connections**: Always pass `iface` as parameter, never create second SerialInterface
2. **Missing camera cleanup**: Always `stop()` + `close()` + `sleep(1)` before handing camera to another process
3. **CRC mismatch**: If compressing, recalculate CRC on compressed data, not original
4. **Ignoring timeout**: Receivers must mark stale transfers timeout and show missing chunks (UX clarity)
5. **Daemon broadcast storm**: Always check `to_id != '^all'` to ignore channel messages

## File Structure

```
meshsender.py         # Core protocol, sender/receiver, web gallery
takepic.py            # Pi camera capture with auto-exposure
camera_daemon.py      # Trail camera daemon (motion + commands)
requirements.txt      # meshtastic, Pillow, picamera2, opencv-python, numpy
gallery/              # Auto-created for received images
```

**No meshsender/ package is used** - top-level scripts import each other via `importlib.util.spec_from_file_location()` (see daemon's dynamic import).

## Python Environment

- **Target**: Python 3.9+ (uses `:=` walrus operator, f-strings)
- **Linux-only**: picamera2, opencv-python (Raspberry Pi specific)
- **Cross-platform**: meshsender.py send/receive works on any OS with Meshtastic USB device
