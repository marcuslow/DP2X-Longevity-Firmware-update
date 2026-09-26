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
  - **Full dead block once `0x27f6b2` returns at once (checked 2026-09-26):** `0x27ea2e`-`0x27f8e3` (3766 bytes) = `0x27ea2e` (288 B), `0x27eb4e` (2232 B), `0x27f406` (196 B), `0x27f4ca` (488 B) and the `0x27f6b2` body (562 B). Every call into these comes from `0x27f6b2`'s own body. The only reference from outside is `0x27cfcc` `ldi:32 0x27f6b2`, and there are no raw pointers into the range.
  - Allocation: `0x27eb4e`-`0x27eb85` eval-bias (56 B). Free: `0x27ea2e`-`0x27eb4d` (288 B), `0x27eb86`-`0x27f6b1` (2860 B), `0x27f6b4`-`0x27f8e3` (560 B). About 3.7 KiB in total. Keep `0x27f6b2` = `ret`.

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

### 9.8 ISO choices limited to Auto/50/100/200 (`--iso-max-200`)
- ISO setting: `0x80101d48`. Values: 0 = Auto, 1 = 50, 2 = 100, 3 = 200, 4 = 400, 5 = 800, 6 = 1600, 7 = 3200.
  - Getter `0x2121da`, setter `0x21227a`, applied via `0x21b886`/`0x21bc20` -> `0x3871cc` ("ISO SET").
  - In some modes the setter forces 0 -> 2 (no Auto in M), and 6/7 -> 5 outside one mode (1600/3200 RAW-only).
- Menu tables are in the .data image: boot copies flash `0xc0000` (0x12dcc bytes) to RAM `0x6a018184` (`0x2030a4`).
- **QS menu:** page pointers at RAM `0x6a01f24c`. Page 0 starts with the ISO item at flash `0xc7140` (0x1c bytes: 3 draw fns, set, get, u16 count @+0x14, options ptr @+0x18). Options are 16-byte records at `0xc73f0` (value, 3 draw fns). The QS screen (`0x2c6448`) draws `count` options centred in 8 slots, and cycling wraps at `count`. Patch: count 8 -> 4.
- **MENU grid:** item at flash `0xc9308` (0x20 bytes: title, help strings, options ptr, u16 count @+0x18, get, set, flags). Options are 0x2c-byte records at `0xca3cc`: +8 value, +0x14 column, +0x18 row, +0x1c enabled, +0x20 (up, down), +0x24 (left, right) option-index links. Patch: count 8 -> 4, and relink Auto/50/100/200 into a closed 2x2 grid (same wrap style as stock).
- Set ISO to Auto/50/100/200 **before** flashing, so the stored value is one the menus still list.
- Auto-ISO behaviour and the ISO the camera applies internally are unchanged. Only the choices offered change.
- **2026-09-26: flashed in `build/DP2X102_test2.BIN`. Works:** QS and MENU offer only Auto/50/100/200.

### 9.9 AFE (analog front end) gain and highlight headroom (analysis only)
- Still-capture AFE gain: `0x2451e6(step)` writes three 16-bit gains (one per sensor layer) to AFE regs 0/2/4 (`|0x2000`), from RAM table `0x6a018d3c` (6 bytes per step).
  - Alternative table `0x6a018d6c` is used only when the factory test flag `0x6a01b3f0` is set (service USB command).
  - AF uses `0x6a018d54`.
- The table is filled at start-up (`0x244ca4`) from per-camera EEPROM calibration (`0x24a754` reads at `0x31e`/`0x320`, 24 bytes = 4 steps x 3 layers). On failure ("AFE Gain Init Error") the fallback is `0x6a018d84`: step 0 = `0x000`, 1 = `0x0aa`, 2 = `0x1ff`, 3 = `0x353`.
- The requested ISO is converted to an analog step plus a digital remainder in `0x244f..`-`0x2450d2` (double maths, base 50.0; step -> `0x6a018d10`, digital gain -> `0x8010b124`). The threshold table `0x6a018cd0` is filled at runtime, so the exact ISO -> step mapping wasn't confirmed statically.
- Conclusion: step 0 is gain code 0, the AFE minimum. There's no lower analog setting, so firmware can't add highlight headroom. The DP2 vs DP2x difference is hardware. The practical mitigation is exposing less.

### 9.10 Metering and EV compensation
- Metering setting (`0x21242c`/`0x212458`): 5 = Evaluative, 2 = Center Weighted Average, 3 = Spot (MENU options `0xca...`, labels `0x46af58`...). `0x21b8e0` maps 5 -> AE mode 0, 2 -> 1, 3 -> 2 via `0x38674c`, which stores the AE mode in `0x6a019628` and loads that mode's zone-weight map (`0x3be7dc` + mode*128).
- EV compensation: UI index (9 = 0 EV, 1/3-EV steps) stored by `0x389c1c` at `0x8010bc3c` (AE struct `0x8010bbfc` + 0x40). Table `0x3c0198` (19 x s32, 16.16 EV: 65536 = 1 EV, 21845 = 1/3 EV).
- The AE target functions `0x389726`/`0x389a7c` take the metered value from `0x38968c` and add or subtract the table entry. The metered value is also read through wrapper `0x38a084` by AF (`0x28425a`, `0x243c9a`) and others (`0x2558e4`, `0x25609e`, `0x38e3dc`).
- Sign: EV comp index < 9 (negative comp) *adds* the table value, so a larger value means a darker picture.
- AE mode `0x6a019628` is written only by `0x38674c`, and only from the metering setting (`0x214004`..., `0x21b922`...). So 0 always means Evaluative.
- Implemented as `--eval-bias EV` (9.11).

### 9.11 Evaluative auto-bias (`--eval-bias -0.5`, branch `experimental-ev`, flashed 2026-09-26, working)
- Both AE target functions have the same 10-byte sequence at `0x38976c` / `0x389ac0`: `call 0x38968c` (meter) followed by the start of the EV-comp block. Each is replaced with `ldi:32 0x27eb4e,r12; call @r12; bra <epilogue>` (the old `beq` to the epilogue becomes a `bra`). The rest of the old EV-comp code is left in place but is never reached.
- Cave routine at `0x27eb4e` (56 bytes): calls the meter, applies EV comp exactly as stock (r8 = AE struct, r9 = EV table, both still set up by the caller), then, if `[0x6a019628] == 0`, adds `ldi:20 0x8000` (0.5 EV in 16.16) to r4. Positive `--eval-bias` values use `sub` instead.
- `0x27f6b2` (factory AFE-gain adjust entry, return value unused by `0x27cc86`) starts with `ret`, so the service command can't run the overwritten code (9.5).
- Not affected: the `0x6a019698` override path, the non-metered path (`+0x2c != 1`), and AF's direct meter reads via `0x38a084`. The EV comp shown on screen is unchanged. The bias is hidden.
- Possible side effect: in M mode, if the exposure meter uses this target, it will read 0.5 EV off in Evaluative. Check on the camera.

## 10. Resume here (session ended 2026-09-26)

**Branch `experimental-ev`:** `build/DP2X102.BIN` here is the full build (lens patch + `--af-refine 8` + `--shutter-count` + `--iso-max-200` + `--eval-bias -0.5`), SHA-256 `1855cdc5...4f73`. It's the same file as `build/DP2X102_test3.BIN`. Copy `build/DP2X102.BIN` straight to the card root. On this branch it is always the build to flash. Build: `python3 tools/patch_lens.py dp2x102.bin build/DP2X102.BIN --af-refine 8 --shutter-count --iso-max-200 --eval-bias -0.5`. **Flashed 2026-09-26. The user reports the EV bias seems to be working.** Still to check: M-mode meter offset in Evaluative, and the AF refine result.

**On the camera now:** `build/DP2X102_test2.BIN` (lens patch + `--af-refine 8` + `--shutter-count` + `--iso-max-200`), SHA-256 `4dd5735f...c4ac`.
Build: `python3 tools/patch_lens.py dp2x102.bin build/DP2X102_test2.BIN --af-refine 8 --shutter-count --iso-max-200`.
Rollback: `build/DP2X102_test.BIN` (without the ISO limit), `build/DP2X102.BIN` (lens patch only) or stock `dp2x102.bin`, renamed `DP2X102.BIN` on the card root.

**Status**
- Lens patch: done, confirmed (section 7).
- Shutter count on the version screen: done, confirmed (9.1, 9.7).
- ISO choices limited to Auto/50/100/200: done, confirmed (9.8).
- AF refine 16 -> 8: **testing in progress.** The user is comparing AF speed and sharpness (close-up f/2.8, distant, dim; about 5 tries each; slow-motion video for timing).

**Next steps, depending on the AF result**
1. Faster and still sharp: consider option C, skipping the refine pass (go straight to the interpolated coarse peak with a same-side approach). Needs injected code in the factory AFE-gain cave `0x27eb4e`-`0x27f405` (9.5), plus neutralising `0x27f6b2`.
2. No difference: the clamp to `0x8015153c` in `0x283e2c` probably overrides the offset. Work out what the evaluator stores in `0x8015153c` (from `0x307da6`'s per-window output, local `r14-64` in `0x2d928e`).
3. More misses or softer focus: revert to 16 (build without `--af-refine`).

**Parked**
- Magnify during half-press (9.4): the user will test which button (key bit `0x1000000`) triggers the stock magnify. Then optionally auto-enable when focus is green.
- Rejected ideas: a faster AF clock (already 40 MHz), bigger coarse steps (the user trusts Sigma's tuning), a lower refine minimum of 5 frames (saves almost nothing).
