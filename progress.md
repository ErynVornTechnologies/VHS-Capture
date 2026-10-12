# progress.md: VHS Capture (Boring Stuff #09)

Handover notes for any agent picking this up.

## Status (2026-10-09)

- v1 working; tested on Kamil's PC with the real grabber but **without a source
  signal** (nothing connected). Real-tape test is the next step.
- Repo prepared for GitHub, **not pushed**. Planned remote:
  `github.com/ErynVornTechnologies/VHS-Capture` (name to be confirmed by Kamil).
- Request (Kamil): app like Video Enhancer, but for capturing old VHS and camcorder
  tapes, with a choice of output folder; default = app folder (`captures\` inside it).

## Hardware facts (measured, not assumed)

- Grabber: `USB2.0 PC CAMERA`, VID_18EC PID_5555 (Arkmicro, EasyCap-style), DirectShow.
- **Only mode: MJPEG 640x480, 5-60 fps** (`capture.py --list`). Full-range JPEG (pc).
- Audio: `Microphone (2- USB2.0 MIC)`, 44.1 kHz stereo s16. The "2-" prefix can
  change, so `guess_grabber()` matches on "usb2.0 mic".
- No signal: a few black frames, then a **solid blue screen** (RGB ~11/13/249).
- No signal: the grabber **free-runs (~47-48 fps) while stamping 30 fps**, so a 29 s
  recording became a 47 s file. Fixed with `-use_wallclock_as_timestamps 1`;
  A/V durations now match to the millisecond. Behaviour with a real PAL signal is not verified yet.
- Also on the PC: Logitech C920 webcam (excluded by `guess_grabber`), OBS Virtual
  Camera, Realtek line-in, Focusrite.

## Architecture

- `capture.py`: `list_devices`, `guess_grabber`, `list_modes`, `build_cmd`, `Session`, CLI.
  One ffmpeg, one dshow input (video+audio together) and two outputs: the MKV recording
  (MJPEG `-c:v copy` + FLAC) and a rawvideo preview pipe (480x360 + 32 px showvolume
  bar = 480x392 RGB at 12 fps). `silencedetect` events are parsed from stderr.
- "No signal" = per-channel std of the preview picture < 3 (black and blue both flat).
  The first version used joint RGB std, and blue gave std 111: a bug, fixed.
- Stop = write `q` to stdin, so the MKV is finalised (exit 0, playable).
- `gui.py`: CustomTkinter, two columns; settings in `settings.json` (gitignored).
  Auto-stop = 60 s of flat picture AND silence; time limit; free-space warning (< 5 GB);
  dropped-frame counter (dshow "real-time buffer ... frame dropped").
- Hand-off to Video Enhancer: looks for `..\08_Video_Enhancer` or `..\Video-Enhancer`
  with a `.venv`; launches `cmd /s /k ""python" "enhancer.py" -i "file" --denoise light"`
  (the outer quotes plus `/s` are needed because paths contain spaces).

## Speaker monitoring (added 2026-10-10, Kamil: "I see the meter but hear nothing")

- `AudioMonitor`: ffmpeg gets a 3rd output, `-map 0:a ... -f s16le tcp://127.0.0.1:<port>`.
  Python accepts it and plays it through `sounddevice` (default output, its native rate).
- **Never close the monitor socket while ffmpeg runs.** One failing or blocked output makes
  ffmpeg abort the recording too. Mute and volume only scale samples in Python. The reader
  always drains; the buffer is capped at 0.4 s, then trimmed to 0.2 s (clock drift).
- One monitor for the app's lifetime, reused by every session (tested: 2 sessions in a row,
  ffmpeg rc 0, real-time playback, buffer ~0 ms). Closed only after the last ffmpeg is gone.
- Windows-only alternative with no code: Recording devices → USB2.0 MIC → Listen → "Listen to this device".

## Tests done

- CLI: 30 s recording (exit 0); auto-stop at 8 s on blue screen + silence; durations match.
- GUI (scripted): preview → record 8 s → stop → preview resumes → close; 0 ffmpeg left.
- Capture → Enhancer hand-off: 1440x1080 25p H.264 + AAC, same duration.
- Lip sync through Enhancer, synthetic: video starting 0.6 s after audio, flash vs
  beep at 2.00 / 2.02 s in the output. In sync.

## Related changes made in 08_Video_Enhancer (same day)

- FLAC/PCM/Opus audio is now converted to AAC (TVs often refuse FLAC in MP4).
- `BrokenPipeError` when the encoder ends first (`-shortest`) is no longer a failure;
  the encoder's exit code decides.
- An attempted "A/V offset fix" was WRONG and reverted: the CFR rawvideo decoder
  already pads late-starting video from t=0. Do not re-add it.

## First real tape (2026-10-10, Kamil's VCR via SCART adapter rewired to OUT pins 19/1/3)

File `captures\Tape_2026-10-10_17-40-33.mkv`, recorded with "Compact H.264":
- **24.93 fps real** (PAL lock OK), 1788 frames / 71.7 s; video 72.44 s vs audio 72.45 s.
- 5 dropped frames (80 ms gaps) in 72 s (~0.3 %). Watch this on full-length tapes.
- **No interlacing in the output**: idet 1788/1788 progressive, no combing in 1:1 crops.
  This Arkmicro grabber merges the fields internally, so Video Enhancer stays at 25p (no 50p).
- Audio OK: mean -12.6 dB, **max -0.3 dB** (close to clipping; lower the grabber's
  input level in Windows if distortion is audible).
- Colour fine for VHS; dark ~4 px at the right edge; the VCR's on-screen PLAY icon appears at the start.
- Compact H.264 ≈ 5.6 Mbit/s ≈ 2.5 GB/h. MJPEG "Original" size per hour not measured yet.
- Enhancer test (span, light denoise, crop 0,0,4,4): 25 f/s, output 1444x1080 25p. Visible
  but modest gain: crisper edges, slight painterly texture on foliage. The source is soft and
  "Compact" adds a generation loss, so use "Original" for real archiving.
- NOTE: two pythonw.exe processes per app window is NORMAL: the venv pythonw.exe is a
  redirector that spawns the base interpreter. It is not two app instances.

## Open items

- [ ] Real tape test: check interlacing in the 640x480 MJPEG (combing or blended?),
      fps lock to 25 with a PAL signal, dropped frames over a full hour, file size per hour.
- [ ] Check what the VCR shows after the tape ends (blue/black = auto-stop works; snow = time limit only).
- [ ] Optional: trim the trailing no-signal part automatically after auto-stop.
- [ ] Push to GitHub.
