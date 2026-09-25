# Sigma DP2x firmware 1.02 (`dp2x102.bin`): lens barrel notes

> **Disclaimer:** unofficial, unsupported modification. Use at your own risk; we are not responsible for any damage or a bricked camera. Always follow SIGMA's official firmware update instructions. See `README.md`.

Goal: stop the barrel from retracting on power-off, and on power-on extend it only
when it isn't already out. Autofocus (the internal focus stepper) is left alone.

## 1. File layout

| File offset | Content |
|---|---|
| `0x0000–0x007f` | Header: `SIGMA.CO0000DP2X`, versions `1.02.0.0001`, date `20120116`. **`0x4c` = main checksum (u32 BE)** |
| `0x0080–0x407f` | Sub-micom firmware slot (all zero in this file, so no sub-MCU update) |
| `0x4080–0x80407f` | 8 MiB main flash image, **loaded at address `0x00040000`** (`addr = fileoff - 0x4080 + 0x40000`) |

Inside the flash image:
- `0x040000…` holds CamSys / Foveon calibration XML-bin blocks.
- ~`0x1c0000`–`0x3b3000` is code.
- `0x3b3000…` holds strings and UI text in every language. For example, the lens error text is `"Remove the front cap, / and cycle the power."`.

**Checksum.** The value at `0x4c` is the 32-bit sum of every byte from file offset `0x80` to EOF. The camera's updater (`0x2794d2`, "Confirm main firm checksum") computes the same byte sum over the 8 MiB image after loading the file to SDRAM `0x87000000`. The sub-micom check (`0x279466`) sums `0x80–0x407f`, which is 0 here. The patch script recomputes `0x4c`. No other checksum was found.

**Updater file name.** The camera looks for `DP2X<ver>.BIN` or `DP2X<ver>D.BIN` in the SD root (`0x278c04…`). It accepts a file whose version is ≥ the installed one, so `DP2X102.BIN` flashes over 1.02.

## 2. CPU and tools

- Fujitsu FR family (FR60/FR80-class), **big-endian**, 16-bit instructions. The RTOS is Softune REALOS/FR; system calls are made with `ldi:8 #fn,r12 ; extsb r12 ; int #0x40`, e.g. `0xab` = delay ms, `0xd0/0xd1/0xd2` = event flag set/clear/wait, `0xe9` = send to data queue.
- GNU binutils (`brew install binutils`) disassembles it with `objdump -b binary -m fr30 -EB --adjust-vma=0x40000`. The Ghidra FR60 module works as well.
- `tools/disasm.sh` rebuilds `work/dis_ann.txt` (disassembly with string references annotated) and a call graph. `tools/lin.py START END` prints a linear listing with callers, and `tools/who.py ADDR` lists callers.
- **Compiler quirk.** Calls are often `call:d target+2` with the target's first instruction (usually `st rp,@-r15`) in the delay slot, which is a call to `target`. `cg2.py` resolves this, but keep it in mind when reading.

## 3. Lens hardware as the firmware sees it

| Thing | Meaning |
|---|---|
| I/O byte `0x5a6` bit0 | **Barrel home sensor**: 1 = fully retracted |
| I/O byte `0x5a6` bit1 | Focus home sensor ("FocusHpDetect") |
| `0x38a0dc(dir, spd)` | Barrel DC-motor drive: sends `0x5001\|spd<<3\|dir<<1` to device 5 via `0x2525d4`. dir 1 = extend, 0 = retract |
| `0x38a100(dir)` | Barrel motor stop/brake (`0x5000\|dir<<1`) |
| Timer32 ch1 (`0x2b4xxx` Dd_Timer32 driver) | Barrel encoder pulse counter |
| `0x38a144`, `0x38a790`, `0x38a7bc`, `0x38a6d8` | Focus stepper: move ±n steps; home search, then position = `0x16c` |
| RAM `0x6a0189b8` | Software "lens is open" flag |
| RAM `0x6a019a78` / `0x6a019a74` | Focus position / focus initialised (=2) |

The barrel position is known only relative to the home sensor. There is no "fully extended" sensor. Extending means driving from home until the encoder counts 60 pulses.

## 4. Lens control flow (stock)

**Low-level barrel ops**
```
0x38a274  barrel_extend(): drive dir1 speed 0x16; after 20 pulses switch to 0x18;
          success (return 0) at >=60 pulses; 200 x 10 ms (2 s) timeout -> return 1  (= blocked / cap on)
0x38a354  barrel_retract(): drive dir0 speed 0x18 until 0x5a6.bit0==1 (max 2 s), then brake
0x38a414  barrel_ctrl(mode):
   mode 0 (power-on):   if !home { focus_park(5); barrel_retract(); }     <-- re-home
                        if barrel_extend()==0 brake(1), ret 0 else { barrel_retract(); ret 1 }
   mode 1:              if home { power up drivers, focus_park, retract (no-op) }
   mode 2 (close):      focus_park2(5); barrel_retract()
```
**API**
```
0x38a380  lens_hw_open():  timer init, power up drivers (0x252xxx), battery check 0x270f94
                           ("LENS_START Battery"): fail -> return -1
                           barrel_ctrl(0) != 0 -> return -2   (blocked -> "Remove the front cap")
                           focus init to 0x16c (0x38a494); return 0
0x38a3c4  lens_hw_close(): barrel_ctrl(2); power down drivers (0x38a0b4)
0x38a3e4  lens_is_home():  returns 0x5a6.bit0
```
**App layer** (lens task, data queue `0x18`, flag `0x22`)
```
0x238868  lens_open():   if flag set -> return. r = lens_hw_open()
                         r==0  -> flag = 1
                         r==-1 -> low battery -> power off (0x221518)
                         else  -> lens error screen, wait 2 s, power off
0x2389f0  lens_close():  if !lens_is_home() lens_hw_close();  flag = 0
0x2387f0  lens task msg handler: 0 = open, 1 = barrel_ctrl(1), 2 = close
0x2387a0  "System_Factor_Lens_Closer": called from power-off (0x221518).
          If already in the lens task, call lens_close() directly; otherwise send msg 2.
          Msg 2 is sent ONLY from here.
```
**All callers of `lens_close()` (`0x2389f0`)**

| Call site | Path |
|---|---|
| `0x2387c0` | Power-off, direct |
| `0x23880a` | Power-off, through lens task msg 2 |
| `0x238680` | Playback-mode idle timer (`0x238600`): counter `0x6a0189bc` reaches 1000 in play modes 8/9, then the lens retracts |
| `0x2793dc` | Firmware-update / flash-writer path, before flashing |

**Power-on path.** Mode change to shooting (`0x2385e2`), or startup (`0x220f18`), calls `lens_open()`.

## 5. Patch (`tools/patch_lens.py`)

| # | Addr | File off | Orig → New | Effect |
|---|---|---|---|---|
| 1 | `0x38a440` | `0x34e4c0` | `d8 7f` → `e0 07` (`bra 0x38a450`) | barrel_ctrl(0): if the barrel is **not** at home, skip park + retract + extend and jump to the "extend OK" tail (brake(1), return 0) |
| 2 | `0x2387c0` | `0x1fc840` | `d1 17` → `9f a0` (nop) | Power-off, direct: don't call lens_close() |
| 3 | `0x23880a` | `0x1fc88a` | `d0 f2` → `9f a0` (nop) | Power-off, lens-task msg 2: don't call lens_close() |
| opt | `0x38a47c` | `0x34e4fc` | `d7 6b` → `9f a0` (nop) | `--also-playback`: barrel_ctrl(2) never retracts (playback idle and pre-update closes too) |

Checksum at `0x4c`: `0x149b651e` → `0x149b6482` (default set).

Resulting behaviour:
- **Power on, barrel retracted** (first boot, or after the playback idle retract): stock behaviour. It extends, and the cap-blocked detection and warning still work.
- **Power on, barrel already out:** no motor movement. Focus still re-homes as usual, which is internal and independent of the barrel.
- **Power off:** barrel stays out. The focus is not parked either; that's harmless because it re-homes at every start.
- **Low battery at power on:** unchanged (the camera powers off before touching the lens).

Build: `python3 tools/patch_lens.py dp2x102.bin build/DP2X102.BIN`, then copy `DP2X102.BIN` to the SD card root and run the normal firmware update.

## 6. Risks and caveats (read before flashing)

- **Not tested on hardware.** A bad flash can brick the camera. Have the stock `dp2x102.bin` ready; flashing it restores the stock lens behaviour.
- **Partial extension.** "Not at home" is taken to mean "fully extended". If power is lost *while* the barrel is moving (battery pulled during start-up), it could stop part-way and the camera would not correct it. **Recovery (default patch set only):** stay in playback mode until the stock idle auto-retract fires (timer `0x238600`, 1000 ticks). Switching back to shooting then re-homes and extends normally. `--also-playback` removes that recovery; flashing the stock firmware is then the fallback.
- The camera is stored with the barrel out, so it is more exposed to knocks, and the stock front cap may not fit over the extended barrel. Pushing on the extended barrel loads the gear train.
- The firmware-update close (`0x2793dc`) still retracts unless `--also-playback` is used. That's intended, so the barrel is in a known state after flashing.

## 7. Field test log

**2026-09-26: `build/DP2X102.BIN` (default patch set) flashed on the camera. Result: SUCCESS.**
SHA-256 `1c3a2fb566639d7ac27487e0628a3fc1bccb6eaa1d24f1b71d2ff62c9e5f6caf`.

- The update flashed and the camera boots normally.
- First power-on after flashing: the barrel extended normally from retracted.
- Power-off: the barrel no longer retracts, and shutdown is noticeably faster.
- Later power-ons: the barrel stays at the same extended position and doesn't move.
- A short lens noise is still heard at start-up. This is expected: it's the internal focus stepper re-homing. Every start-up runs focus init (`0x38a494` → `0x38a540(0x16c)`, home search via `0x38a6d8`), and the patch leaves that alone. The barrel itself doesn't move.

Not yet checked:
- Cap-blocked warning after a playback idle retract.
- How long the playback idle retract takes (the recovery path).
- Behaviour after power loss mid-extension.

## 8. Useful string anchors

| Addr | String |
|---|---|
| `0x3bae18` | `System_Factor_Lens_Closer!!!` (used at `0x2387c2`) |
| `0x3b6254` | `power off start` (power-off routine `0x221518`) |
| `0x402c40` | `LENS_START Battery_%d : %d` (battery check `0x270f94`) |
| `0x41a3c8` | `Confirm main firm checksum` (`0x2794d2`) |
| `0x41a8fc` | `current firmware verion` (update file search `0x278c04`) |

## 9. Exploration notes (branch `marcusonly`, 2026-09-26)

Static analysis only. Nothing below is patched or tested unless stated.

### 9.1 Shutter count (parked)
- Live counter: RAM `0x80101ff4` (u32). `0x214a10` increments it after each still is saved ("Still Capture file save end").
- Persistent copy: last word of the settings sector at flash `0x800000` (magic `"C72A"`, `0x30c` bytes, so the count is at `0x800308`). `0x212028` copies it to RAM `0x80101cec` at boot; power-off (`0x221518` -> `0x214460`) writes it back. The updater reloads the camera's existing sector during flashing ("Current file load ... Camfile overwrite"), so the count should survive a firmware update. Not verified on the camera.
- Stock firmware only prints it on the debug UART (`0x27e810`, "Total Shutter :%d"). Service USB commands `0x38`/`0x3b` set/get it through the USB buffer `0x80112053`.
- Proposed display: the setup-menu version line at `0x2c721e` does `memcpy(buf, 0x808000 /*serial*/, 4)` then `sprintf("Ver.%01d.%02d [SN:%08d]")`. Changing the source address at `0x2c7224` to `0x80101ff4` and relabelling the format strings `0x45c7fc`/`0x45c7d8` shows the count in place of the serial number. The text buffer is 32 bytes, too small to show both.

### 9.2 Focus homing at start-up
- The focus stepper is open-loop; its only reference is the home switch (`0x5a6` bit1). Position (`0x6a019a78`) and the initialised flag (`0x6a019a74`) are RAM, so they're lost at power-off, and every boot re-homes via `0x38a494` -> `0x38a540` -> `0x38a6d8`:
  - if on the switch, back off 20 steps; otherwise approach home by up to 364 (`0x16c`) steps;
  - creep 1 step per 1 ms until the switch triggers;
  - position = 364.
- Stock power-off also homes the focus (`barrel_ctrl(2)` -> `0x38a570`) so the barrel can retract with the focus group in a known spot. Our lens patch skips that, so boot usually takes the longer approach path. Harmless, and drift is corrected at every boot.

### 9.3 Contrast AF engine
- Half-press handler `0x238ad6`: "AF start" at `0x23900c`, "AF done" at `0x23912c`. Result flag `0x6a0189b4` (1 = focus OK, green box).
- Start `0x28425a`: the window is set by `0x38adb4` (limits `0x6a019a84`/`0x6a019a86`/`0x6a019a88`; the far end depends on `0x30837c`, probably macro). If the scene is bright (`0x80150fb0` >= `0x50000`) and the lens is inside the window, scan from the current position; otherwise jump to the nearer end (`0x284182`). Direction is chosen in `0x2841de`.
- Per frame: `0x38afb0` polls the AF block (`0x40000006` bit `0x20`, 1 ms sleeps), reads three contrast values (`0x40000000` + `0xb4`/`0x88`/`0x9c`), and stores them (`0x2843a2`). The history lives at `0x80151474` and the frame counter at `0x80150fb8` (limits 5/10/100).
- Coarse step is a fixed +/-4 motor steps: `0x38aee0` (`0xfc` at `0x38aef2`, `0x04` at `0x38aefe`). Peak detection uses a +/-4 hysteresis around the peak (`0x8015153e`).
- Refine `0x283e2c`: move to peak +/-16 (clamped to `0x8015153c`) and re-scan through the peak from one side (backlash-consistent).
- Sensor mode for AF (`0x244784`): AFE clock mode 4 = **40 MHz, already the fast clock** (`0x245934`: modes 1-2 = 20 MHz, 3-6 = 40 MHz), so there's no clock-speed gain available. The per-brightness timing table at `0x3be3a4` (5 x 12-byte entries, index `0x6a018da6` chosen by brightness thresholds `0x90000`..`0x50000`) sets sensor regs `0x66`/`0x67` and a line/frame-length value (`0xf02` bright -> `0x15c2` dark). So AF frames get slower in dim light.

### 9.4 Magnify during half-press (stock, trigger unknown; parked for camera test)
- While half-press is held after AF (loop `0x239178`, normal AF mode only), key bit `0x1000000` turns MF-spot magnification on (`0x2397f8` -> `Iif_Mf_Spot_On`, flag `0x6a0189c0`=1). Key bit `0x4000000` turns it off. Releasing S1 or shooting always turns it off (`0x2392d8`).
- The key word is `0x6a018238`, built from GPIO `0x40`/`0x42` plus sub-MCU data, so the physical button is unknown. Test by holding half-press after the green box and trying each control, starting with the MF wheel.
- Idea: auto-enable on entry when `0x6a0189b4`==1 (hook the entry branch at `0x23916a`). Needs a code cave; free space not yet confirmed.

### 9.5 Free flash space for injected code
- Empty-looking regions exist, but none is proven unused:
  - `0x3f2d7e`-`0x3f754a` (~18 KiB of zeros, no code refs) sits inside a sparse data blob whose density tapers off from `0x3f0800`. It's probably the blank middle of a table or bitmap, so the zeros may be meaningful.
  - `0x5b6000`-`0x800000` holds addresses the code uses (`0x700010`, `0x7d0000`, ...), possibly runtime storage.
- **Chosen cave: factory AFE-gain tuning routine `0x27eb4e`-`0x27f405` (2232 bytes).** All references into it are its own internal jump targets, plus the call sites `0x27f4ca`/`0x27f5c6` inside `0x27f6b2` ("AFE Gain Adjustment Start"). The only entry chain is the service USB command dispatcher (`0x24e054` -> `0x252224` -> `0x27df8c` -> `0x27cc86` -> `0x27f6b2`). No data pointers into it anywhere in the image.
  - To use it: also neutralise the entry (make `0x27f6b2` return early), so a service tool can't jump into the new code.
  - Cost: Sigma's factory AFE gain adjustment over USB won't work with this firmware. Flashing stock restores it.

### 9.6 AF refine pass, in detail
- State machine `0x284114` on `0x80150fac`: 1 = coarse scan (`0x283842`), 2 = refine (`0x283776`), 3 = `0x283822`. Context struct at `0x80150fa4` (0x5a4 bytes):
  - +0 coarse phase, +4 refine result (`fa8`), +0x14 frame count n, +0x18 coarse best idx, +0x1c refine best idx
  - per-frame arrays: `0x801512e4` A, `0x80151154` B, `0x80150fc4` C (u32 contrast), `0x80151474` -position (u16), up to 100 frames
  - +0x598 coarse peak raw, +0x59a coarse peak, +0x59c/+0x59e refine peak raw/peak, +0x5a0 flag
- Per frame, `0x2d928e` calls the AF library `0x30c21e` (plus the per-window `0x307da6`, up to 10 windows). It returns peak found / best index / an **interpolated** peak position; `0x30c8c4` does a linear interpolation via divide `0x373e9e`. So stock already interpolates between samples.
- `0x283f6a`: peak = raw - (pos[best] - pos[best-1]), a one-frame latency correction.
- Coarse decision `0x283afc`: no contrast if n>=10 and B[best] < 30000. Peak passed when B[n-1] < B[best] - B[best]/8 and C also lower (-> phase 5), or when 2+ frames have passed since best (-> phase 4).
- Refine setup `0x283e2c`: target = peak -/+ 16 (`add2 -16` at `0x283e66`, `ldi:8 0x10` at `0x283eb0`), clamped; move there (`0x38ae4c`); clear history (`0x283714`); set direction.
- Refine loop `0x283776`: steps +/-4 per frame; always at least 5 frames (`cmp 5` at `0x2837ac`), then stops when `fa8`==1 or at n>10 / window edge / 100. Agreement check `0x283a82`: success if |coarse peak - refine peak| <= 4 steps.
- So a refine costs one ~20-step move plus 5-10 frames. It re-scans from the opposite side (backlash) and **cross-checks the two peak estimates**. It isn't a finer-step scan: both passes use 4 steps.

### 9.7 Test build `build/DP2X102_test.BIN` (not yet flashed)
`python3 tools/patch_lens.py dp2x102.bin build/DP2X102_test.BIN --af-refine 8 --shutter-count`
SHA-256 `a6349399f322bb62c09703c22ba1ef09ee87900e1c56c205736943cc103e59e8`, checksum `0x149b66c4`.
- Lens patch (default set), plus:
- AF refine restarts 8 steps past the peak instead of 16 (`0x283e66` `a500`->`a580`, `0x283eb0` `c101`->`c081`). If the clamp to `0x8015153c` in `0x283e2c` always overrides the offset, this has no effect.
- Version line shows `Shots:%d` from `0x80101ff4` instead of `[SN:%08d]`.
- To test: AF time and accuracy against the flashed build, on close-up f/2.8, distant, and dim targets; and check the version screen shows a plausible count.
- To roll back: flash `build/DP2X102.BIN` (lens patch only) or stock `dp2x102.bin`, renamed to `DP2X102.BIN`.
- **2026-09-26: `DP2X102_test.BIN` flashed. Shutter count works:** the version screen shows `Shots:<n>`. AF refine-8 test in progress.

## 10. Resume here (session ended 2026-09-26)

**On the camera now:** `build/DP2X102_test.BIN` (lens patch + `--af-refine 8` + `--shutter-count`). Rollback: `build/DP2X102.BIN` (lens patch only) or stock `dp2x102.bin`, renamed `DP2X102.BIN` on the card root.

**Status**
- Lens patch: done, confirmed (section 7).
- Shutter count on the version screen: done, confirmed (9.1, 9.7).
- AF refine 16 -> 8: **testing in progress.** The user is comparing AF speed and sharpness (close-up f/2.8, distant, dim; about 5 tries each; slow-motion video for timing).

**Next steps, depending on the AF result**
1. Faster and still sharp: consider option C, skipping the refine pass (go straight to the interpolated coarse peak with a same-side approach). Needs injected code in the factory AFE-gain cave `0x27eb4e`-`0x27f405` (9.5), plus neutralising `0x27f6b2`.
2. No difference: the clamp to `0x8015153c` in `0x283e2c` probably overrides the offset. Work out what the evaluator stores in `0x8015153c` (from `0x307da6`'s per-window output, local `r14-64` in `0x2d928e`).
3. More misses or softer focus: revert to 16 (build without `--af-refine`).

**Parked**
- Magnify during half-press (9.4): the user will test which button (key bit `0x1000000`) triggers the stock magnify. Then optionally auto-enable when focus is green.
- Rejected ideas: a faster AF clock (already 40 MHz), bigger coarse steps (the user trusts Sigma's tuning), a lower refine minimum of 5 frames (saves almost nothing).
