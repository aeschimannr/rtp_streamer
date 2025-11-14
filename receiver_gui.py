#!/usr/bin/env python3
"""
Minimal cross‑platform RTP receiver GUI for FFmpeg (Tkinter)
- 3 buttons: Load SDP, Choose Output, Start/Stop (same button)
- Launches: ffmpeg -protocol_whitelist "file,rtp,udp" -i <SDP> -c copy -movflags +faststart <OUTPUT>
- No extra deps, no fancy features. Keep it simple.

Packaging so the client installs NOTHING:
- Bundle ffmpeg inside the one‑file executable with PyInstaller.
- Build examples (choose the one for your OS):
  Linux/macOS:
    pyinstaller --onefile --windowed \
      --add-binary "/absolute/path/to/ffmpeg:ffmpeg" \
      receiver_gui.py
  Windows (PowerShell/CMD):
    pyinstaller --onefile --windowed \
      --add-binary "C:\path\to\ffmpeg.exe;ffmpeg" \
      receiver_gui.py

Notes:
- The --add-binary puts the ffmpeg binary inside the bundle under an internal folder named "ffmpeg/".
- At runtime we resolve ffmpeg path by checking: PATH → next to the exe → inside the PyInstaller bundle (sys._MEIPASS/ffmpeg/...).
"""

import os
import sys
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox


def find_ffmpeg() -> str | None:
    """Return an ffmpeg executable path or None if not found.

    Search order:
      1) PATH (shutil.which)
      2) Next to the executable (same folder as the frozen exe or this script)
      3) Inside PyInstaller bundle (sys._MEIPASS/ffmpeg/ffmpeg[.exe])
    """
    # 1) PATH
    p = shutil.which("ffmpeg")
    if p:
        return p

    # 2) Same directory as the executable/script
    base_dir = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
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


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Video Stream Recorder")
        self.geometry("600x220")

        self.sdp_path = tk.StringVar(value="")
        self.rtsp_url = tk.StringVar(value="")
        self.out_path = tk.StringVar(value="")
        self.proc: subprocess.Popen | None = None
        self.ffmpeg_path = find_ffmpeg()

        # --- UI ---
        row = 0
        tk.Button(self, text="Load SDP…", command=self.load_sdp).grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Label(self, textvariable=self.sdp_path, anchor="w", fg="#333").grid(row=row, column=1, padx=10, pady=10, sticky="we")

        row += 1
        tk.Label(self, text="or RTSP endpoint:").grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Entry(self, textvariable=self.rtsp_url, width=40).grid(row=row, column=1, padx=10, pady=10, sticky="we")

        row += 1
        tk.Button(self, text="Choose output…", command=self.choose_output).grid(row=row, column=0, padx=10, pady=10, sticky="w")
        tk.Label(self, textvariable=self.out_path, anchor="w", fg="#333").grid(row=row, column=1, padx=10, pady=10, sticky="we")

        row += 1
        self.start_btn = tk.Button(self, text="Start", command=self.toggle_start_stop)
        self.start_btn.grid(row=row, column=0, padx=10, pady=10, sticky="w")
        self.status_lbl = tk.Label(self, text="Idle", anchor="w")
        self.status_lbl.grid(row=row, column=1, padx=10, pady=10, sticky="we")

        # Make right column stretch
        self.grid_columnconfigure(1, weight=1)

        # Ensure clean exit
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # Warn once if ffmpeg not located yet
        if self.ffmpeg_path is None:
            self.status_lbl.config(text="ffmpeg not found — select SDP/output then Start to retry")

    # --- Actions ---
    def load_sdp(self):
        path = filedialog.askopenfilename(
            title="Select SDP file",
            filetypes=[("SDP files", "*.sdp;*.SDP"), ("All files", "*.*")],
        )
        if path:
            self.sdp_path.set(path)

    def choose_output(self):
        path = filedialog.asksaveasfilename(
            title="Save output MKV",
            defaultextension=".mkv",
            initialfile="output.mkv",
            filetypes=[("MKV video", "*.mkv"), ("All files", "*.*")],
        )
        if path:
            self.out_path.set(path)

    def toggle_start_stop(self):
        if self.proc is None:
            self.start_ffmpeg()
        else:
            self.stop_ffmpeg()

    def start_ffmpeg(self):
        # Late resolve ffmpeg (in case PATH changed or bundle extracted)
        if self.ffmpeg_path is None:
            self.ffmpeg_path = find_ffmpeg()
        if self.ffmpeg_path is None:
            messagebox.showerror(
                "FFmpeg not found",
                "ffmpeg is missing. If you built with --add-binary, it's bundled automatically."
                "Otherwise put 'ffmpeg' next to this executable or in PATH.",
            )
            return

        sdp = self.sdp_path.get().strip()
        rtsp = self.rtsp_url.get().strip()
        out = self.out_path.get().strip()
        if not sdp and not rtsp:
            messagebox.showerror("Missing source", "Load an SDP file or enter an RTSP URL.")
            return
        if sdp and not os.path.isfile(sdp):
            messagebox.showerror("Missing SDP", "The selected SDP file no longer exists. Load it again or use RTSP.")
            return
        if not out:
            messagebox.showerror("Missing output", "Please choose an output .mkv path.")
            return

        if sdp:
            cmd = [
                self.ffmpeg_path,
                "-protocol_whitelist", "file,rtp,udp",
                "-i", sdp,
                "-c", "copy",
                out,
            ]
            status_text = "Recording from SDP…"
        else:
            cmd = [
                self.ffmpeg_path,
                "-rtsp_transport", "tcp",
                "-i", rtsp,
                "-c", "copy",
                out,
            ]
            status_text = "Recording from RTSP…"

        try:
            self.proc = subprocess.Popen(cmd)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to start ffmpeg: {e}")
            self.proc = None
            return

        self.status_lbl.config(text=status_text)
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
        self.status_lbl.config(text="Idle")

    def stop_ffmpeg(self):
        if self.proc is None:
            return
        try:
            self.proc.terminate()
        except Exception:
            pass
        self.status_lbl.config(text="Stopping…")

    def on_close(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
