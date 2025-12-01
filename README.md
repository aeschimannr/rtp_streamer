# rtp_streamer

Utilities for testing RTP video streaming (sender/receiver GUIs) plus a simple
MQTT → JSONL logger.

## MQTT logger

`mqtt_logger.py` subscribes to a user supplied topic filter and appends every
message to a JSON Lines file. Each record contains a UTC timestamp, the topic
and either the parsed JSON payload or the raw UTF-8 string when parsing fails.

Example:

```bash
python mqtt_logger.py \
  --host 192.168.1.10 \
  --topic sensors/ship/# \
  --out /tmp/ship_log.jsonl
```

Command line flags mirror the ones exposed in the PyInstaller binary that the
`build-apps` GitHub workflow produces for Linux, macOS and Windows.
