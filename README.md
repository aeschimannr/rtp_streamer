# rtp_streamer

Utilities for testing RTP video streaming (sender/receiver GUIs) plus a simple
MQTT → JSONL logger.

## MQTT logger

`mqtt_logger.py` launches a small Tkinter GUI. Pick the broker IP/port, enter the
topic filter (wildcards supported) and choose where the JSONL file must be
written. Every MQTT payload is decoded as JSON (with a fallback to raw UTF-8)
and appended to the chosen file together with a UTC timestamp.

Usage:

```bash
python3 -m pip install paho-mqtt
python3 mqtt_logger.py
```

Fill the form, click **Start** to begin logging and **Stop** to close the output
file. The GitHub Actions workflow builds PyInstaller bundles for Linux, macOS
and Windows (`dist/mqtt_logger*`).
