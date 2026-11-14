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
  - Covers media keys, volume, timers, and opening allowed apps. Spotify search and play was added later.
  - The settings window is now NOVA settings (below).
- **NOVA settings window (2026-11-14), at the user's request:** "a centralized window where I can access all the settings". The tray's quick settings stay, and the tray gains "Open NOVA settings…". (Later the settings moved into NOVA's window, below, and this separate window was removed.)
  - `nova/settings_window.py` plus `nova/fluent.py`, which holds the Windows 11 parts: sidebar buttons, cards, toggle switches, Mica through `DWMWA_SYSTEMBACKDROP_TYPE` plus a dark title bar, and the Segoe Fluent Icons font. Inputs keep Qt 6.12's windows11 style.
  - Pages: Home, Timers, Reminders, Notes, Apps, Appearance, Voice, Assistant, Privacy & safety. They replace the separate Apps and Reminders & notes windows.
  - Settings save to `state.json` "settings" and are applied over config.toml at start (`config.apply_overrides`, known keys and types only).
    - Live: city, model, time to reply, speed, hide over fullscreen.
    - After a restart: lookups, PC control, reminders, live captions. "Restart NOVA" starts `-m nova --restarted`, which waits up to 20 s for the lock, then quits the old one.
  - The user's PC runs a tiling window manager: new windows open at 952x1000 whatever size is asked for.
- **Logo and colour themes (2026-11-14):**
  - Claude drew the logo (`assets/nova.svg`, a star sending out sound waves, with a simplified version for 16-24 px). The user disliked the purple-pink gradient.
  - `nova/themes.py` adds themes for the logo and the orb: Ocean (the default at first), Aurora, Sunrise, Forest, Graphite, Nebula (the old purple-pink), and Windows accent (from `AccentPalette`).
  - Logo SVGs use placeholder colours `#0000F1/2/3`, swapped at runtime. The orb reads `themes.current_colors()`; muted grey, error red and alert orange stay fixed.
  - The theme is picked in NOVA settings > Appearance (live: window icon, orb, orb tray icon) and saved as `ui.theme`.
  - `tools/make_logo.py` renders the PNGs, `.ico` and wordmarks in the default theme, plus `samples/logo_themes.png`.
  - **Codex (gpt-6.1-sol) drew six more themeable logos** from a brief: original (not like Siri, Copilot, Alexa, Google or Cortana), SVG Tiny, placeholder colours, and readable at 16 px with `-small` versions.
    - They are Daybreak (an eight-point burst), Cadence (a stepped voice signal), Slingshot (a planet and orbit), Starling (a star in a speech bubble), North (an N monogram) and Lookout (an assistant face).
    - For a while they lived in `assets/logos/` with a Logo picker in NOVA settings > Appearance (`ui.logo`).
  - **Final logo: Slingshot (2026-11-14).** The user chose it (in Sunrise) and asked for the logo options to go.
    - Slingshot is now `assets/nova.svg` and `nova-small.svg`. `assets/logos/`, the Logo picker, `ui.logo` and `themes.LOGOS` are gone; a leftover `ui.logo` in state.json is ignored.
    - Sunrise is the default theme. The startup `.ico`, PNGs and README wordmarks are Slingshot in Sunrise; the other themes stay pickable.
- **Phase 4 (reminders and notes) is built and tested (2026-11-14).** It needs a live check by the user.
- **NOVA's window (2026-11-14),** at the user's request: "a window for nova … something like a JARVIS ui that i can maximize AND make fullscreen … have the settings there as well. when the window is idle, make texts and buttons hidden, and only keep the mascot in the center".
  - `nova/hud.py`, opened from tray > Open NOVA. All drawn with QPainter on one widget (Bahnschrift for the HUD labels), in the colour theme's colours (`orb.style` per state).
    - Centre: the mascot. The orb uses `orb.paint`; the pixel mascots reuse `Sprite` for frames, scaled by whole pixels.
    - Rings around it: hairlines, a turning tick ring, arcs at different speeds, and a dashed ring. While thinking or searching, a conical scanner sweeps and the arcs speed up. 96 voice ripples follow the mic or speaker level, and an arc counts down the time to reply.
    - The conversation is drawn under the ring with `WordFlow`, centred.
    - Chrome: corner brackets, the name and status, time and date (with a seconds bar), the weather (`tools_server.current_weather`, every 20 min), CPU and memory (`GetSystemTimes`, `GlobalMemoryStatusEx`), what's next (`assistant.upcoming`), and Spotify's song. The song comes from Spotify's window title (`EnumWindows`, in-process), never from spotify_cli, so it can't start Spotify. There are six round buttons.
    - Fades: the chrome shows for 3 s after any mouse move or key, and the conversation while NOVA is busy plus 6 s. Fully idle leaves only the mascot, the pointer is hidden, and only the mascot's area repaints, at 30 fps for the orb and 15 fps for pixel mascots. Painting a 1280x800 frame takes 2-7 ms.
    - Settings: `settings_window.SettingsPanel` (the sidebar and pages, split out of `SettingsWindow`) in a drawer that slides in from the right, accented in the theme's middle colour. With it open, the readouts fade and the mascot moves left.
    - Click the mascot or press Space: `bridge.talk_requested` makes the assistant chime and listen as if "Hey Nova" was said (`_handle_talk`, then `_listen_once`). If NOVA is busy it stops it instead; if muted it unmutes.
    - F11 or a double-click toggles fullscreen; Esc closes settings, then leaves fullscreen. Closing only hides the window. While it's in front (active, maximised or fullscreen) `Notch.set_hud_front` hides the notch, which would otherwise sit on the button row.
  - Tested:
    - Offscreen renders of every state (idle with chrome, idle with only the mascot, listening, searching, speaking, settings, muted) for the orb and the fox.
    - On screen, with the user's tiling window manager: 952x1000 at first, fullscreen 1920x1080, maximised 1920x1017, the drawer, and the notch hiding and coming back.
    - The conversation test has a new "click to talk" scenario: it heard the question and answered in 4.4 s.
- **NOVA's window, second pass (2026-11-14).** The user asked for: "make the ui minimalistic there's too much happening", "the ui color changes based on the mascot", a new font, "remove the notch when the window is active", "make nova the centralized app and remove the separate window for the nova settings", a different pixel background for every mascot with the orb kept minimal, and a fix for "i can't choose a mascot in the settings within nova". Weather was also timing out.
  - Minimal: the rings, scanner, voice ripples, tick marks, corner brackets, dot grid, CPU and memory, and two of the buttons are gone.
    - Left: the mascot; the time, date and weather (top left); Next and Now playing (bottom corners, only when there's something); four buttons (talk, mute, settings, fullscreen); and the conversation.
    - The orb gets one thin ring, only during a conversation, with a turning arc while thinking and a fill for the countdown.
    - The mascot never moves or resizes; the text goes under it. Pixel mascots get a pixel text box with a 2 px border, and the buttons are square.
  - Colours follow the mascot (`MASCOT_COLORS`, taken from each mascot's own palette). The orb follows the theme. The settings drawer's accent follows too.
  - Fonts, downloaded from Google Fonts into `assets/fonts` with their OFL licences, loaded with `QFontDatabase.addApplicationFont`: Pixelify Sans with the pixel mascots, Outfit with the orb.
  - Scenes (`nova/scenes.py`), drawn in code at half the mascot's pixel size with Bayer-dithered bands. Each scene's ground row lines up with the mascot's bottom edge, so it peeks over it like it peeks over the taskbar.
    - The scenes: the cat's moonlit rooftops, the robot's station deck with a planet, the fox's dusk forest, the frog's pond at night, the ghost's misty hill with a big moon, and the axolotl's sea floor. The orb's background is plain dark with a soft theme glow.
    - Claude drew a first version, which was too busy. Codex drew the one in use from a brief (calm, minimal, small palettes, quiet behind the clock and text box).
    - Codex's sandbox couldn't run the project's Python, so it never saw its own renders; Claude rendered and checked them. Each scene takes 2-22 ms.
  - Settings live only in NOVA's window: `SettingsWindow` was removed. Tray > Settings (renamed from "Open NOVA settings…") opens the window with the drawer.
  - Mascot picker bug: a press on a `Tile` wasn't accepted, so it went up to the HUD, which closed the drawer and took the mouse grab, so the release never reached the tile. Fixed in two places: `Tile` accepts presses, and the drawer swallows presses that nothing inside it took. Tested with simulated clicks (frog, orb, axolotl): each was picked, the drawer stayed open, and clicking beside the drawer still closes it.
  - The notch hides whenever the window is open and not minimised (before, only while it was active, maximised or fullscreen).
  - Weather: `api.open-meteo.com` (51.81.167.44) couldn't be reached from the user's ISP at all (TCP timeouts, even on HTTP), while Quad9 returned 167.114.211.86, which worked.
    - Fix: `tools_server.forecast()` tries the main server for 5 s, then `previous-runs-api.open-meteo.com` and `historical-forecast-api.open-meteo.com`, which answer `/v1/forecast` with the same data. It remembers the one that worked and tries the main one again after 30 minutes.
    - Now about 7 s the first time, then about 0.9 s.
- **Mic indicator and pixel-accurate scenes (2026-11-14),** at the user's request ("add an indicator when it picks up my mic", "make them pixel accurate to the pixels of the mascots").
  - Scenes are drawn at the mascot's pixel size (they were half), on its grid (`HudWindow._grid`: grid origin ≤ 0 at the mascot's position mod its scale).
    - The ground row is exactly the mascot's bottom edge. When settings slide in, the mascot snaps to the grid as it moves.
    - The sleeping z's are a 4x4 glyph on the same grid. At 1280x800 a scene is about 119x75 art pixels; Codex's scenes hold up at that size.
  - Mic indicator, top right: a 7x9 pixel microphone (a Fluent glyph with the orb) and four signal bars.
    - It's on while `Mic.level` is above NOVA's own speech threshold, max(2.5 x noise, 300) / 4000, plus 0.8 s, and all the time it's LISTENING; never when muted. It shows even with the chrome hidden.
    - The bars scale from the threshold to 5x the threshold.
- **Tray:** "Open NOVA settings…" is now just "Settings" (it sits under "Open NOVA").
- **Eyes follow the cursor; Restart NOVA (2026-11-14),** at the user's request ("the eyes follow my cursor", "reload nova so i don't have to keep running the command upon new builds").
  - New looks per mascot:
    - Cat: the whole eye moves a pixel up or down in its black face (`gaze_*`).
    - Robot: the eye bars move about the screen; looking down, the mouth and blush move too.
    - Fox, frog, ghost, axolotl: the 4x3 eyes' pupils move down and diagonally down. Their pupils already sit at the top, so up equals level.
    - Frames are `look_up_left` ... `look_down_right` (`ui.GAZE_FRAMES`). A missing one falls back to the same column level, then idle.
  - `ui.gaze_toward()` picks the nearest of 8 directions (a 0.38 dead zone on each axis), or straight ahead when the cursor is within 0.6 of a mascot width.
    - `Sprite.gaze` overrides the random glances while IDLE, for `GAZE_S` = 5 s after the cursor last moved (`QCursor.pos()` polled each tick: the window and the notch). Hover still means "listen".
    - The orb's core leans up to 0.2 R towards the cursor (`orb.paint(look=)`).
  - Tray > Restart NOVA: `bridge.restart_requested` calls the same `restart()` as the settings' Restart button.
    - It now passes `--window normal|max|full` when NOVA's window is open, and the new process reopens it that way.
- **Left:** live checks of phases 2-4, then polish:
  - instant local commands ("pause", "volume up") without Claude, for speed.

### Phase 4 results (reminders and notes)
- **Storage:** `nova/memory.py`, in `data/reminders.json` and `data/notes.json` (gitignored).
  - Writes are atomic (temp file plus `os.replace`).
  - An unreadable file is renamed `*.unreadable-<time>.json`, not overwritten.
  - The tools can only reach those two files.
- **Reminders:**
  - `add_reminder(text, when, repeat)`: Claude gives a local ISO time, worked out from the clock in each message (now with seconds, so "in N minutes" is exact). Times must be in the future and at most a year ahead. Repeats are none, daily, weekdays or weekly, keeping the wall-clock time.
  - `list_reminders` and `cancel_reminder` (by number, words or "all").
  - A scheduler thread wakes at the next due time (at least every 60 s).
  - A due reminder rings through the same alert path as timers, with status "Reminder".
  - Reminders missed while NOVA was off are announced at startup ("You missed a reminder from …"); a repeating one jumps to its next future time.
- **Notes:**
  - `add_note(text, list)`, `list_notes`, `search_notes`, `delete_note` and `clear_list`.
  - Lists are named ("shopping list" is stored as "shopping").
  - Deleting is soft: the bin keeps notes for 30 days and empties itself on load.
  - Clearing every note at once isn't possible by voice, only per list.
- **Notch and tray:**
  - When NOVA is idle or muted, the hover card shows "Next: pasta timer 3:12 · call Mum, 5:00 PM".
  - NOVA settings has the Timers, Reminders and Notes pages: add, edit, delete, and the bin with restore. They refresh while open.
- **Tests** (temporary data folders, never the real `data/`):
  - **Unit tests:** missed reminders at start, firing, repeats (Friday 8:00 on weekdays goes to Monday), bad times, persistence, a corrupt file, the bin.
  - **Haiku:**
    - Requests handled: "in one minute", "at 9" (it picked 9 AM at 1 AM), a weekday repeat, list, cancel, a wifi-password note and recall, and a shopping list.
    - "Delete all my notes" was refused, with the per-list option explained.
  - **Simulated-room voice test:** set a reminder, add to and read back a list; the reminder rang after the conversation.

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
  - **Spotify (`play_spotify`, `spotify_now_playing`), 2026-11-14,** at the user's request ("run spotify and search for a song"):
    - Opening a `spotify:track:` URI only shows the track in Spotify 1.309; it doesn't play. Instead, the Spotify app ships an official CLI, `spotify_cli.exe` (an app alias in `%LOCALAPPDATA%\Microsoft\WindowsApps`), that searches, plays and reads now-playing through the logged-in desktop app: no API key, ~0.3-0.7 s per call.
    - NOVA runs only `status`, `open`, `me`, `search`, `library list`, `play <uri>` and `now-playing` (`SPOTIFY_COMMANDS`, checked on every call), with list arguments and no shell. The CLI's playlist, library, queue, device, Jam and upload commands are never used.
    - Claude never supplies a URI: it gives a query (cleaned: printable, one line, ≤ 100 characters, leading dashes removed so it can't be a flag) and a kind (track, album, artist, playlist, liked). NOVA plays the first result of Spotify's own search, or the user's own playlist when the name matches, after checking the URI's shape. Liked Songs is `spotify:user:<username>:collection`.
    - It works only while Spotify is allowed under Apps. If Spotify isn't running, NOVA starts it (`open`) and waits up to 25 s (not live-tested yet: the user's Spotify was open).
    - Tested: with `play` faked (searches and the library real), Claude picked the right kind for a song, an artist, "my focus playlist", liked songs and "what song is this?", 3-5 s to the first words. Hostile queries ("--help", shell characters) were only searched as text.
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
                                                          ├─ Spotify             search + play only
                                                          └─ reminders / notes   stored locally
                                                                     │
                                   notch UI  ◄─── status + text ─────┤
                                   speakers  ◄─── edge-tts ──────────┘
```

### Safety model
- Claude gets **only** WebSearch and the NOVA tools (weather, news, media keys, volume, timers, open allowed apps, Spotify search and play, reminders and notes in NOVA's own data folder). Never Bash, PowerShell or file tools. Anything that would prompt for permission is refused (`--permission-prompts none`).
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
