#!/usr/bin/env python3
"""Tkinter MQTT logger that writes JSONL to disk.

The GUI lets the user pick an MQTT broker IP/port, topic filter and output file
path. Every received payload is appended to the JSON Lines file with a UTC
timestamp and previewed inside the UI. Designed to be frozen with PyInstaller.
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext


class MqttLoggerApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("MQTT JSON Logger")
        self.geometry("560x460")

        self.host_var = tk.StringVar(value="127.0.0.1")
        self.port_var = tk.StringVar(value="1883")
        self.topic_var = tk.StringVar(value="sensors/#")
        default_path = Path.home() / "mqtt_log.jsonl"
        self.out_var = tk.StringVar(value=str(default_path))
        self.status_var = tk.StringVar(value="Idle")

        self._client: mqtt.Client | None = None
        self._log_file = None
        self._message_count = 0
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._form_widgets: list[tk.Widget] = []

        self._build_ui()
        self.after(150, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        form = tk.Frame(self)
        form.pack(fill="x", padx=12, pady=10)

        def add_row(label: str, var: tk.StringVar, row: int, width: int = 28, browse: bool = False) -> None:
            tk.Label(form, text=label).grid(row=row, column=0, sticky="e", padx=6, pady=4)
            entry = tk.Entry(form, textvariable=var, width=width)
            entry.grid(row=row, column=1, sticky="we", padx=6, pady=4)
            self._form_widgets.append(entry)
            if browse:
                btn = tk.Button(form, text="Browse…", command=self._pick_output_file)
                btn.grid(row=row, column=2, sticky="w", padx=6)
                self._form_widgets.append(btn)

        form.grid_columnconfigure(1, weight=1)
        add_row("Broker IP", self.host_var, 0)
        add_row("Port", self.port_var, 1, width=10)
        add_row("Topic", self.topic_var, 2, width=35)
        add_row("Output file", self.out_var, 3, width=35, browse=True)

        controls = tk.Frame(self)
        controls.pack(fill="x", padx=12)

        self.start_btn = tk.Button(controls, text="Start", command=self.toggle_logging, width=10)
        self.start_btn.pack(side="left", padx=(0, 8))

        tk.Label(controls, textvariable=self.status_var, anchor="w").pack(side="left", fill="x")

        self.log_text = scrolledtext.ScrolledText(self, height=15, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=12, pady=(8, 12))

    # ------------------------------------------------------------------
    def _pick_output_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Choose JSONL log file",
            defaultextension=".jsonl",
            filetypes=(("JSON Lines", "*.jsonl"), ("All files", "*.*")),
            initialfile=Path(self.out_var.get()).name,
        )
        if path:
            self.out_var.set(path)

    def toggle_logging(self) -> None:
        if self._client is None:
            self.start_logging()
        else:
            self.stop_logging()

    # ------------------------------------------------------------------
    def start_logging(self) -> None:
        host = self.host_var.get().strip()
        topic = self.topic_var.get().strip()

        if not host:
            messagebox.showerror("Missing host", "Enter an MQTT broker IP or hostname.")
            return
        if not topic:
            messagebox.showerror("Missing topic", "Enter a topic filter (e.g. sensors/#).")
            return

        try:
            port = int(self.port_var.get().strip())
        except ValueError:
            messagebox.showerror("Invalid port", "Port must be an integer (e.g. 1883).")
            return

        out_path = Path(self.out_var.get()).expanduser()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = out_path.open("a", encoding="utf-8", buffering=1)
        except OSError as exc:
            messagebox.showerror("File error", f"Cannot open log file: {exc}")
            return

        client = mqtt.Client(client_id=f"mqtt-logger-{threading.get_ident()}")
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        self._client = client
        self._log_file = log_file
        self._message_count = 0
        self.status_var.set("Connecting…")
        self._queue_event("log", f"→ Logging to {out_path}")
        self._set_form_state("disabled")
        self.start_btn.config(text="Stop")

        client.user_data_set({"topic": topic})

        try:
            client.connect(host, port, keepalive=60)
        except Exception as exc:
            self._queue_event("log", f"[ERROR] Failed to connect: {exc}")
            self.status_var.set("Connection failed")
            self._cleanup_client()
            self._set_idle_controls()
            messagebox.showerror("Connection error", f"Failed to connect: {exc}")
            return

        client.loop_start()

    def stop_logging(self) -> None:
        if self._client is None and self._log_file is None:
            self._set_idle_controls()
            return
        self._queue_event("log", "→ Stopping logger")
        self._cleanup_client()
        self._set_idle_controls()

    def _cleanup_client(self) -> None:
        if self._client is not None:
            try:
                self._client.loop_stop()
            except Exception:
                pass
            try:
                self._client.disconnect()
            except Exception:
                pass
        self._client = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
        self._log_file = None

    def _set_form_state(self, state: str) -> None:
        for widget in self._form_widgets:
            widget.configure(state=state)

    def _set_idle_controls(self) -> None:
        self.status_var.set("Idle")
        self.start_btn.config(text="Start")
        self._set_form_state("normal")

    # ------------------------------------------------------------------
    def _on_connect(self, client: mqtt.Client, userdata, flags, rc: int) -> None:
        if rc == 0:
            topic = None
            if isinstance(userdata, dict):
                topic = userdata.get("topic")
            if topic:
                client.subscribe(topic)
            self._queue_event("status", f"Connected — subscribed to {topic}")
            self._queue_event("log", f"[INFO] Subscribed to {topic}")
        else:
            self._queue_event("status", f"Connect failed (rc={rc})")
            self._queue_event("log", f"[ERROR] Connect failed rc={rc}")
            self._queue_event("command", "stop")

    def _on_disconnect(self, _client: mqtt.Client, _userdata, rc: int) -> None:
        if rc != 0:
            self._queue_event("log", f"[WARN] Unexpected disconnect rc={rc}")
            self._queue_event("command", "stop")
        self._queue_event("status", "Disconnected")

    def _on_message(self, _client: mqtt.Client, _userdata, msg: mqtt.MQTTMessage) -> None:
        if self._log_file is None:
            return

        payload_str = msg.payload.decode("utf-8", errors="replace")
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            payload = {"_raw": payload_str}

        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "topic": msg.topic,
            "payload": payload,
        }

        line = json.dumps(record, ensure_ascii=False)
        try:
            self._log_file.write(line + "\n")
        except Exception as exc:  # pragma: no cover - best effort
            self._queue_event("log", f"[ERROR] Write failed: {exc}")
            self._queue_event("status", "Write error — stopping")
            self._queue_event("command", "stop")
            return

        self._message_count += 1
        preview = payload_str.replace("\n", " ")
        if len(preview) > 120:
            preview = preview[:117] + "..."
        self._queue_event("status", f"Logged {self._message_count} messages")
        self._queue_event("log", f"[MSG {self._message_count}] {msg.topic}: {preview}")

    # ------------------------------------------------------------------
    def _queue_event(self, kind: str, payload: str) -> None:
        self._queue.put((kind, payload))

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self.status_var.set(payload)
            elif kind == "log":
                self._append_log(payload)
            elif kind == "command" and payload == "stop":
                self.stop_logging()
        self.after(150, self._poll_queue)

    def _append_log(self, line: str) -> None:
        widget = self.log_text
        widget.configure(state="normal")
        widget.insert("end", line + "\n")
        widget.see("end")
        try:
            total_lines = int(float(widget.index("end-1c").split(".")[0]))
        except Exception:
            total_lines = 0
        if total_lines > 400:
            widget.delete("1.0", "5.0")
        widget.configure(state="disabled")

    # ------------------------------------------------------------------
    def on_close(self) -> None:
        self.stop_logging()
        self.destroy()


def main() -> None:
    app = MqttLoggerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
