# RT-950 Pro CHIRP Driver Workspace

This repository tracks reverse-engineering notes, tooling, and implementation work for adding Radtel RT-950 Pro clone-mode support to [CHIRP](https://chirp.danplanet.com/).

## Repository Layout
- `DESIGN.md` - fixed definition of done, protocol notes, and memory map.
- `IMPLEMENTATION.md` - living status log and task board tied to the design.
- `AGENTS.md` - primary contacts and collaboration expectations.
- `Reference/` - vendor CPS decompilation and clean-room export utility from prior work.

## Development Environment
1. Create and activate a Python 3.11 virtual environment:
   ```powershell
   c:\Python\python_3.11\python.exe -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install --upgrade pip
   ```
2. Install dependencies as they are added: `pip install -r requirements.txt`.
3. Keep tooling Python-only so tests can run without a CHIRP install; CHIRP integration will reference the same modules.

## Planned Tooling
- Standalone harness to decode/encode RT-950 Pro `.dat` images.
- CSV translator compatible with `Reference/950Pro Export` artifacts for regression checks.
- Optional serial transport wrapper for talking to a physical radio when connected.

## Harness Commands
- `python -m rt950pro image clone-read --port COM5 [--output dump.bin] [--baud 115200]` - capture a clone dump over the serial link (writes a decrypted buffer).
- `python -m rt950pro image clone-write --port COM5 --input rt950pro_clone.bin` - push a prepared clone image back to the radio.
- `python -m rt950pro image summary <image.dat> [--limit N]` - summarise populated channels in a raw clone image.
- `python -m rt950pro image dat-summary <codeplug.dat> [--limit N] [--assembly path]` - summarise channels from a CPS `.dat` file (requires `pythonnet` and the vendor assembly).
- `python -m rt950pro regression dat-csv <codeplug.dat> <channels.csv> [--assembly path] [--limit N]` - compare CPS `.dat` channel data with the CSV export.
- `python -m rt950pro channel decode --hex <64-hex-chars>` - decode a single 32-byte channel blob into JSON.
- `python -m rt950pro channel encode ...` - placeholder; emits a `MOCK:` warning until implemented in the CLI, though the underlying `RadioImage` encoder now supports round trips.

## Testing
- Automated: `python -m pytest` — validates byte-stable round trips for `rt950pro_clone.bin` and ensures mutated configuration data survives encode/decode cycles.
- Manual smoke tests (use your own CPS export files for the last two examples):
  - `python -m rt950pro channel decode --hex <64 hex chars>`
  - `python -m rt950pro image clone-read --port COM5 --output rt950pro_clone.bin`
  - `python -m rt950pro image clone-write --port COM5 --input rt950pro_clone.bin`
  - `python -m rt950pro image summary rt950pro_clone.bin --limit 5`
  - `python -m rt950pro image dat-summary "Reference/950Pro Export/Radio.dat" --limit 5 --assembly "C:\Program Files (x86)\RT-950PRO_CPS\BT-RT950PRO_CPS.exe"`
  - `python -m rt950pro regression dat-csv "Reference/950Pro Export/Radio.dat" "Reference/950Pro Export/channels.csv" --assembly "C:\Program Files (x86)\RT-950PRO_CPS\BT-RT950PRO_CPS.exe"`

## Testing Philosophy
- Prefer end-to-end binary comparisons over mocks; when mocking is unavoidable, log a warning so the shim can be revisited.
- Validate against known-good `.dat` files before exercising a physical radio.
- Document every new test command in `IMPLEMENTATION.md` to keep alignment with the design.

## Contributing
- Review `DESIGN.md` for scope; propose design changes before editing that file.
- Update `IMPLEMENTATION.md` with progress notes and open questions as work advances.
- Coordinate on-radio experiments with Nathan (_2E0NBS_).
