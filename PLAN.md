# N.O.V.A. — Not Only a Voice Assistant

A Windows background assistant. Say "Hey Nova", ask anything, hear a short spoken answer. It can also search the web, control approved apps and the PC, and keep reminders and notes. It lives in a small notch at the bottom of the screen.

Status: **phase 1 built** (core loop + conversation mode), waiting on a live test. Next: phase 2 (web search).

### Conversation mode (added to phase 1 at the user's request)
- "Hey Nova" starts a conversation; follow-ups need no wake word; ends after 10 s of silence, "that's all"/"goodbye"/"never mind", or a click.
- Talk-over: WebRTC echo cancellation (livekit APM). It must get the speaker signal in step with the mic (10 ms reference per 10 ms capture, silence when idle); bursty feeding gave 2.8 dB instead of 45 dB.
- Echo vs. user check: on a trigger NOVA ducks itself to 20% for 0.5 s; echo falls away (2702 -> 64), a real voice doesn't (1735 -> 941).
- Simulated-room test (`tools/conversation_test.py`): all scenarios pass; typical latency 3.5-4.4 s, edge-tts outliers up to ~6 s. Long opening sentences are now split at a comma.
- The user uses headphones and speakers; speakers are the harder case and need a live check.

### Phase 1 results (simulated mic, real Whisper/Claude/edge-tts)
- End of speech → first spoken word: 3.5–3.8 s typical (1 s silence wait + ~0.9 s Whisper, ~0.8 s Claude first text, ~0.6 s first audio). One 7.2 s outlier from a slow edge-tts response.
- Fixes found: chime must not flush queued mic audio; pause check at 0.7 s (not 0.45, commas triggered it); 2 s pre-roll; edge-tts warm-up at startup (first request 1.5–4 s); accept "Inova," (comma) as the wake phrase.
- Known: Vosk near-misses ("Hey Noah") briefly flash the notch to Listening before Whisper rejects them.
- CUDA Whisper needs `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` (~1 GB); not installed.
- Tests: `tools/conversation_test.py` (replaced `pipeline_test.py`).

### Phase 0 results (generated voices, clean + noisy)
- "nova" is in the Vosk small model's vocabulary.
- Two-stage wake check: Vosk with a decoy grammar (catches 100%, fires on ~1/3 of near-misses) → Whisper confirm. Result: 133/144 real wake-ups accepted (92%, misses all in heavy noise), 0/129 near-misses let through.
- Whisper `base.en` CPU int8 is as accurate as `small.en` here and faster (~0.6 s per clip).
- Prompting Whisper with "Nova is my voice assistant. I use Claude, Spotify, …" fixes custom words: "Claude" went from often wrong (cloud, clod, Lord) to always right.
- After "Hey Nova": wait up to 6 s for the command to start, then end after 1 s of quiet.
- PyAV 19 breaks `faster_whisper.decode_audio`; decode with PyAV directly.
- Tools: `tools/wake_test.py`, `tools/vocab_test.py`, `tools/mic_test.py`.

## Fixed decisions

- **Wake word:** "Hey Nova" only. Offline detection, always on.
- **Brain:** Claude Code CLI on the signed-in subscription. One persistent `claude` process in stream-json mode (measured ~2–4 s to first words). No API keys, no paid services.
- **Speech to text:** faster-whisper. CPU int8 by default, CUDA (RTX 4050) optional with CPU fallback.
- **Voice:** edge-tts (e.g. `en-US-AvaMultilingualNeural`, configurable), spoken sentence by sentence as the answer streams in. Offline Windows voice as the fallback.
- **UI:** a notch at the bottom centre of the screen plus a tray icon. A settings window for app permissions and options.

## Architecture

```
mic ─► wake word (Vosk, offline) ─► record question ─► Whisper ─► transcript
                                                                     │
                          quick local commands ◄─────────────────────┤ stop / cancel / mute / new conversation
                                                                     ▼
                                                   Claude Code process (persistent)
                                                     ├─ WebSearch
                                                     └─ NOVA tools (local MCP server)
                                                          ├─ open_app(name)      approved apps only
                                                          ├─ timers, volume, media keys
                                                          └─ reminders / notes   stored locally
                                                                     │
                                   notch UI  ◄─── status + text ─────┤
                                   speakers  ◄─── edge-tts ──────────┘
```

### Safety model
- Claude gets **only** WebSearch and the NOVA tools. Never Bash, PowerShell or file tools.
- Every NOVA tool validates its input. `open_app` only launches apps the user has switched on in settings.
- The mic is ignored while NOVA speaks, plus ~400 ms afterwards, so it can't wake itself.

### App discovery and permissions
- Discover installed apps with `Get-StartApps`. It lists Start-menu apps, both desktop and Store apps, with an AppID that launches via `explorer.exe shell:AppsFolder\<AppID>`. Rescan on demand.
- The settings window lists every app with a toggle. Only toggled apps are controllable. Optional spoken aliases per app (e.g. "browser" → Zen Browser).
- Starter list to verify: Spotify, Claude, Zen Browser, File Pilot.

### Notch UI
- Placement: bottom centre of the screen (the taskbar is at the top, so there's no conflict).
- Collapsed: the mascot peeking up from the bottom edge (ears and eyes).
- Expands on mouse hover, or when NOVA is activated by voice: the mascot pops up and a Windows 11 style card (teaching-tip look, no gradients) shows Listening / Thinking / answer text, active timers and upcoming reminders.
- **Mascot:** a pixel-art black cat (`nova/pixelcat.py`, drawn as text grids, scaled up in whole pixels). Replaced the orb on 2026-11-08 at the user's request; a smooth Codex-generated cat was tried first, then the user asked for pixel art like a pixel game.
  - idle: peeks, blinks, glances left/right
  - listening: ears perked, wide eyes, hops with your voice
  - thinking: looks up; progress bar moves on the card
  - speaking: mouth opens and the head bobs with NOVA's voice
  - alert (reminder/timer): hops
  - muted: asleep with drifting pixel z's
  - error: ears flat, sad eyes, a quick shake
- Collapses again after the answer finishes or the mouse leaves.
- Hides when a fullscreen app (game, video) is in front.
- Reminders and timers alert by expanding the notch and speaking.
- Toolkit: **PySide6 (Qt)**. It gives frameless translucent windows, anti-aliased rounded shapes and smooth expand animations, and covers the tray icon and settings window too. tkinter can't do a polished notch.

## Build phases

Each phase works on its own before the next one starts.

0. **Wake-word test.** Check "nova" is in the Vosk model vocabulary. Generate clips with edge-tts ("Hey Nova, …", sentences without the wake word, near-misses like "bossa nova", "Casanova"). Measure misses and false triggers.
1. **Core loop.** Wake word → record → Whisper confirm → Claude → spoken answer. Chimes, 5-minute conversation memory, "new conversation", "cancel". Basic notch states and tray icon (Mute, Quit). Target: about 4 s or less from end of speech to first spoken word.
2. **Web search.** Weather, news, current facts. Optional `location` setting.
3. **PC control.** NOVA tools MCP server: app discovery + permission toggles in settings, open/focus apps, timers, volume, media play/pause/next.
4. **Reminders and notes.** Local storage, background scheduler, alerts through the notch and voice.
5. **Polish.** Notch animations, fullscreen detection, `config.toml`, `setup.ps1`, `start.ps1`, `install-startup.ps1` (the user runs it), README.

## Resolved

- Notch at the bottom centre; taskbar is at the top.
- The notch is a pixel-art black cat with a Windows-style card (was an animated orb).
- Wake phrase is "Hey Nova" only (no plain "Nova").

## Open questions

- (none right now)
