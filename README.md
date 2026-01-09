```markdown
# Meshsender

A small Python toolset for capturing, sending and receiving images over Meshtastic-enabled LoRa mesh devices.

This project provides:
- A CLI to send and receive images via a serial-attached Meshtastic node (meshsender.py).
- A Raspberry Pi camera capture helper that captures an image with Picamera2 and sends it to the mesh (takepic.py).
- A simple built-in web gallery that serves received images from gallery/.

This README documents installation, usage, internals, and troubleshooting for the repository.

Status
- Prototype (2026). Works for small, optimized thumbnails on low-bandwidth LoRa networks.
- Includes chunking, CRC integrity checks, ACK handling and a tiny web UI for browsing received images.

Quick links
- CLI: meshsender.py
- Pi capture & send helper: takepic.py
- Requirements: requirements.txt
- License: MIT (LICENSE)

Table of contents
- Features
- Requirements
- Installation
- Usage
  - Run receiver
  - Send an image
  - Capture & send from Raspberry Pi camera
- Transfer format & tuning
- Configuration knobs
- Troubleshooting
- Systemd service examples
- Contributing
- License

Features
- Split/fragment JPEG data into small chunks that fit LoRa payload constraints.
- 10-byte header per chunk (transfer metadata + CRC) for safe reassembly.
- CRC32 verification of full image before saving.
- Simple HTTP gallery server to view received images.
- Picamera2-based example for long-exposure / low-light capture on Raspberry Pi.

Requirements
- Hardware:
  - Meshtastic LoRa devices and a working mesh network (at least one sender and one receiver).
  - Raspberry Pi camera module for takepic.py (optional).
- Software:
  - Python 3.9+
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
