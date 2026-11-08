# N.O.V.A. — Not Only a Voice Assistant

Say **"Hey Nova"** and ask anything. NOVA answers out loud using Claude (through your signed-in Claude Code CLI, no API key). It lives at the bottom centre of your screen (a pixel-art cat or robot peeking up from the edge, or a glowing orb notch) and in the tray.

## Run it

```powershell
.\start.ps1            # background, no window
.\start.ps1 -Console   # in this window with live logs (Ctrl+C to quit)
```

First time on a new machine: `powershell -ExecutionPolicy Bypass -File setup.ps1`

## Using it

**"Hey Nova" starts a conversation.** After that, just talk: no wake word between turns.

- **"Hey Nova, what's the capital of France?"** in one breath, or **"Hey Nova"**, wait for the chime (two soft rising notes), then ask.
- When NOVA finishes, the card shows **Your turn** with a bar that runs down. Reply any time in the next **10 seconds** ("and what about Germany?").
- **Talk over NOVA** to interrupt it. It briefly turns itself down to check it's really you (not its own echo), then stops and listens. Saying "wait" or "stop" is enough. On headphones NOVA notices there's no echo and switches the echo canceller off, because it would otherwise muffle your voice while NOVA talks (the log says "Echo canceller bypassed").
- **The conversation ends** after 10 seconds of silence, when you say **"that's all"**, **"goodbye"**, **"stop listening"** or **"never mind"**, or when you click the notch while it's listening.
- **Click the notch** while NOVA is talking to stop it (the conversation continues).
- **Your words appear on the notch as you speak.** The live caption is a rough guess (offline Vosk); when you stop, Whisper's more accurate text replaces it, and that's what NOVA answers.
- **"New conversation"** / **"start over"** makes Claude forget what you talked about. It also forgets by itself after 5 minutes of not being used.

## The notch

Pick its look from the tray: right-click > **Mascot** (remembered):

- **Black cat** and **Robot** (pixel art): peek up from the bottom edge of the screen, in the centre (just ears and eyes, or antennae and the top of the robot's screen). They blink and glance around while they wait. Say "Hey Nova" (or hover) and they pop up with a card above them: ears perk up / bigger eyes while you talk (and a hop with your voice), looking up while thinking, a moving mouth with NOVA's voice, asleep with small drifting z's while muted (the robot's screen dims), sad when something goes wrong.
- **Orb**: the classic notch, a thin glowing pill that opens into a panel with an animated wireframe orb beside the text. The orb wobbles with your voice and NOVA's, spins faster while thinking, turns grey when muted and red on an error.

The card:

- **Status** (Listening / Thinking / Your turn) with a small level meter. A thin bar along the bottom moves while NOVA thinks, and runs down while it waits for your reply.
- **Text**: your words live while you talk; then your question in small print above NOVA's answer, which grows with the reply.
- **See-through**: Windows blurs what's behind it (acrylic, like Start and the flyouts). It follows light/dark mode and your Windows accent colour. On dark themes the black cat gets a faint outline so it doesn't disappear.
- Left-click: stop NOVA talking (or end the conversation if it's listening). Right-click: the tray menu.

The pixel mascots are drawn as text in `nova\pixelcat.py` and `nova\pixelbot.py` (one character per pixel), so they're easy to edit; the orb is `nova\orb.py`. Preview every state without talking: `.venv\Scripts\python.exe tools\notch_preview.py` (writes `samples\notch_preview_cat.png` / `_robot` / `_orb` and `samples\mascot_frames.png`).

## Tray icon

- The icon is your mascot (the cat's or robot's head with a white outline so it stands out on the taskbar, or the orb); grey and asleep when muted.
- Left click: mute or unmute the wake word. **Muting also frees NOVA's video memory** (~1.7 GB down to ~0.1 GB; the last 0.1 GB stays until you quit). RAM is freed too (NOVA uses ~0.2-0.3 GB while running). After unmuting NOVA is ready in ~1-3 s, using its smaller speech model for the ~9 s it takes the accurate one to reload.
- **Show notch** (right-click menu) hides or shows the notch; your choice is remembered. NOVA still listens and chimes with it hidden.
- Right click: Mute wake word, New conversation, Show notch, **Mascot**, **Voice**, Quit NOVA.

### Voices

Right-click the tray icon > **Voice** to switch voices any time. NOVA says "Hi, I'm Nova" in the new voice, and remembers your choice (in `state.json`).

- **Kokoro voices** (Bella, Heart, Sarah, Nicole, Emma, Michael, Fenrir, George) run on your PC: no internet needed for speech. On the GPU (RTX 4050) a sentence takes ~0.4-0.8 s to make; on the CPU it's 2-5x slower.
- **Microsoft voices** (Ava, Andrew) are online: usually fast, occasionally slow when the service is busy.
- If a voice fails, NOVA falls back to the other kind, then to the built-in Windows voice.
- Add more voices to the menu in `config.toml` (`[voice] menu`).

## Settings

Edit `config.toml`, then restart NOVA. Main ones:

| Setting | What it does |
|---|---|
| `[wake] wait_for_command` | Seconds to wait for your question after "Hey Nova" + pause (default 6) |
| `[wake] end_silence` | Seconds of quiet that end your question (default 1.0) |
| `[conversation] enabled` | Keep listening after each answer (default true) |
| `[conversation] follow_up_timeout` | Seconds of silence before the conversation ends (default 10) |
| `[conversation] talk_over` | Talk over NOVA to interrupt it (default true; uses echo cancellation) |
| `[mic] device` | Part of your mic's name; empty = Windows default |
| `[speech_to_text] model` | `large-v3-turbo` (default, most accurate, needs the GPU), `small.en` or `base.en` (faster, less accurate). Compare them on your own voice: `.venv\Scripts\python.exe tools\stt_compare.py` |
| `[speech_to_text] vocabulary` | Names and words it should expect (keep "Claude" in here) |
| `[speech_to_text] device` | `cuda` (GPU, default) or `cpu`. Falls back to CPU if CUDA fails. Kokoro uses the GPU automatically when it can |
| `[claude] model` | `haiku` (fastest) or `sonnet` |
| `[voice] name` | Starting voice, e.g. `kokoro:af_bella` or `edge:en-US-AvaMultilingualNeural` (the tray choice overrides it) |
| `[voice] kokoro_speed` | Speaking speed for Kokoro voices (default 1.05) |
| `[voice.pronunciations]` | How to say a word, spelled the way it sounds (e.g. `Magdaong = "Mugduh-ong"`). Only the voice changes; the text keeps the real spelling |
| `[ui] mascot` | Starting look: `cat`, `robot` or `orb` (the tray's Mascot menu overrides it) |
| `[ui] accent` | Colour for the notch's bars and caret; empty = your Windows accent colour |
| `[ui] notch` | Show the notch (default true; the tray's Show notch overrides it) |
| `[ui] live_captions` | Show your words on the notch as you speak (default true) |

## Start with Windows

```powershell
powershell -ExecutionPolicy Bypass -File install-startup.ps1           # add
powershell -ExecutionPolicy Bypass -File install-startup.ps1 -Remove   # remove
```

## Safety

Claude runs with **no tools**: it can only talk. A misheard sentence can't run commands or touch files.

## Troubleshooting

- Logs: `logs\nova.log`
- Test the wake word on its own: `.venv\Scripts\python.exe tools\mic_test.py`
- List microphones: `.venv\Scripts\python.exe tools\mic_test.py --list`
- GPU: the log should say `kokoro ready on CUDAExecutionProvider` and `whisper large-v3-turbo on CUDA`. If it says CPU, re-run `setup.ps1` (it installs `requirements-gpu.txt`: onnxruntime-gpu 1.26 + NVIDIA's CUDA 12 libraries, ~2.4 GB in `.venv`). Without the GPU, NOVA uses `base.en` for speech-to-text (turbo is too slow on a CPU).
- Misheard words: run `.venv\Scripts\python.exe tools\stt_compare.py` (quit NOVA first). You read 15 phrases and it shows how each speech-to-text model did on your voice.
