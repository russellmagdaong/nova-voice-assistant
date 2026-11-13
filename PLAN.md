# N.O.V.A. — Not Only a Voice Assistant

A Windows background assistant. Say "Hey Nova", ask anything, hear a short spoken answer. It can also search the web, control approved apps and the PC, and keep reminders and notes. It lives in a small notch at the bottom of the screen.

Status (2026-11-14):
- **Phase 1 is done and in daily use:**
  - core loop and conversation mode
  - talk-over
  - Whisper large-v3-turbo and Kokoro on the GPU
  - mute frees video memory
- **Phase 2 is built and passes the simulated-room test:** web search, plus NOVA's own weather and news tools. It needs a live check by the user, whose city is set to Manila, Philippines.
- **Most of phase 5 was done early:**
  - the notch with switchable mascots
  - fullscreen hiding
  - tray
  - `config.toml`
  - setup, start and install-startup scripts
  - README
- **Phase 3 (PC control) is built and tested (2026-11-14).** It needs a live check by the user.
  - Covers media keys, volume, timers, and opening allowed apps.
  - The "Apps NOVA can open" window is the settings window.
- **Phase 4 (reminders and notes) is on hold** at the user's request.

### Phase 3 results (PC control)
- The user asked that it be secure and that it never delete important items or do anything that could break the PC. So it's a fixed list of harmless actions with checked inputs, and nothing else.
- **Tool host** (`nova/tool_host.py`): NOVA's tools (weather, news and the PC tools) are served from inside NOVA as an MCP server over HTTP.
  - It listens on 127.0.0.1 only, on a random port.
  - Requests need a per-run random bearer token, compared with `hmac.compare_digest`. Requests with an Origin header (browsers) are refused (403), and bodies are capped at 256 KB.
  - Running inside NOVA keeps timers alive across Claude restarts and lets them ring through NOVA.
  - This replaced the stdio `tools_server.py` process. That module keeps the weather and news functions, the MCP handler and schema-checked argument validation (types, enums, ranges, required fields, lengths ≤ 100; unknown arguments dropped).
- **PC tools** (`nova/pc_tools.py`):
  - **Media keys:** `keybd_event`.
  - **Volume:** Windows Core Audio `IAudioEndpointVolume` through raw ctypes COM, so no new packages.
  - **Timers:** 1 s to 24 h, at most 10.
  - **`open_app` / `list_apps`:**
    - Apps come from `Get-StartApps`: 218 apps here, of which 54 can never be allowed.
    - Launching runs `explorer.exe shell:AppsFolder\<stored AppID>`, without a shell.
    - Matching is fuzzy and uses aliases.
    - `BLOCKED_APPS` (shells, admin and system tools, uninstallers, installers, reset and recovery) is enforced both in the window and at launch.
- **Not possible, by design:** deleting, moving or editing files; running commands; installing or uninstalling; changing settings other than the volume; closing apps; power actions. The system prompt tells Claude to say so. Tested: "delete my Downloads folder", "shut down", "run ipconfig" and "close all my apps" were all refused without a tool call.
- **Timer alerts:** they ring after a chime, by voice and in the notch (ALERT state, status "Timer").
  - If NOVA is idle or muted, the main loop checks for alerts every 0.5 s.
  - In a conversation, the alert plays right after the current answer.
  - Clicking the notch stops it.
- **Speed:** a PC action adds ~1.5 s for the tool round trip; first word ~3.4-5.6 s. Possible later: instant local commands for "pause" or "volume up" without Claude.
- **Tray icons:** no white outline except the black cat (2026-11-14, at the user's request).

### Phase 2 results (web search)
- Claude Code's built-in WebSearch is the session's only tool: `--tools WebSearch --allowedTools WebSearch --permission-prompts none`. Anything else that would need permission is refused automatically.
- The system prompt says to search only for current or uncertain facts, and to do one quick search in standard mode. On its first run Haiku did two searches, one of them extended, and took 13-17 s. With the instruction it takes 8-10 s.
- When Claude starts a search, NOVA says a filler line ("Let me check." etc.) about 1 s after the question; the end-to-end first word comes 3.7-4.1 s after you stop talking. The notch shows "Searching the web" (pixel mascots glance left and right), then switches to speaking when the answer's text starts. Echo of the filler is ignored correctly on speakers.
- No `location` set: the prompt gives the Windows region (Philippines) and Claude asks which city for weather.
- **Weather and news tools (2026-11-14).** Search gave vague live weather ("one undated snapshot showed about 30 degrees") and stale news (Nov 9-10 stories on Nov 13).
  - `nova/tools_server.py` was first NOVA's own MCP server: standard library only, JSON-RPC over stdio, started by Claude Code via `--mcp-config`, with `--strict-mcp-config` so it's the only MCP server. Since phase 3 the same tools are served by `tool_host.py` inside NOVA. `tools_server.py --test` still runs them on their own.
  - `get_weather(place, days)` uses Open-Meteo geocoding and forecast (current conditions, the next 12 h every 3 h, daily forecast). With no place it uses `[claude] location`.
  - `get_news(topic, world, count)` uses Google News RSS for the user's country edition (PH), sections or search with `when:2d`, and each headline's age.
  - Both are read-only and free, need no API key, retry once, and limit input length.
  - Measured with Haiku:

    | Question | Total time |
    |---|---|
    | Weather | 5.6 s |
    | News | 3.5 s |
    | Web search | 9-11 s |
    | Follow-up from the same forecast ("will it rain tomorrow?"), no new call | 1.3 s |

  - The notch shows "Checking the weather" / "Getting the news" / "Searching the web".
  - The Open-Meteo forecast host once failed its TLS handshake twice in a row (a network hiccup). It answered in ~0.85 s right after; hence the retry.
- Fullscreen hiding (phase 5): the foreground window covers the notch's monitor and isn't a maximised captioned window, or Windows reports a D3D-exclusive or presentation state. Checked once a second; tested with a fullscreen and a maximised window from another process.

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
- Claude gets **only** WebSearch and the NOVA tools (weather, news, media keys, volume, timers, open allowed apps). Never Bash, PowerShell or file tools. Anything that would prompt for permission is refused (`--permission-prompts none`).
- Every NOVA tool validates its input. `open_app` only launches apps the user has switched on in settings.
- The mic is ignored while NOVA speaks, plus ~400 ms afterwards, so it can't wake itself.

### App discovery and permissions
- Discover installed apps with `Get-StartApps`. It lists Start-menu apps, both desktop and Store apps, with an AppID that launches via `explorer.exe shell:AppsFolder\<AppID>`. Rescan on demand.
- The settings window lists every app with a toggle. Only toggled apps are controllable. Optional spoken aliases per app (e.g. "browser" → Zen Browser).
- Starter list to verify: Spotify, Claude, Zen Browser, File Pilot.

### Notch UI
- Placement: bottom centre of the screen (the taskbar is at the top, so there's no conflict).
- Collapsed: the mascot peeking up from the bottom edge (ears and eyes), or the orb's thin pill.
- Expands on mouse hover, or when NOVA is activated by voice: the mascot pops up and a Windows 11 style card in its own window, no gradients (dark mode: black glass, translucent black without blur, the user's choice, because acrylic turns a black tint solid; light mode: acrylic blur) shows Listening / Thinking / answer text, active timers and upcoming reminders.
- **Mascot** (tray > Mascot, each entry shows its icon):
  - **Pixel mascots** (`nova/pixel*.py`), drawn as text grids and scaled up in whole pixels: black cat, robot, fox, frog, ghost and axolotl.
  - **Orb** (`nova/orb.py`), in the classic pill notch.
  - **History:**
    - The cat replaced the orb on 2026-11-08. A smooth cat generated by Codex was tried first, then the user asked for pixel art.
    - The robot and the orb were added as options the same day.
    - 2026-11-13:
      - The fox, frog, ghost and axolotl were drawn from one brief by both Codex (gpt-6.1-sol) and Antigravity (Gemini 3.8 Flash). Codex's set was clearly better: outlines, readable eyes and greyed muted icons. Antigravity's needed file-only mode because its headless permissions blocked commands.
      - The orb was redrawn as a glowing core inside a mesh of jittered points linked to their nearest neighbours. The old latitude/longitude wireframe looked like a globe at rest.
  - Pixel mascot behaviour:
    - idle: peeks, blinks, glances left/right
    - listening: ears perked, wide eyes, hops with your voice
    - thinking: looks up; progress bar moves on the card
    - searching the web: glances left and right; "Searching the web" on the card
    - speaking: mouth opens and the head bobs with NOVA's voice
    - alert (reminder/timer): hops
    - muted: asleep with drifting pixel z's
    - error: ears flat, sad eyes, a quick shake
- Collapses again after the answer finishes or the mouse leaves.
- Hides when a fullscreen app (game, video, presentation) is in front (`[ui] hide_in_fullscreen`). Done.
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
- The notch's look is switchable (tray > Mascot): pixel-art black cat, pixel robot (both peek up from the edge, with a Windows-style acrylic card above), or the orb notch (pill that opens into a panel with a wireframe orb). Added 2026-11-08 at the user's request.
- Wake phrase is "Hey Nova" only (no plain "Nova").

## Open questions

- (none right now)
