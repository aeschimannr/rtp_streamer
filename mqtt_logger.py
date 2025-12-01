#!/usr/bin/env python3
"""Minimal MQTT → JSONL logger.

The script subscribes to a user supplied topic filter and appends one JSON
record per MQTT message to a local ``.jsonl`` file (JSON Lines format). Both the
raw payload string and the parsed JSON object are handled so we never drop data
when a publisher sends malformed JSON.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MQTT JSON logger")
    parser.add_argument("--host", default="127.0.0.1", help="MQTT broker host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--topic", required=True, help="Topic filter to subscribe to (e.g. 'foo/#')")
    parser.add_argument("--out", default="mqtt_log.jsonl", help="Output JSONL file path")
    parser.add_argument("--client-id", default="mqtt-json-logger", help="MQTT client identifier")
    parser.add_argument("--username", default=None, help="Optional MQTT username")
    parser.add_argument("--password", default=None, help="Optional MQTT password (when username is set)")
    parser.add_argument("--raw", action="store_true", help="Store payload as raw UTF-8 string instead of JSON")
    parser.add_argument("--qos", type=int, choices=(0, 1, 2), default=0, help="QoS level for the subscription")
    return parser.parse_args()


class JsonLogger:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self._should_stop = False

        out_path = Path(args.out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Line-buffered writes ensure every record hits disk promptly
        self._fp = out_path.open("a", encoding="utf-8", buffering=1)

        self._client = mqtt.Client(client_id=args.client_id, clean_session=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)

        if args.username:
            self._client.username_pw_set(args.username, args.password)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    # ------------------------------------------------------------------
    # MQTT callbacks
    def _on_connect(self, client: mqtt.Client, _userdata: Any, _flags: dict[str, Any], rc: int) -> None:
        if rc == 0:
            print(f"[INFO] Connected to MQTT {self.args.host}:{self.args.port}")
            print(f"[INFO] Subscribing to topic '{self.args.topic}' with QoS {self.args.qos}")
            client.subscribe(self.args.topic, qos=self.args.qos)
        else:
            print(f"[ERROR] MQTT connect failed with code {rc}")

    def _on_message(self, _client: mqtt.Client, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        payload_str = msg.payload.decode("utf-8", errors="replace")

        if self.args.raw:
            payload: Any = payload_str
        else:
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                payload = {"_raw": payload_str}

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "topic": msg.topic,
            "payload": payload,
        }

        self._fp.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _on_disconnect(self, _client: mqtt.Client, _userdata: Any, rc: int) -> None:
        print(f"[INFO] Disconnected (rc={rc})")

    # ------------------------------------------------------------------
    def request_stop(self) -> None:
        self._should_stop = True

    def run(self) -> int:
        try:
            print(f"[INFO] Connecting to {self.args.host}:{self.args.port} ...")
            self._client.connect(self.args.host, self.args.port, keepalive=60)
        except Exception as exc:  # pragma: no cover - best effort logging
            print(f"[ERROR] Failed to connect to broker: {exc}")
            self._fp.close()
            return 1

        self._client.loop_start()
        try:
            while not self._should_stop:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[INFO] KeyboardInterrupt, exiting…")
        finally:
            self._client.loop_stop()
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._fp.close()

        return 0


def main() -> None:
    args = parse_args()
    logger = JsonLogger(args)

    def handle_signal(signum: int, _frame: Any) -> None:
        print(f"\n[INFO] Signal {signum} received, shutting down…")
        logger.request_stop()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    sys.exit(logger.run())


if __name__ == "__main__":  # pragma: no cover
    main()
