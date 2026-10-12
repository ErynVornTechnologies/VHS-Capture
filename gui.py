"""VHS Capture - GUI. Part of the Boring Stuff series by Erynvorn Technologies.
https://erynvorntechnologies.com | https://github.com/ErynVornTechnologies

Live preview with audio meter, one-click recording to MKV, a time limit and
an automatic stop when the tape ends (no picture + silence).
"""
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, messagebox

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import capture as cap  # noqa: E402

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("dark-blue")
ASSETS = os.path.join(cap.APP_DIR, "assets")
SETTINGS = os.path.join(cap.APP_DIR, "settings.json")
GOLD = "#c9b39b"
LINKS = [("erynvorntechnologies.com", "https://erynvorntechnologies.com"),
         ("GitHub", "https://github.com/ErynVornTechnologies"),
         ("LinkedIn", "https://www.linkedin.com/in/kamil-krzysztof-nagorski/")]
NO_AUDIO = "(no audio)"
ENHANCER_DIRS = [os.path.join(cap.APP_DIR, "..", "08_Video_Enhancer"),
                 os.path.join(cap.APP_DIR, "..", "Video-Enhancer")]


def find_enhancer():
    for d in ENHANCER_DIRS:
        py = os.path.join(d, ".venv", "Scripts", "python.exe")
        if os.path.isfile(os.path.join(d, "enhancer.py")) and os.path.isfile(py):
            return os.path.abspath(d), py
    return None, None


def fmt_time(s):
    return time.strftime("%H:%M:%S", time.gmtime(max(0, s)))


class CaptureApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("VHS Capture | Erynvorn Technologies")
        self.geometry("1120x800")
        self.minsize(1040, 760)
        try:
            self.iconbitmap(os.path.join(ASSETS, "evt_icon.ico"))
        except Exception:  # noqa: BLE001
            pass
        self.cfg = self._load_cfg()
        self.session = None        # current cap.Session (preview or recording)
        self.recording = False
        self.modes = []
        self._latest = None        # newest preview frame from the worker thread
        self._busy = False         # device switching in progress
        # speaker monitoring: one player for the whole app lifetime (see capture.AudioMonitor)
        self.monitor, self._mon_err = None, None
        try:
            self.monitor = cap.AudioMonitor(volume=float(self.cfg.get("mon_vol", 0.8)),
                                            muted=not self.cfg.get("listen", True))
        except Exception as e:  # noqa: BLE001 - no sound card / sounddevice missing
            self._mon_err = str(e)
        self._build()
        if self._mon_err:
            self.log(f"[warn] speaker monitoring unavailable: {self._mon_err}")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(80, self._render)
        self.after(500, self._tick)
        self.after(200, self.refresh_devices)

    # ------------------------------------------------------------ settings
    def _load_cfg(self):
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001
            return {}

    def _save_cfg(self):
        self.cfg.update(folder=self.v_folder.get(), name=self.v_name.get(),
                        video=self.v_video.get(), audio=self.v_audio.get(),
                        standard=self.v_std.get(), format=self.v_fmt.get(),
                        minutes=self.v_min.get(), auto=self.v_auto.get(),
                        enhance=self.v_enh.get(), listen=self.v_listen.get(),
                        mon_vol=round(self.v_vol.get(), 2), mode=self.v_mode.get())
        try:
            with open(SETTINGS, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except OSError:
            pass

    # ------------------------------------------------------------------ UI
    def _build(self):
        top = ctk.CTkFrame(self, fg_color="transparent"); top.pack(fill="x", padx=18, pady=(12, 4))
        try:
            logo = Image.open(os.path.join(ASSETS, "evt_logo.png"))
            self._logo = ctk.CTkImage(light_image=logo, dark_image=logo, size=(200, 49))
            ctk.CTkLabel(top, text="", image=self._logo).pack(side="left")
        except Exception:  # noqa: BLE001
            pass
        ctk.CTkLabel(top, text="  VHS Capture", font=("Roboto", 24, "bold")).pack(side="left", padx=10)
        ctk.CTkLabel(top, text="Record VHS, Hi8 and camcorder tapes through a USB grabber",
                     text_color="#8a8a8a").pack(side="left", padx=6, pady=(8, 0))

        foot = ctk.CTkFrame(self, fg_color="transparent"); foot.pack(side="bottom", pady=(0, 8))
        ctk.CTkLabel(foot, text="Boring Stuff by Erynvorn Technologies  |",
                     text_color="#6a6a6a").pack(side="left", padx=(0, 6))
        for text, url in LINKS:
            lnk = ctk.CTkLabel(foot, text=text, text_color=GOLD, cursor="hand2")
            lnk.pack(side="left", padx=4)
            lnk.bind("<Button-1>", lambda _e, u=url: webbrowser.open(u))

        body = ctk.CTkFrame(self, fg_color="transparent"); body.pack(fill="both", expand=True, padx=18)
        left = ctk.CTkFrame(body); left.pack(side="left", fill="y", padx=(0, 10), pady=4)
        right = ctk.CTkFrame(body); right.pack(side="left", fill="both", expand=True, pady=4)

        # --- left: preview + live status
        blank = Image.new("RGB", (cap.PREV_W, cap.PREV_H + cap.BAR_H), (12, 12, 12))
        self._img = ctk.CTkImage(light_image=blank, dark_image=blank,
                                 size=(cap.PREV_W, cap.PREV_H + cap.BAR_H))
        self.lbl_prev = ctk.CTkLabel(left, text="", image=self._img)
        self.lbl_prev.pack(padx=12, pady=(12, 6))
        st = ctk.CTkFrame(left, fg_color="transparent"); st.pack(fill="x", padx=12)
        self.lbl_sig = ctk.CTkLabel(st, text="● starting...", text_color="#8a8a8a",
                                    font=("Roboto", 14, "bold"))
        self.lbl_sig.pack(side="left")
        self.lbl_rec = ctk.CTkLabel(st, text="", text_color="#ef4444", font=("Roboto", 14, "bold"))
        self.lbl_rec.pack(side="right")
        self.lbl_stats = ctk.CTkLabel(left, text="", text_color="#cfcfcf", font=("Consolas", 13),
                                      justify="left", anchor="w")
        self.lbl_stats.pack(fill="x", padx=12, pady=(4, 6))
        lf = ctk.CTkFrame(left, fg_color="transparent"); lf.pack(fill="x", padx=12, pady=(0, 6))
        self.v_listen = ctk.BooleanVar(value=bool(self.monitor) and not self.monitor.muted)
        ctk.CTkCheckBox(lf, text="Listen on speakers", variable=self.v_listen,
                        command=self._listen_changed,
                        state="normal" if self.monitor else "disabled").pack(side="left")
        self.v_vol = ctk.DoubleVar(value=self.monitor.volume if self.monitor else 0.8)
        ctk.CTkSlider(lf, from_=0, to=1.5, variable=self.v_vol, width=200,
                      command=self._vol_changed,
                      state="normal" if self.monitor else "disabled").pack(side="left", padx=12)
        ctk.CTkLabel(lf, text="volume", text_color="#7a7a7a").pack(side="left")
        self.txt = ctk.CTkTextbox(left, width=cap.PREV_W, height=120, font=("Consolas", 11))
        self.txt.pack(padx=12, pady=(0, 12), fill="both", expand=True)
        self._build_right(right)

    def _row(self, parent, r, label, widget, hint=None):
        ctk.CTkLabel(parent, text=label).grid(row=r, column=0, padx=(14, 8), pady=5, sticky="w")
        widget.grid(row=r, column=1, padx=6, pady=5, sticky="we")
        if hint:
            ctk.CTkLabel(parent, text=hint, text_color="#7a7a7a", font=("Roboto", 11)).grid(
                row=r + 1, column=1, padx=8, sticky="w")

    def _build_right(self, p):
        c = self.cfg
        p.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(p, text="Source", font=("Roboto", 15, "bold")).grid(
            row=0, column=0, padx=14, pady=(12, 2), sticky="w")
        self.v_video = ctk.StringVar(value=c.get("video", ""))
        self.om_video = ctk.CTkOptionMenu(p, variable=self.v_video, values=["..."],
                                          command=lambda _: self.on_device_change(True))
        self._row(p, 1, "Video device:", self.om_video)
        self.v_audio = ctk.StringVar(value=c.get("audio", ""))
        self.om_audio = ctk.CTkOptionMenu(p, variable=self.v_audio, values=["..."],
                                          command=lambda _: self.on_device_change())
        self._row(p, 2, "Audio device:", self.om_audio)
        self.v_mode = ctk.StringVar(value="")
        self.om_mode = ctk.CTkOptionMenu(p, variable=self.v_mode, values=["..."],
                                         command=lambda _: self.on_device_change())
        self._row(p, 3, "Video mode:", self.om_mode)
        self.v_std = ctk.StringVar(value=c.get("standard", list(cap.STANDARDS)[0]))
        self._row(p, 4, "Tape standard:", ctk.CTkOptionMenu(
            p, variable=self.v_std, values=list(cap.STANDARDS), command=lambda _: self.on_device_change()))
        ctk.CTkButton(p, text="Device settings (brightness, PAL/NTSC...)", fg_color="#3a3a3a",
                      command=self.device_dialog).grid(row=5, column=1, padx=6, pady=(2, 8), sticky="w")

        ctk.CTkLabel(p, text="Recording", font=("Roboto", 15, "bold")).grid(
            row=6, column=0, padx=14, pady=(8, 2), sticky="w")
        ff = ctk.CTkFrame(p, fg_color="transparent")
        self.v_folder = ctk.StringVar(value=c.get("folder", cap.DEFAULT_OUT))
        ctk.CTkEntry(ff, textvariable=self.v_folder).pack(side="left", fill="x", expand=True)
        ctk.CTkButton(ff, text="Browse", width=70, command=self.pick_folder).pack(side="left", padx=4)
        ctk.CTkButton(ff, text="Open", width=60, fg_color="#3a3a3a", command=self.open_folder).pack(side="left")
        self._row(p, 7, "Save to:", ff)
        self.v_name = ctk.StringVar(value=c.get("name", "Tape"))
        self._row(p, 8, "File name:", ctk.CTkEntry(p, textvariable=self.v_name),
                  hint="date and time are added automatically, e.g. Tape_2026-10-09_14-30-00.mkv")
        self.v_fmt = ctk.StringVar(value=c.get("format", list(cap.FORMATS)[0]))
        self._row(p, 10, "Format:", ctk.CTkOptionMenu(p, variable=self.v_fmt, values=list(cap.FORMATS)),
                  hint="Original = exact grabber stream, no extra loss, largest files")
        self.v_min = ctk.StringVar(value=c.get("minutes", "0"))
        self._row(p, 12, "Stop after (min):", ctk.CTkEntry(p, textvariable=self.v_min, width=80),
                  hint="0 = no limit. E-180 tape = 180, E-240 = 240")
        self.v_auto = ctk.BooleanVar(value=c.get("auto", True))
        ctk.CTkCheckBox(p, text="Stop automatically when the tape ends (60 s of no picture + silence)",
                        variable=self.v_auto).grid(row=14, column=0, columnspan=2, padx=14, pady=4, sticky="w")
        self.enh_dir, self.enh_py = find_enhancer()
        self.v_enh = ctk.BooleanVar(value=bool(c.get("enhance", False)) and bool(self.enh_dir))
        cb = ctk.CTkCheckBox(p, text="Send to Video Enhancer afterwards (1080p, deinterlace, denoise)"
                             if self.enh_dir else "Video Enhancer not found next to this app",
                             variable=self.v_enh, state="normal" if self.enh_dir else "disabled")
        cb.grid(row=15, column=0, columnspan=2, padx=14, pady=4, sticky="w")

        self.btn_rec = ctk.CTkButton(p, text="●  RECORD", height=54, font=("Roboto", 18, "bold"),
                                     fg_color="#dc2626", hover_color="#b91c1c", command=self.toggle_record)
        self.btn_rec.grid(row=16, column=0, columnspan=2, padx=14, pady=(16, 8), sticky="we")

    # --------------------------------------------------------- helpers
    def _listen_changed(self):
        if self.monitor:
            self.monitor.muted = not self.v_listen.get()

    def _vol_changed(self, v):
        if self.monitor:
            self.monitor.volume = float(v)

    def log(self, msg):
        self.after(0, lambda: (self.txt.insert("end", msg.rstrip() + "\n"), self.txt.see("end")))

    def pick_folder(self):
        d = filedialog.askdirectory(initialdir=self.v_folder.get() or cap.DEFAULT_OUT)
        if d:
            self.v_folder.set(os.path.normpath(d))

    def open_folder(self):
        d = self.v_folder.get() or cap.DEFAULT_OUT
        os.makedirs(d, exist_ok=True)
        os.startfile(d)

    def _mode(self):
        return next((m for m in self.modes if m["label"] == self.v_mode.get()), None)

    def _audio(self):
        a = self.v_audio.get()
        return None if a in ("", NO_AUDIO, "...") else a

    # ------------------------------------------------------- devices
    def refresh_devices(self):
        def work():
            vids, auds = cap.list_devices()
            self.after(0, lambda: self._set_devices(vids, auds))
        threading.Thread(target=work, daemon=True).start()

    def _set_devices(self, vids, auds):
        if not vids:
            self.lbl_sig.configure(text="● no capture device found", text_color="#ef4444")
            self.log("[error] No video capture device. Plug in the grabber and restart the app.")
            return
        gv, ga = cap.guess_grabber(vids, auds)
        self.om_video.configure(values=vids)
        self.om_audio.configure(values=auds + [NO_AUDIO])
        if self.v_video.get() not in vids:
            self.v_video.set(gv)
        if self.v_audio.get() not in auds + [NO_AUDIO]:
            self.v_audio.set(ga or NO_AUDIO)
        self.log(f"[device] video: {self.v_video.get()} | audio: {self.v_audio.get()}")
        self.on_device_change(True)

    def on_device_change(self, reload_modes=False):
        if self.recording or self._busy:
            return
        self._busy = True

        def work():
            if reload_modes:
                self.modes = cap.list_modes(self.v_video.get())
                labels = [m["label"] for m in self.modes] or ["(none)"]
                self.after(0, lambda: (self.om_mode.configure(values=labels),
                                       self.v_mode.set(self.cfg.get("mode") if self.cfg.get("mode") in labels
                                                       else labels[0])))
                time.sleep(0.2)
            self._stop_session()
            self.after(0, self._start_preview)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------- sessions
    def _on_frame(self, img):
        self._latest = img  # rendered on the Tk thread by _render

    def _on_exit(self, sess, rc):
        if sess is self.session and rc not in (0, None) and not getattr(sess, "stopping", False):
            self.log(f"[error] ffmpeg stopped unexpectedly (code {rc}). Is the device used by "
                     f"another app (OBS, Camera)? Unplug and replug the grabber, then pick it again.")

    def _new_session(self, out_path=None):
        mode = self._mode()
        if not mode:
            self._busy = False
            return None
        s = cap.Session(self.v_video.get(), self._audio(), mode, cap.STANDARDS[self.v_std.get()],
                        out_path, cap.FORMATS[self.v_fmt.get()], on_frame=self._on_frame,
                        on_log=self.log, monitor=self.monitor)
        s.on_exit = lambda rc, s=s: self._on_exit(s, rc)
        return s

    def _start_preview(self):
        try:
            self.session = self._new_session()
            if self.session:
                self.lbl_sig.configure(text="● preview", text_color="#8a8a8a")
        except Exception as e:  # noqa: BLE001
            self.log(f"[error] preview failed: {e}")
        finally:
            self._busy = False

    def _stop_session(self):
        s, self.session = self.session, None
        if s:
            s.stopping = True
            s.stop()

    def device_dialog(self):
        if self.recording:
            return
        self._busy = True

        def work():
            self._stop_session()
            cap.show_device_dialog(self.v_video.get())  # blocks until the dialog closes
            self.after(0, self._start_preview)
        threading.Thread(target=work, daemon=True).start()

    def toggle_record(self):
        if self.recording:
            self.stop_record("stopped by user")
        else:
            self.start_record()

    def start_record(self):
        if self._busy or not self._mode():
            return
        folder = self.v_folder.get().strip() or cap.DEFAULT_OUT
        try:
            out = cap.output_name(folder, self.v_name.get())
        except OSError as e:
            messagebox.showerror("Folder", f"Cannot use this folder:\n{folder}\n\n{e}")
            return
        if cap.free_gb(folder) < 5:
            if not messagebox.askyesno("Low disk space",
                                       f"Only {cap.free_gb(folder):.1f} GB free on this drive.\n"
                                       "A long tape can need 10+ GB. Record anyway?"):
                return
        try:
            self.limit_min = float(self.v_min.get().replace(",", ".") or 0)
        except ValueError:
            self.limit_min = 0.0
        self._save_cfg()
        self._busy = True
        self.btn_rec.configure(state="disabled")

        def work():
            self._stop_session()
            s = self._new_session(out)
            self.after(0, lambda: self._recording_started(s, out))
        threading.Thread(target=work, daemon=True).start()

    def _recording_started(self, s, out):
        if s is None:
            self._busy = False
            self.btn_rec.configure(state="normal")
            self.log("[error] could not start recording: no video mode selected")
            return
        self.session, self.recording, self._busy = s, True, False
        self.rec_path = out
        self.btn_rec.configure(state="normal", text="■  STOP", fg_color="#3f3f46", hover_color="#52525b")
        self.log(f"[record] {out}")
        if self.limit_min:
            self.log(f"[record] will stop after {self.limit_min:g} min")

    def stop_record(self, reason):
        if not self.recording:
            return
        self.recording = False
        self._busy = True
        self.btn_rec.configure(state="disabled", text="finishing file...")
        s, path = self.session, self.rec_path

        def work():
            self._stop_session()
            size = os.path.getsize(path) / 2**20 if os.path.exists(path) else 0
            self.log(f"[done] {reason}: {os.path.basename(path)} ({size:.1f} MB, {fmt_time(s.elapsed())})")
            if s.dropped:
                self.log(f"[warn] {s.dropped} frames were dropped during capture")
            if self.v_enh.get() and self.enh_py and size > 0:
                self._send_to_enhancer(path)
            self.after(0, self._recording_stopped)
        threading.Thread(target=work, daemon=True).start()

    def _recording_stopped(self):
        self.btn_rec.configure(state="normal", text="●  RECORD", fg_color="#dc2626", hover_color="#b91c1c")
        self.lbl_rec.configure(text="")
        self._start_preview()

    def _send_to_enhancer(self, path):
        # cmd /k keeps the window open so the result and any error stay visible.
        # /s + one outer pair of quotes = cmd keeps the inner quoted paths intact
        # (paths here contain spaces, e.g. "ANTIGRAVITY LAB").
        inner = " ".join(f'"{a}"' for a in (self.enh_py, os.path.join(self.enh_dir, "enhancer.py")))
        inner += f' -i "{path}" --denoise light'
        subprocess.Popen(f'cmd /s /k "{inner}"', cwd=self.enh_dir,
                         creationflags=subprocess.CREATE_NEW_CONSOLE)
        self.log("[enhance] Video Enhancer started in a new window (1080p, light denoise)")

    # ------------------------------------------------------- UI loops
    def _render(self):
        img, self._latest = self._latest, None
        if img is not None:
            pil = Image.fromarray(img)
            self._img.configure(light_image=pil, dark_image=pil)
        self.after(80, self._render)

    def _tick(self):
        s = self.session
        if s is not None:
            if s.flat:
                self.lbl_sig.configure(text="● NO SIGNAL (check cable, input, press PLAY)",
                                       text_color="#f59e0b")
            else:
                self.lbl_sig.configure(text="● signal OK", text_color="#4ade80")
            folder = self.v_folder.get() or cap.DEFAULT_OUT
            free = cap.free_gb(folder if os.path.isdir(folder) else cap.APP_DIR)
            if self.recording:
                el = s.elapsed()
                size = s.file_size()
                rate = size / max(el, 1) * 3600 / 2**30
                self.lbl_rec.configure(text="● REC" if int(el) % 2 == 0 else "○ REC")
                left = f"   ~{free / rate:.1f} h of space left" if rate > 0.01 else ""
                self.lbl_stats.configure(
                    text=f"{fmt_time(el)}   {size / 2**20:,.0f} MB   ~{rate:.1f} GB/h\n"
                         f"free: {free:,.0f} GB{left}   dropped frames: {s.dropped}")
                if self.limit_min and el >= self.limit_min * 60:
                    self.stop_record(f"time limit {self.limit_min:g} min")
                elif self.v_auto.get() and s.no_signal_for() >= 60:
                    self.stop_record("tape ended (60 s of no picture + silence)")
            else:
                self.lbl_stats.configure(text=f"ready   free: {free:,.0f} GB\n")
        self.after(500, self._tick)

    def on_close(self):
        if self.recording and not messagebox.askyesno(
                "Recording", "A recording is running. Stop it, save the file and quit?"):
            return
        self._save_cfg()
        self.recording = False
        self._stop_session()
        if self.monitor:  # only after ffmpeg is gone (it writes to the monitor socket)
            self.monitor.close()
        self.destroy()


if __name__ == "__main__":
    CaptureApp().mainloop()
