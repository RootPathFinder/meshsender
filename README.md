# Meshsender

A Python toolset for capturing, sending, and receiving images over Meshtastic-enabled LoRa mesh devices.

## Overview

Meshsender enables image transmission over low-bandwidth LoRa mesh networks by fragmenting JPEG data into small chunks with integrity verification. It includes a command-line interface for sending/receiving images and a Raspberry Pi helper for automated capture and transmission.

**Status**: Prototype (2026). Optimized for small thumbnails and reliable delivery over constrained LoRa networks.

## Features

- **Image Fragmentation**: Splits JPEG data into small chunks fitting LoRa payload constraints
- **Integrity Verification**: CRC32 checksums on full image payload before saving
- **Acknowledgments**: ACK handling for reliable transmission
- **Web Gallery**: Built-in HTTP server to browse received images at `http://localhost:5678`
- **Pi Camera Integration**: Capture high-sensitivity images from Raspberry Pi with long-exposure support
- **Transfer Metadata**: 10-byte headers with chunk indexing, CRC, and size information

## Requirements

### Hardware

- **Meshtastic Devices**: At least one LoRa device for sending and one for receiving (or a single device in relay mode)
- **Serial Connection**: USB connection to Meshtastic device
- **Raspberry Pi** (optional): For `takepic.py` image capture feature

### Software

- Python 3.9+
- Dependencies (see Installation)

## Installation

### 1. Clone or download the repository

```bash
git clone <repository-url>
cd meshsender
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

The following packages will be installed:
- **meshtastic**: Meshtastic Python API for device communication
- **Pillow**: Image processing (resize, format conversion, overlay)
- **PyPubSub**: Event-based message subscription for packet handling
- **pyserial**: Serial communication with Meshtastic devices
- **picamera2**: Raspberry Pi camera interface (Linux only)

### 3. Set up Meshtastic device

1. Connect your Meshtastic LoRa device via USB
2. Ensure the device is properly configured with a working mesh network
3. Verify connection with: `meshtastic --info`

### 4. Create gallery directory (optional)

The `gallery/` directory is created automatically on first run to store received images.

## Usage

### Receive Mode

Start the receiver on a device connected to your mesh network:

```bash
python meshsender.py receive
```

This will:
- Listen for incoming images on the mesh network
- Save received images to `gallery/img_<timestamp>.jpg`
- Start a web server at `http://localhost:5678` displaying the last 20 received images
- Display transfer progress in the terminal

**Example output:**
```
[*] Receiver Active. Web Port: 5678
[!] Incoming Image from !da56b70c (15234 bytes)
Progress: [===============>] 100% | 15234/15234B | 2543.0B/s | R:0
[SUCCESS] 15234 bytes in 6.1s
```

### Send Mode

Send an image to a specific node:

```bash
python meshsender.py send <target_node_id> <image_path> [--res <resolution>] [--qual <quality>]
```

**Parameters:**
- `target_node_id`: Meshtastic node ID (e.g., `!da56b70c`) or node name
- `image_path`: Path to the image file (JPEG, PNG, etc.)
- `--res`: Thumbnail resolution in pixels (default: 80, recommended: 80-720)
- `--qual`: JPEG quality 1-100 (default: 15, lower = more compression)

**Examples:**

Send a small thumbnail:
```bash
python meshsender.py send !da56b70c photo.jpg --res 80 --qual 15
```

Send a larger preview:
```bash
python meshsender.py send !da56b70c photo.jpg --res 320 --qual 40
```

**Features during send:**
- Automatically resizes image to target resolution
- Adds timestamp and compression stats as overlay
- Shows real-time transfer progress
- Retries failed chunks automatically
- Outputs transfer summary (size, duration, average speed)

### Raspberry Pi Camera Capture & Send

Use `takepic.py` to capture images with the Raspberry Pi camera and send them to the mesh:

```bash
python takepic.py
```

**Configuration** (edit `takepic.py`):

```python
TARGET_NODE = "!da56b70c"        # Node ID to send to
IMAGE_PATH = "/home/dave/small.jpg"  # Where to save captured image
PYTHON_BIN = "/path/to/python"   # Path to Python executable
SENDER_SCRIPT = "/path/to/meshsender.py"  # Path to meshsender.py
RES = "720"                       # Resolution
QUAL = "70"                       # Quality
```

**Features:**
- Captures high-sensitivity images with long exposure (1 second default)
- Applies manual gain and white balance for low-light scenarios
- Allows sensor warmup before capture
- Automatically sends captured image to mesh

**Typical workflow:**
```bash
# Capture image with camera and send to mesh in one command
python takepic.py
# Output:
# [*] Camera warming up for long exposure...
# [*] Capturing image...
# [+] Image saved to /home/dave/small.jpg
# [*] Sending to Node: !da56b70c
# [+] Transmission finished successfully.
```

## Transfer Configuration & Tuning

### Chunk Size

- **Current Setting**: 200 bytes per chunk (with 10-byte header = 210 bytes total)
- **Adjustment**: Edit `CHUNK_SIZE` in `meshsender.py` to fit your LoRa MTU
- **Typical LoRa MTU**: 240-250 bytes

### Image Resolution & Quality

| Resolution | Quality | Size (KB) | Send Time (~6s/chunk) |
|-----------|---------|----------|----------------------|
| 80px      | 15      | 1.2      | ~6 seconds           |
| 160px     | 25      | 3.5      | ~15 seconds          |
| 320px     | 40      | 8.2      | ~35 seconds          |
| 720px     | 70      | 25+      | 2+ minutes           |

### Retry Behavior

- Failed packets are automatically retried after 10 seconds
- ACK handling ensures delivery verification
- Transfer resumes from failed chunk (no need to restart)

## Configuration Knobs

**meshsender.py:**
```python
PORT_NUM = 256          # Meshtastic port number for this app
CHUNK_SIZE = 200        # Data payload per chunk (bytes)
WEB_PORT = 5678         # Web gallery server port
GALLERY_DIR = "gallery" # Directory to store received images
```

**takepic.py:**
```python
TARGET_NODE = "!da56b70c"
IMAGE_PATH = "/home/dave/small.jpg"
PYTHON_BIN = "/home/dave/mesh-env/bin/python"
SENDER_SCRIPT = "/home/dave/meshsender.py"
RES = "720"
QUAL = "70"
```

**Picamera2 settings:**
- `FrameDurationLimits`: (1000000, 1000000) = 1 FPS for long exposure
- `AnalogueGain`: 12.0 for low-light sensitivity
- `ColourGains`: (1.2, 0.9) for white balance

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Connection Error" | Ensure Meshtastic device is connected via USB and `meshtastic --info` works |
| Slow transfers | Reduce `--res` and `--qual` parameters; check mesh signal quality |
| Image corruption | Verify CRC errors in output; try reducing chunk size if MTU is exceeded |
| Web gallery not loading | Check firewall; ensure port 5678 is not in use; try `http://127.0.0.1:5678` |
| Camera permission denied | Run with `sudo` on Raspberry Pi, or add user to `video` group: `sudo usermod -a -G video $USER` |
| Picamera2 not found | Ensure you're on Raspberry Pi with Python 3.11+; install: `sudo apt install -y python3-picamera2` |

## Examples

### Monitor receiving gallery live

Terminal 1 (Receiver):
```bash
python meshsender.py receive
# Visit http://localhost:5678 in a browser to see incoming images
```

Terminal 2 (Sender):
```bash
python meshsender.py send !da56b70c sunset.jpg --res 320 --qual 50
```

### Automated Pi camera with cron

Schedule periodic image captures:

```bash
# Edit crontab
crontab -e

# Add this to capture and send every hour
0 * * * * cd /home/dave/meshsender && python takepic.py >> /tmp/meshsender.log 2>&1
```

### Systemd service (optional)

Create `/etc/systemd/system/meshsender-receiver.service`:

```ini
[Unit]
Description=Meshsender Image Receiver
After=network.target

[Service]
Type=simple
User=meshuser
WorkingDirectory=/home/meshuser/meshsender
ExecStart=/usr/bin/python3 /home/meshuser/meshsender/meshsender.py receive
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable meshsender-receiver
sudo systemctl start meshsender-receiver
```

## License

MIT License (see [LICENSE](LICENSE))

## Contributing

Contributions welcome! Areas for improvement:
- Video frame transmission support
- Compression algorithm optimization
- Web gallery enhancements
- Additional platform support (Windows LoRa dongles)
  - Meshtastic Python package
  - Pillow (PIL)
  - PyPubSub
  - pyserial
  - Picamera2 (only required if using takepic.py)
- OS-level packages: Picamera2/libcamera require Raspberry Pi OS packages and firmware (follow Raspberry Pi official docs).

Recommended Python install
1. Create and activate a venv:
   python -m venv .venv
   source .venv/bin/activate
2. Install dependencies:
   pip install -r requirements.txt

(See requirements.txt in this repo for package names and recommended pins.)

Installation
1. Clone the repository:
   git clone https://github.com/RootPathFinder/meshsender.git
   cd meshsender
2. Create virtualenv and install deps (see above).
3. Plug in your Meshtastic node via USB to the machine that will run meshsender.py.
4. Ensure the user running the scripts has permission to open the serial device (e.g., add to dialout group).

Usage

Run the receiver
- Start the receiver on the machine with a serial-attached Meshtastic device:
  python meshsender.py receive

What it does:
- Subscribes to meshtastic.receive events.
- Reassembles incoming chunked images, CRC-checks them and writes JPEG files to gallery/.
- Serves a tiny web gallery on port 5678 (default). Visit http://<receiver-ip>:5678/ to view images.

Send an image
- Send an existing image via a sender node attached to the machine:
  python meshsender.py send <target_node_id> /path/to/image.jpg --res 720 --qual 70

Notes:
- <target_node_id> can be a node numeric ID or peer-id formatted like !da56b70c.
- --res sets the max thumbnail side (image.thumbnail((res,res))) — lower reduces payload.
- --qual sets JPEG quality (lower = smaller payload, worse quality).

Capture & send from Raspberry Pi (takepic.py)
- Edit takepic.py constants: TARGET_NODE, IMAGE_PATH, PYTHON_BIN, SENDER_SCRIPT, RES, QUAL.
- Run:
  python takepic.py
- This example config:
  - Uses Picamera2 to capture a high-resolution image with long exposure settings suitable for low-light.
  - Saves to IMAGE_PATH and invokes meshsender.py to send the saved image.

Transfer format and chunking details
- Each transmitted packet payload begins with a 10-byte header:
  - [0]   : total_chunks (1 byte)
  - [1]   : chunk_index (1 byte)
  - [2..5]: CRC32 of the full image (4 bytes, big-endian)
  - [6..9]: total byte size of the image (4 bytes, big-endian)
  - [10:] : chunk data
- Default CHUNK_SIZE is 200 bytes (meshsender.py). Actual per-chunk data = CHUNK_SIZE - 10.
- The sender requests ACKs (wantAck=True) and sleeps between chunk transmissions (default 6s) to avoid overwhelming the mesh.
- Receiver buffers per-sender and only saves the image when all chunks are present and the CRC matches.

Tuning recommendations (LoRa is low-bandwidth)
- Keep images very small: reduce resolution (--res) and quality (--qual).
- Reduce chunk size only if devices or network require it; larger CHUNK_SIZE increases per-packet payload but may hit MTU limits.
- Consider increasing inter-chunk delay on noisy networks; for reliable links you can reduce delays to speed transfers.
- For production use consider implementing exponential backoff, partial retransmit, and transfer IDs.

Configuration knobs (in meshsender.py / takepic.py)
- PORT_NUM — app port used for payloads (should be same on sender/receiver)
- CHUNK_SIZE — size used for segmentation (default 200)
- WEB_PORT — HTTP server port for gallery (default 5678)
- GALLERY_DIR — directory for saving received images
- In takepic.py: TARGET_NODE, IMAGE_PATH, PYTHON_BIN, SENDER_SCRIPT, RES, QUAL

Troubleshooting
- Serial permission errors: add user to dialout group:
  sudo usermod -aG dialout <username>
  then log out/in.
- No incoming images:
  - Verify meshtastic node is connected and accessible via meshtastic Python API.
  - Confirm PORT_NUM matches sender/receiver.
  - Check meshtastic debug logs and serial connectivity.
- Web gallery unreachable:
  - Confirm meshsender.py receive is running.
  - Ensure firewall allows WEB_PORT (default 5678).
- Camera capture fails:
  - Ensure Picamera2 and libcamera are installed and configured.
  - Enable camera in raspi-config and reboot if necessary.

Systemd service examples
- Receiver (auto-start):
  /etc/systemd/system/meshsender-receiver.service
  ```
  [Unit]
  Description=Meshtastic Image Receiver
  After=network.target

  [Service]
  User=pi
  WorkingDirectory=/home/pi/meshsender
  ExecStart=/usr/bin/python3 /home/pi/meshsender/meshsender.py receive
  Restart=on-failure
  RestartSec=10

  [Install]
  WantedBy=multi-user.target
  ```

- Capture-and-send (single run):
  /etc/systemd/system/takepic.service
  ```
  [Unit]
  Description=Capture photo and send via Meshtastic
  After=network.target

  [Service]
  User=pi
  WorkingDirectory=/home/pi/meshsender
  ExecStart=/usr/bin/python3 /home/pi/meshsender/takepic.py
  Restart=no

  [Install]
  WantedBy=multi-user.target
  ```

Security & privacy
- The built-in gallery is unauthenticated HTTP. Keep it behind a firewall or VPN for public networks.
- Mesh traffic is visible to nodes in the mesh — avoid sending sensitive images unless you trust the network.

Contributing
- Bug reports and PRs are welcome.
- For PRs: create a topic branch, run tests (if any), and open a PR against main.
- Suggested improvements:
  - CLI argument clean-up and validation
  - Exponential backoff and clearer retry logging
  - Transfer resume/partial retransmit capability
  - Embedded metadata (filename, timestamp, EXIF) in transfer metadata
  - Proper unit tests for chunking/reassembly

How to create the README update branch and PR locally
1. Create a branch:
   git checkout -b docs/improve-readme
2. Replace README.md with the contents of this file and commit:
   git add README.md
   git commit -m "docs: improve README with install, usage and troubleshooting"
3. Push:
   git push origin docs/improve-readme
4. Create a PR (using GitHub CLI):
   gh pr create --title "docs: improve README" --body "Polished README with install, usage and troubleshooting." --base main --head docs/improve-readme

If you prefer the web UI, after pushing visit:
https://github.com/RootPathFinder/meshsender/compare and select the docs/improve-readme branch to open a PR.

License
- MIT — see LICENSE file.


```
