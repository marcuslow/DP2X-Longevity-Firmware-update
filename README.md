# DP2X Longevity Firmware update

> [!WARNING]
> **READ THIS BEFORE USING. NO WARRANTY. USE ENTIRELY AT YOUR OWN RISK.**
>
> This is unofficial, modified firmware. It is **not made, endorsed, supported or
> approved by SIGMA Corporation**. Flashing it can permanently damage your camera
> ("brick" it), and may void your warranty and any right to manufacturer service.
>
> **The authors and contributors are NOT responsible for any damage to your camera,
> lens, memory card, data or anything else, or for any loss arising from its use.
> This includes a bricked camera, lens or mechanism failure, and lost images.**
> By downloading, building or flashing this firmware, you accept full responsibility
> for the result.
>
> **Always follow SIGMA's official firmware update instructions and procedures for
> the DP2x** (see SIGMA's DP2x firmware download and support page). In particular:
> - use a fully charged battery;
> - use an SD card formatted in the camera;
> - never turn the camera off, open the battery or card door, or remove the card
>   while an update is running.
>
> The patch was tested on **one** camera only, with firmware 1.02. It is provided
> "AS IS", WITHOUT WARRANTY OF ANY KIND, express or implied, including fitness for a
> particular purpose. If you are not comfortable with the risk, don't use it.

A patch for **Sigma DP2x firmware 1.02**. It stops the lens barrel retracting
at every shutdown, and stops the retract-then-extend at start-up when the
barrel is already out. This cuts needless wear on the barrel mechanism.
Autofocus is unchanged.

| | Stock 1.02 | Patched |
|---|---|---|
| Power off | Barrel retracts | Barrel stays out, shutdown is faster |
| Power on, barrel retracted | Extends (warns if the cap is on) | Same |
| Power on, barrel out | Retracts fully, then extends again | No barrel movement |
| Focus | Recalibrates at start-up | Same (a short motor noise is normal) |
| Playback idle / before a firmware update | Retracts | Same (kept as a recovery path) |

**Status:** tested and working on one camera (2026-09-26). See `NOTES.md` §7.

## Install
Follow **SIGMA's official DP2x firmware update procedure**. Only the file is different:
1. Fully charge the battery. Format the SD card in the camera.
2. Copy `build/DP2X102.BIN` to the root of the SD card. Check its SHA-256 first
   (`shasum -a 256 DP2X102.BIN`):
   `1c3a2fb566639d7ac27487e0628a3fc1bccb6eaa1d24f1b71d2ff62c9e5f6caf`
3. Run the firmware update as SIGMA's instructions describe. Don't power off or
   open any door while it runs.

Or build it yourself from Sigma's stock `dp2x102.bin`. The script checks the input file and fixes the checksum:
```
python3 tools/patch_lens.py dp2x102.bin DP2X102.BIN
```

**Revert:** flash Sigma's stock 1.02 firmware (`DP2X102.BIN` from Sigma).

## Caveats
- The camera is left with the barrel out: protect it, and don't push on it.
  The front cap may not fit.
- If power is lost *while* the barrel is moving, it may stop part-way.
  To recover, stay in playback mode until the lens auto-retracts, then switch to shooting.
- See the warning at the top: there's no warranty, and we're not responsible for any damage or a bricked camera.
- This project is not affiliated with SIGMA Corporation. The DP2x firmware is SIGMA's copyright;
  "SIGMA" and "DP2x" are SIGMA's trademarks and are used here only to identify the camera.

## Details
`NOTES.md` covers the reverse-engineering: file layout, checksum, CPU (Fujitsu FR,
big-endian), the lens control flow, and each patched instruction. `tools/` contains
the patch script and the disassembly helpers (`tools/disasm.sh` needs GNU binutils).
