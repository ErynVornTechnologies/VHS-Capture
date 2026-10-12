"""VHS Capture - record VHS / Hi8 / camcorder tapes through a USB grabber.

Part of the Boring Stuff series by Erynvorn Technologies.
https://erynvorntechnologies.com | https://github.com/ErynVornTechnologies

One ffmpeg process per session (DirectShow input, video + audio together so
they stay in sync) with two outputs:
  1. the recording: MKV (crash-safe), video stream-copied when the grabber
     already delivers MJPEG (no extra loss, almost no CPU), FLAC audio
  2. a small live preview (480x360 + audio level bar) piped to the GUI as
     raw RGB frames, plus silencedetect events on stderr

"No signal" is detected in Python from the preview frames: grabbers show a
flat black or BLUE screen without input, so a near-zero pixel variance means
no picture, whatever its colour.
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime

import numpy as np

FFMPEG = r"C:\ffmpeg\bin\ffmpeg.exe" if os.path.exists(r"C:\ffmpeg\bin\ffmpeg.exe") else "ffmpeg"
NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(APP_DIR, "captures")

PREV_W, PREV_H, BAR_H = 480, 360, 32  # preview frame = picture + volume bar
PREV_FPS = 12
STANDARDS = {"PAL / SECAM (25 fps)": "25", "NTSC (29.97 fps)": "30000/1001"}
FORMATS = {
    "Original (no re-encode)": "copy",   # MJPEG straight from the grabber
    "Compact H.264": "h264",             # ~4x smaller, uses some CPU
    "Lossless FFV1": "ffv1",             # for raw (YUY2) grabbers
}


def _ff(args, timeout=20):
    return subprocess.run([FFMPEG, "-hide_banner"] + args, capture_output=True, text=True,
                          errors="replace", timeout=timeout, creationflags=NOWIN).stderr


def list_devices():
    """-> (video_names, audio_names) from DirectShow."""
    err = _ff(["-list_devices", "true", "-f", "dshow", "-i", "dummy"])
    video, audio = [], []
    for name, kind in re.findall(r'"([^"]+)" \((video|audio)\)', err):
        (video if kind == "video" else audio).append(name)
    return video, audio


def guess_grabber(video, audio):
    """Pick the likely capture card (not the webcam) and its audio input."""
    keys = ("usb2.0 pc camera", "easycap", "capture", "video grabber", "av to usb", "usbtv")
    v = next((d for d in video if any(k in d.lower() for k in keys)), video[0] if video else None)
    a = next((d for d in audio if "usb2.0 mic" in d.lower() or "capture" in d.lower()), None)
    return v, a


def list_modes(video_device):
    """-> list of dicts {codec|pix, w, h, fps_max, label} the device offers."""
    err = _ff(["-list_options", "true", "-f", "dshow", "-i", f"video={video_device}"])
    modes, seen = [], set()
    for kind, val, w, h, fps in re.findall(
            r"(vcodec|pixel_format)=(\S+)\s+min s=\d+x\d+ fps=[\d.]+ max s=(\d+)x(\d+) fps=([\d.]+)", err):
        key = (kind, val, w, h)
        if key in seen:
            continue
        seen.add(key)
        modes.append({"kind": kind, "val": val, "w": int(w), "h": int(h), "fps_max": float(fps),
                      "label": f"{val.upper()} {w}x{h}"})
    # analogue sources: prefer SD sizes, MJPEG first (it can be stream-copied)
    modes.sort(key=lambda m: (abs(m["h"] - 576) > 100, m["val"] != "mjpeg", -m["w"] * m["h"]))
    return modes


def show_device_dialog(video_device):
    """Open the driver's property page (brightness, contrast, video standard...)."""
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "dshow",
                    "-show_video_device_dialog", "true", "-i", f"video={video_device}",
                    "-frames:v", "1", "-f", "null", "-"], creationflags=NOWIN)


def free_gb(folder):
    try:
        return shutil.disk_usage(folder).free / 2**30
    except OSError:
        return float("nan")


class AudioMonitor:
    """Plays the capture audio on the PC speakers ("listen while recording").

    ffmpeg sends raw s16le stereo PCM to a localhost TCP socket that we own.
    Safety rules, because ffmpeg aborts ALL outputs (the recording too) if one
    output fails or blocks:
      * the socket stays open for the whole session; mute/volume only scale
        the samples in Python
      * the reader thread drains the socket non-stop into a bounded buffer;
        when the buffer runs ahead (grabber vs sound-card clock drift) the
        oldest audio is dropped, so latency stays ~0.2 s and ffmpeg never waits
    """

    def __init__(self, volume=1.0, muted=False):
        import sounddevice as sd  # optional dependency, only needed for listening
        self.rate = int(sd.query_devices(kind="output")["default_samplerate"])
        self.volume, self.muted = volume, muted
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._max = int(self.rate * 4 * 0.4)  # 0.4 s of 16-bit stereo
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.port = self._srv.getsockname()[1]
        self._stream = sd.RawOutputStream(samplerate=self.rate, channels=2, dtype="int16",
                                          latency="low", callback=self._play)
        self._stream.start()
        self._closed = False
        threading.Thread(target=self._serve, daemon=True).start()

    def url(self):
        return f"tcp://127.0.0.1:{self.port}"

    def _serve(self):
        while not self._closed:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            with conn:
                while not self._closed:
                    try:
                        data = conn.recv(65536)
                    except OSError:
                        break
                    if not data:
                        break  # this ffmpeg session ended; wait for the next one
                    with self._lock:
                        self._buf += data
                        if len(self._buf) > self._max:  # keep latency bounded
                            cut = len(self._buf) - self._max // 2
                            del self._buf[:cut - cut % 4]

    def _play(self, outdata, frames, _time, _status):
        n = frames * 4
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
        if self.muted or not chunk:
            outdata[:] = b"\x00" * n
            return
        a = np.frombuffer(chunk, np.int16)
        if self.volume != 1.0:
            a = np.clip(a.astype(np.float32) * self.volume, -32768, 32767).astype(np.int16)
        b = a.tobytes()
        outdata[:len(b)] = b
        if len(b) < n:
            outdata[len(b):] = b"\x00" * (n - len(b))

    def close(self):
        self._closed = True
        try:
            self._srv.close()
        except OSError:
            pass
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass


def build_cmd(video, audio, mode, rate, out_path=None, fmt="copy", monitor=None):
    """ffmpeg command: optional recording + always the preview on stdout."""
    # Wall-clock timestamps: cheap grabbers free-run without a signal (seen ~47
    # fps stamped as 30 fps, a 29 s capture became 47 s). Arrival time keeps
    # the duration real and video aligned with audio.
    cmd = [FFMPEG, "-hide_banner", "-nostats", "-loglevel", "info",
           "-f", "dshow", "-use_wallclock_as_timestamps", "1",
           "-rtbufsize", "512M", "-thread_queue_size", "4096",
           "-video_size", f"{mode['w']}x{mode['h']}", "-framerate", rate]
    cmd += ["-vcodec", mode["val"]] if mode["kind"] == "vcodec" else ["-pixel_format", mode["val"]]
    if audio:
        cmd += ["-audio_buffer_size", "50"]  # ms; smaller buffers keep A/V aligned
    cmd += ["-i", f"video={video}" + (f":audio={audio}" if audio else "")]

    fc = (f"[0:v]fps={PREV_FPS},scale={PREV_W}:{PREV_H}:flags=fast_bilinear,"
          f"format=rgb24[pv]")
    if audio:
        fc += (f";[0:a]silencedetect=noise=-50dB:d=2,showvolume=w={PREV_W}:h=14:f=0.3:"
               f"t=0:v=0:o=h:r={PREV_FPS},scale={PREV_W}:{BAR_H},format=rgb24[vol];"
               f"[pv][vol]vstack=shortest=0[out]")
    else:
        fc += f";[pv]pad={PREV_W}:{PREV_H + BAR_H}:0:0:black[out]"
    cmd += ["-filter_complex", fc]

    if out_path:
        cmd += ["-map", "0:v"] + (["-map", "0:a"] if audio else [])
        if fmt == "copy" and mode["val"] == "mjpeg":
            cmd += ["-c:v", "copy"]
        elif fmt == "h264":
            cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
                    "-pix_fmt", "yuv420p"]
        else:  # ffv1, or "copy" requested on a raw grabber
            cmd += ["-c:v", "ffv1", "-level", "3", "-g", "1", "-slicecrc", "1"]
        if audio:
            cmd += ["-c:a", "flac"]
        cmd += [out_path]
    if audio and monitor is not None:  # live listening on the PC speakers
        cmd += ["-map", "0:a", "-ac", "2", "-ar", str(monitor.rate), "-c:a", "pcm_s16le",
                "-f", "s16le", "-flush_packets", "1", monitor.url()]
    cmd += ["-map", "[out]", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    return cmd


def output_name(folder, name):
    name = re.sub(r'[<>:"/\\|?*]+', "_", (name or "Tape").strip()) or "Tape"
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{name}_{datetime.now():%Y-%m-%d_%H-%M-%S}.mkv")


class Session:
    """One running ffmpeg: preview only (out_path=None) or preview + recording.

    on_frame(np.ndarray HxWx3) is called from a worker thread for every
    preview frame; on_log(str) for notable ffmpeg messages; on_exit(rc).
    """

    def __init__(self, video, audio, mode, rate, out_path=None, fmt="copy",
                 on_frame=None, on_log=None, on_exit=None, monitor=None):
        self.out_path = out_path
        self.cmd = build_cmd(video, audio, mode, rate, out_path, fmt, monitor)
        self.on_frame, self.on_log, self.on_exit = on_frame, on_log or print, on_exit
        self.has_audio = bool(audio)
        self.silent_since = None     # wall-clock time silence started (None = sound)
        self.flat_since = None       # wall-clock time the picture went flat
        self.flat = False
        self.dropped = 0
        self.started = time.time()
        self.p = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, creationflags=NOWIN,
                                  bufsize=PREV_W * (PREV_H + BAR_H) * 3 * 2)
        threading.Thread(target=self._read_frames, daemon=True).start()
        threading.Thread(target=self._read_log, daemon=True).start()

    # -- workers
    def _read_frames(self):
        n = PREV_W * (PREV_H + BAR_H) * 3
        while True:
            buf = self.p.stdout.read(n)
            if not buf or len(buf) < n:
                break
            img = np.frombuffer(buf, np.uint8).reshape(PREV_H + BAR_H, PREV_W, 3)
            # flat picture (black OR the blue no-signal screen) -> no signal
            # per channel: a blue screen has very different R, G and B but no detail
            flat = float(img[8:PREV_H - 8:4, 8:-8:4].reshape(-1, 3).std(axis=0).max()) < 3.0
            if flat and not self.flat:
                self.flat_since = time.time()
            self.flat = flat
            if self.on_frame:
                self.on_frame(img)
        rc = self.p.wait()
        if self.on_exit:
            self.on_exit(rc)

    def _read_log(self):
        for raw in iter(self.p.stderr.readline, b""):
            line = raw.decode(errors="replace").strip()
            if "silence_start" in line:
                self.silent_since = time.time()
            elif "silence_end" in line:
                self.silent_since = None
            elif "frame dropped" in line:
                # dshow buffer overflow: the PC fell behind the tape
                self.dropped += 1
                if self.dropped in (1, 10, 100) or self.dropped % 1000 == 0:
                    self.on_log(f"[warn] {self.dropped} frame(s) dropped by the capture buffer")
            elif any(k in line for k in ("Error", "error", "Could not", "I/O")):
                self.on_log(line)

    # -- state
    @property
    def running(self):
        return self.p.poll() is None

    def elapsed(self):
        return time.time() - self.started

    def file_size(self):
        try:
            return os.path.getsize(self.out_path) if self.out_path else 0
        except OSError:
            return 0

    def no_signal_for(self):
        """Seconds of flat picture AND silence (silence ignored without audio)."""
        if not self.flat or self.flat_since is None:
            return 0.0
        t = time.time() - self.flat_since
        if self.has_audio:
            t = 0.0 if self.silent_since is None else min(t, time.time() - self.silent_since)
        return t

    def stop(self, timeout=15):
        """Ask ffmpeg to finish cleanly ('q'), so the MKV gets its index."""
        if not self.running:
            return self.p.returncode
        try:
            self.p.stdin.write(b"q")
            self.p.stdin.flush()
        except OSError:
            pass
        try:
            return self.p.wait(timeout)
        except subprocess.TimeoutExpired:
            self.p.kill()
            return self.p.wait()


def record(video=None, audio=None, folder=DEFAULT_OUT, name="Tape", standard="PAL / SECAM (25 fps)",
           fmt="copy", minutes=0.0, auto_stop=60, log=print):
    """Blocking recording for the CLI. Stops on time limit, no signal or Ctrl+C."""
    vids, auds = list_devices()
    gv, ga = guess_grabber(vids, auds)
    video = video or gv
    audio = ga if audio is None else (audio or None)
    if not video:
        raise SystemExit("No video capture device found.")
    modes = list_modes(video)
    if not modes:
        raise SystemExit(f"Device '{video}' reports no usable video modes.")
    out = output_name(folder, name)
    log(f"[device] {video} | audio: {audio or 'none'} | mode: {modes[0]['label']}")
    log(f"[record] {out}  (free: {free_gb(folder):.1f} GB)  Ctrl+C to stop")
    s = Session(video, audio, modes[0], STANDARDS[standard], out, fmt, on_log=log)
    try:
        while s.running:
            time.sleep(1)
            el = s.elapsed()
            print(f"\r  {time.strftime('%H:%M:%S', time.gmtime(el))}  "
                  f"{s.file_size() / 2**20:8.1f} MB  {'NO SIGNAL' if s.flat else 'signal OK'}   ",
                  end="", flush=True)
            if minutes and el >= minutes * 60:
                log("\n[stop] time limit reached")
                break
            if auto_stop and s.no_signal_for() >= auto_stop:
                log(f"\n[stop] no picture and no sound for {auto_stop} s (end of tape?)")
                break
    except KeyboardInterrupt:
        log("\n[stop] Ctrl+C")
    rc = s.stop()
    log(f"[done] {out}  {s.file_size() / 2**20:.1f} MB, exit code {rc}")
    return out


def main():
    ap = argparse.ArgumentParser(
        description="VHS Capture: record tapes through a USB video grabber",
        epilog="Erynvorn Technologies | https://erynvorntechnologies.com")
    ap.add_argument("--list", action="store_true", help="list capture devices and modes")
    ap.add_argument("--video", help="video device name (default: auto-detect grabber)")
    ap.add_argument("--audio", help="audio device name ('' = no audio; default: auto)")
    ap.add_argument("--folder", default=DEFAULT_OUT)
    ap.add_argument("--name", default="Tape")
    ap.add_argument("--standard", choices=list(STANDARDS), default="PAL / SECAM (25 fps)")
    ap.add_argument("--format", choices=list(FORMATS.values()), default="copy")
    ap.add_argument("--minutes", type=float, default=0, help="stop after N minutes (0 = off)")
    ap.add_argument("--auto-stop", type=int, default=60,
                    help="stop after N s of no picture + silence (0 = off)")
    a = ap.parse_args()
    if a.list:
        vids, auds = list_devices()
        gv, ga = guess_grabber(vids, auds)
        for v in vids:
            print(f"video: {v}{'   <- grabber' if v == gv else ''}")
            for m in list_modes(v):
                print(f"         {m['label']} up to {m['fps_max']:.0f} fps")
        for d in auds:
            print(f"audio: {d}{'   <- grabber' if d == ga else ''}")
        return
    record(a.video, a.audio, a.folder, a.name, a.standard, a.format, a.minutes, a.auto_stop)


if __name__ == "__main__":
    main()
