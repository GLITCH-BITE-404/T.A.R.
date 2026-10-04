# Changelog

All notable changes to T.A.R. are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-10-04

First tagged release.

### Added
- **Cloud brain**: Google Gemini (free tier, no SDK needed) alongside Anthropic
  Claude, with real tool calling; the provider follows the model id.
- **Console** with LOCAL / CLOUD tabs: cloud on/off, key entry, cloud model list.
- **77 actions**, including reminders/timers, wifi, bluetooth, battery, weather,
  date/time, calculator and unit conversion, file management (list, find, move,
  copy, rename, mkdir, trash, restore, download), force-quit, process list,
  power (suspend/lock/logout/reboot/shutdown), do-not-disturb, night light,
  colour picker, screen recording, clipboard history, YouTube playback,
  coin flip and dice.
- **Guarded shell action** for the cloud brain (no root, no `rm`, no writes to
  system paths, no disk tools or bootloader).
- **Window ownership**: T.A.R. remembers windows it opened, permanently;
  "close it" / "close them" only touch those. "close everything" closes all
  windows except T.A.R.
- **Effects layer** over both views: glitch, scanline, shockwave, alert, flash,
  shake, heartbeat, matrix takeover, party mode, hacker cascade, barrel roll,
  fake self-destruct, banner text.
- **Easter eggs** that run instantly without a model.
- **Promptable persona** (`/persona`) on top of a natural default voice.
- **New chat** from anywhere: `/new`, "new chat", or `tar-ai new`.
- `tar-ai` launcher (focuses instead of opening a second window) and `install.sh`.
- Longer memory in cloud mode: 40 turns of chat plus every action this session.

### Fixed
- Window actions targeted T.A.R. itself (it always has focus while you type),
  so "close it" closed T.A.R.
- "open a terminal and run btop" opened two windows; "run btop inside it" ran
  `btop inside it`.
- "open kitty and then close it" web-searched "close it".
- Effects only animated the hidden chat-view orb, and "glitch" did nothing.
- The chat didn't stay scrolled to the newest message (and later overshot into
  blank space).
- Cloud mode silently fell back to the local model when no key was set, and
  showed bogus "model not on disk" warnings.
- "resume" skipped to the next song.

[0.1.0]: https://github.com/GLITCH-BITE-404/T.A.R./releases/tag/v0.1.0
