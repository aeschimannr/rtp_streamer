#!/usr/bin/env python3
"""
Minimal cross‑platform RTP sender GUI for FFmpeg (Tkinter)
UI: IP, Port, Start/Stop (streams local file 'output.mp4')
- Sends H.264 video via RTP to ip:port
- Auto-generates an SDP file named 'stream.sdp' (next to the exe)
- No extra deps; designed to be packaged with PyInstaller and embedded ffmpeg

Build (choose per OS) — embeds ffmpeg so the client installs NOTHING:
  Linux/macOS:
    pyinstaller --onefile --windowed \
      --add-binary "/absolute/path/to/ffmpeg:ffmpeg" \
      sender_gui.py
  Windows:
    pyinstaller --onefile --windowed \
      --add-binary "C:\\path\\to\\ffmpeg.exe;ffmpeg" \
      sender_gui.py

Runtime behavior:
- Looks for 'output.mp4' in the same folder as the executable.
- If you want a file picker, we can add it later (kept minimal as requested).
"""

import os
import sys
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox


def app_base_dir() -> str:
    return os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))


def find_ffmpeg() -> str | None:
    """Return an ffmpeg executable path or None if not found.

    Search order:
      1) PATH (shutil.which)
      2) Next to the executable (same folder)
      3) Inside PyInstaller bundle (sys._MEIPASS/ffmpeg/ffmpeg[.exe])
    """
    # 1) PATH
    p = shutil.which("ffmpeg")
    if p:
        return p

    # 2) Same directory as the executable/script
    base_dir = app_base_dir()
    for cand in ("ffmpeg", "ffmpeg.exe"):
        candidate = os.path.join(base_dir, cand)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    # 3) Inside PyInstaller bundle
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for cand in (os.path.join(meipass, "ffmpeg", "ffmpeg"), os.path.join(meipass, "ffmpeg", "ffmpeg.exe")):
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand

    return None


class SenderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RTP Sender — FFmpeg (minimal)")
        self.geometry("420x180")

        self.ip_var = tk.StringVar(value="10.0.0.2")
        self.port_var = tk.StringVar(value="5002")
        self.status_var = tk.StringVar(value="Idle")

        self.proc: subprocess.Popen | None = None
        self.ffmpeg_path = find_ffmpeg()
        self.source_path = os.path.join(app_base_dir(), "output.mp4")
        self.sdp_path = os.path.join(app_base_dir(), "stream.sdp")

        # --- UI ---
        tk.Label(self, text="Receiver IP:").grid(row=0, column=0, padx=10, pady=8, sticky="e")
        tk.Entry(self, textvariable=self.ip_var, width=20).grid(row=0, column=1, padx=10, pady=8, sticky="w")

        tk.Label(self, text="Port:").grid(row=1, column=0, padx=10, pady=8, sticky="e")
        tk.Entry(self, textvariable=self.port_var, width=8).grid(row=1, column=1, padx=10, pady=8, sticky="w")

        self.start_btn = tk.Button(self, text="Start", command=self.toggle_start_stop)
        self.start_btn.grid(row=2, column=0, padx=10, pady=12, sticky="e")

        tk.Label(self, textvariable=self.status_var, anchor="w").grid(row=2, column=1, padx=10, pady=12, sticky="w")

        # stretch
        self.grid_columnconfigure(1, weight=1)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.ffmpeg_path is None:
            self.status_var.set("ffmpeg not found — build with --add-binary or place next to exe")

    # --- Actions ---
    def toggle_start_stop(self):
        if self.proc is None:
            self.start_stream()
        else:
            self.stop_stream()

    def start_stream(self):
        # Late resolve ffmpeg each time
        if self.ffmpeg_path is None:
            self.ffmpeg_path = find_ffmpeg()
        if self.ffmpeg_path is None:
            messagebox.showerror("FFmpeg not found", "ffmpeg missing. Build the app with --add-binary to embed it.")
            return

        ip = self.ip_var.get().strip()
        port = self.port_var.get().strip()

        if not ip:
            messagebox.showerror("Missing IP", "Enter receiver IP.")
            return
        if not port.isdigit():
            messagebox.showerror("Invalid port", "Enter a numeric port (e.g., 5002).")
            return

        # Minimal: expect 'output.mp4' next to the executable
        if not os.path.isfile(self.source_path):
            messagebox.showerror("Source missing", f"File not found: {self.source_path}\nPlace 'output.mp4' next to the executable.")
            return

        # Build simplest sending command (copy video; assumes H.264 source)
        cmd = [
            self.ffmpeg_path,
            "-re",
            "-i", self.source_path,
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            "-payload_type", "96",
            "-sdp_file", self.sdp_path,
            "-f", "rtp",
            f"rtp://{ip}:{port}",
        ]

        try:
            self.proc = subprocess.Popen(cmd)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start ffmpeg: {e}")
            self.proc = None
            return

        self.status_var.set(f"Streaming → rtp://{ip}:{port}\nSDP: {self.sdp_path}")
        self.start_btn.config(text="Stop")
        threading.Thread(target=self._wait_and_reset, daemon=True).start()

    def _wait_and_reset(self):        
        if self.proc is None:
            return
        self.proc.wait()
        self.after(0, self._reset_ui_after_exit)

    def _reset_ui_after_exit(self):
        self.proc = None
        self.start_btn.config(text="Start")
        self.status_var.set("Idle")

    def stop_stream(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
        except Exception:
            pass
        self.status_var.set("Stopping…")

    def on_close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    SenderApp().mainloop()


