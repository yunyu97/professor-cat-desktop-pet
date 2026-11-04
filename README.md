# Professor Cat — desktop Pomodoro cat

A watercolor tabby that lives on your desktop, keeps your Pomodoro timer and plays
music from YouTube while you work.

There are two versions:

| Program | Music |
|---|---|
| **Professor Cat (Life)** — `ProfessorCatLife.py` | Comes with a default YouTube playlist. |
| **Professor Cat Study** — `ProfessorCatStudy.py` | No built-in music. Right-click the cat → **Add your study playlist…** and paste any YouTube playlist or video link. It is remembered. |

Everything else is identical.

## Download

See **Releases** for ready-to-run builds:

- **Windows:** unzip `ProfessorCatLife-windows.zip` or `ProfessorCatStudy-windows.zip` and run the `.exe` inside
  (keep the `_internal` folder next to it).
- **macOS:** `ProfessorCat*-mac-apple-silicon.zip` (M1–M4) or `...-mac-intel.zip`. Unzip, then
  right-click the app → **Open** the first time (the apps are not signed by Apple).

## What the cat does

- Walks, turns, sits, grooms, naps and wanders over to look at your mouse pointer.
  Hind legs are animated separately (near leg in front, darker far leg behind) with planted paws.
- Pick it up and it dangles; drop or throw it and it falls and lands.
- **Double-click** the cat to start/pause the timer. **Right-click** for presets, modes and music.
- **Music controls** on the badge: previous · play/pause · next · mute · volume − / +, with the song title.
- Music pauses on breaks and resumes for work.
- **Rest reminder:** after 2 hours of accumulated work the cat asks for a rest and a cat bed appears.
  Drag the cat into the bed to start a 15-minute sleep. Ignore it and the reminder disappears after
  30 seconds (it asks again after 15 more minutes of work).

## Run from source

```bash
pip install pyside6
python ProfessorCatLife.py      # or: python ProfessorCatStudy.py
```

## Build

- **Windows:** `pip install pyinstaller` then `pyinstaller ProfessorCatLife.spec` / `pyinstaller ProfessorCatStudy.spec`
  → `dist/ProfessorCatLife/ProfessorCatLife.exe`, `dist/ProfessorCatStudy/ProfessorCatStudy.exe`
- **macOS (on a Mac):** `bash build_mac.sh` → `dist/ProfessorCatLife.app`, `dist/ProfessorCatStudy.app`
- **macOS without a Mac:** Actions tab → **Build macOS apps** → **Run workflow**, then download the zips.

`prepare_life_sprites.py` regenerates `cat_sprites_life/` from `cat_sprites_real/` and the Gemini sprite sheet.
