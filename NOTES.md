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
