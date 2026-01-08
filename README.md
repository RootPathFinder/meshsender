# meshsender

A small Python project to send and receive images over Meshtastic-enabled LoRa mesh devices.

Purpose
- Send images over a Meshtastic network (split + encode for transport).
- Receive and reassemble images from Meshtastic messages.
- Provide a CLI and Python API for integration into other tools.

Prerequisites
- Meshtastic devices and working mesh network
- Python 3.9+
- `meshtastic` Python package
- `Pillow` for image handling

Quickstart (local)
1. Create and activate a virtualenv:
   - python -m venv .venv
   - source .venv/bin/activate   (or `.venv\\Scripts\\activate` on Windows)
2. Install dependencies:
   - pip install -r requirements.txt
3. Add your working `meshsender.py` to the repo root (this is the CLI entry you mentioned).
4. Run the CLI:
   - python meshsender.py send --image path/to/image.jpg --destination <node-id>
   - python meshsender.py receive

Repository layout
- meshsender/          — package metadata (if you need it)
- meshsender.py        — your main executable (you uploaded this)
- examples/            — demo scripts (optional)
- requirements.txt
- README.md
- LICENSE
- .gitignore

Notes
- Pay attention to chunking, retransmissions, and rate limits of the mesh network when implementing send/receive logic.
