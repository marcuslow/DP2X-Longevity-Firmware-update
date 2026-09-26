# Sigma DP2 firmware 1.05 (`dp2v105.bin`): porting the DP2x AF

> **Disclaimer:** unofficial, unsupported modification. Use at your own risk. See `README.md`.
> Static analysis only. Nothing here has been built or run on a DP2 yet.

Goal: first give the DP2 the stock DP2x 1.02 AF (engine + focus-motor driver). Then add our DP2x AF
changes (refine 8, 5-point, fallback). The EV bias is not wanted on the DP2.

## 1. File and tools

- Sigma's file `dp2v105.bin` (1.05.0.0000, 2010-05-13) goes in `sigma DP2/` (git-ignored, like `dp2x102.bin`).
  It has the same layout as the DP2x: 0x80 header, sub-micom slot `0x80-0x407f` (empty), 8 MiB image at file `0x4080`
  loaded at `0x40000`. The checksum at `0x4c` is the byte sum of file `0x80..EOF` (verified: `0x146e8d71`).
- **Updater file name: `DP2V<ver>.BIN`** (strings `DP2V`, `.BIN`, `D.BIN`), so builds are `DP2V105.BIN`.
  Whether it accepts the same version again (as the DP2x does) is not checked yet.
- Disassembly: `tools/disasm.sh 'sigma DP2/dp2v105.bin' work-dp2` (git-ignored output).
- `tools/xmatch.py SRC DST ADDR...` finds the twin of a function in the other firmware (operands masked, then fuzzy).
  `tools/closure.py SRC DST LO HI ROOT...` lists a function's call closure with twins and RAM use.

## 2. What differs (DP2 1.05 vs DP2x 1.02)

**Same in both:** CPU, AF contrast block (`0x40000000`, contrast at `+0x88/+0x9c/+0xb4`, ready bit `+6` bit `0x20`),
the AF contrast library (DP2 `0x30e724` == DP2x `0x30c21e`), and the RTOS task layout.
The focus hardware is the same too:
- home sensor I/O `0x5a6` (`0x510+0x96`)
- home = 364 (`0x16c`), limit 338 (`0x152`)
- motor-chip registers 8/9/A/B (`0xb000|n` = steps) on device 2
- motor-done event flag `0x35`
- the same homing method

**AF engine (software):** the DP2x AF control module (`0x2836xx-0x2845xx`) is a rewrite of the DP2 one
(`0x27fe04-0x281200`). The neighbouring functions are twins, for example DP2 `0x27fc4a` == DP2x `0x28366c`.
- The DP2 lens task feeds each frame to a message-driven AF module (`0x302b60`, start `0x302c52`).
  In the DP2x that module still exists, but it is dead: `0x308192` has no callers.
- Sigma's DP2x catalog says "faster auto focusing with an enhanced AF algorithm".

**Focus drive (software):**
- DP2 move `0x381098`: send `[0xb000|n, 0x8597]`, sleep 10 ms, poll `0x5a6` every 1 ms until the motor stops,
  then send hold `0x8517`.
- DP2x move `0x38ab64`/`0x38abf4`: wait on flag `0x35`, send, return at once. The lens moves while the next
  AF frame is exposed.
- The motor-chip settings differ: reg8 low bits DP2 `0x17` vs DP2x `0x0a`; regA `0x11f` vs `0x19f`
  (DP2x init `0x38a122`). Meaning unknown, possibly the step rate.
- Homing creep: DP2 about 5 ms per step, DP2x 1 ms. Back-off: DP2 10 steps, DP2x 20.

**Hardware:** the DP2x adds an AFE chip and an FPGA on LVDS at 20/40 MHz (AF uses 40 MHz). The DP2 uses a
different sensor driver ("SENDRV"). It has no AFE-gain factory routine, so the DP2x code cave doesn't exist here.

## 3. How AF is wired

RTOS task table at `0x200000` (28-byte entries from `0x20001c`: flags, entry, stack, 0, ?, id<<16|prio, 0):

| | DP2 | DP2x |
|---|---|---|
| task 6 (prio 20), lens/AF task | `0x3817a0` | `0x38b1d4` |
| extra AF-related task | task 20 `0x204816` (calls `0x383080`, frame wait) | gone |

- **DP2x task 6** (`0x38b1d4`): state `0x6a019a6c`.
  - 0: run `0x284114`, sleep 1 ms.
  - 1: direction setup `0x2841de`, then `0x38b00e`.
  - 2: frame wait + store `0x38af12`, then the state machine `0x284114`.
  - 3: finish `0x2842f6` / `0x284394`, wait flag `0x35`, motor hold `0x38a1a0`, state 0.
- **DP2 task 6** (`0x3817a0`): while AF is on (`0x6a0195b4`==1):
  - wait for a frame (`0x36cefc`)
  - decide with `0x280218` (result 1 -> `0x302c32`, 2 -> `0x302c52`)
  - feed the AF module (`0x302b60`), then `0x27f7da`, then send a message `0x308392`
- **Half-press:** DP2x `0x238ad6` vs DP2 `0x238254` (similar, ratio 0.87).
  - DP2x calls `0x28425a` (start), `0x38b038`, `0x38b188`, `0x38b090` (end: 1 = OK, 2 = fail).
  - DP2 calls `0x37fdfc` (start: `0x2800da` + `0x302c52`), then polls `0x37fe78` / `0x37fe5c` with 200 ms waits.

## 4. What has to be ported

`tools/closure.py work work-dp2 ...` from roots `0x28425a 0x2d928e 0x284114` and the lens-side functions
(`0x38ad38`-`0x38b21c`) gives:
- AF engine: 19 functions, 3292 B, RAM `0x80150f9c-0x80151548` plus `0x801552xx` (per-frame wrapper `0x2d928e`).
- Lens side: 26 functions, 2164 B, RAM `0x6a019a64-0x6a019a8c`.
- None has a real DP2 twin (the 1.00 hits are trivial getters), so the code has to be relocated as a whole.
- External callees to map to DP2 addresses:
  - AF library `0x30c21e`/`0x307da6`/`0x30837c`
  - meter `0x38a084`/`0x38968c`
  - device send `0x2525d4`/`0x252650` (DP2 `0x250c28`/`0x250ca4`)
  - Timer32 `0x2b4xxx`
  - AF point `0x2130e4`
  - half-press helpers `0x238a8a`/`0x238ab0`/`0x23941c`, and `0x27007c`/`0x2700a4`/`0x397fd8`/`0x398018`/`0x374dc8`/`0x387520`

**Code space (chosen): overwrite the old DP2 AF engine module `0x27fe04`-`0x2818f9` (6902 B).** The module ends
at `0x28183c`+190 B. From `0x2818fa` on it is other modules (getters called from `0x2788da`, `0x38bbd4`, ...).
Every way into it (checked 2026-09-27):
- Direct calls from outside, 6 of them:
  - `0x2800da` <- `0x37fdfc` (AF start, half-press only): replaced.
  - `0x28018e` and `0x280218` <- task 6 `0x3817a0`: replaced.
  - `0x2808b0` <- `0x27fac8`, which is only called from inside the module (`0x2805b0` <- `0x2800da`), so dead.
  - `0x280254` <- `0x27f6ea`. That is the old AF module's "NoContrast done" callback: its pointer is at `0xc3644` in
    the callback table in .data. It only runs while the old module is running. Keep a stub at `0x280254` that
    returns 0; the callback then takes its "NoContrast done" path and doesn't retry through `0x383758`.
  - `0x280acc` <- `0x204a84` (key scanning). It is a 24-byte setter (`[0x6a01aef8] = arg`). Keep it in place.
- Pointer table `0x40a850` (`0x28108a`/`0x281116`/`0x2812b0`): read only from inside the module (`0x281018`).
- No other ldi:32 or raw pointers. The other hits are image or font data, or pairs of 16-bit values
  (for example `0xc3014` = 40, 218).
- The old AF module (`0x302xxx`) can only be *started* (`0x302c52`/`0x302c32`) by the half-press start `0x37fdfc`,
  task 6 and the NoContrast retry `0x383758`. All three are gone after the port.
  - Other code, `0x237e36` and `0x238ac0` (via `0x37fc74`/`0x37fcac`/`0x37fd34`), still sends it cancel/stop events.
    That's harmless while it is idle.
  - Those paths must also stop the new engine. That is part of the interface work.
- The old task-6 body `0x3817a0` (240 B) also becomes dead, but it sits between live lens functions; reuse it only
  if needed.
- Space: 6902 B minus the stubs, against about 5.5 KB of ported code.

**Fallback code space:** the DP2 image has a 650 KB zero run at `0x160aa4-0x1fffff`, just below the code at `0x200000`.
- No `ldi:32` in the code points into it. The candidates `0x177000`/`0x186000`/`0x189000`/`0x1b9000`/`0x1e0000`
  are sizes or multipliers, and `0x1f0030` is a task PS value, the same as in the DP2x.
- Raw words in data that fall in the range were not all checked.
- Not proven unused. Before relying on it, check the flash driver's erase/write addresses.

**RAM:** the new engine needs about 0x5b0 B of state (DP2x `0x80150f9c...`) plus the lens variables. Plan: reuse the
old DP2 AF engine's RAM (not yet mapped), or find unused SDRAM.

## 5. Plan

1. **Interfaces.** Map every external callee and RAM variable in 4. to its DP2 equivalent. Decode the DP2 half-press AF
   block and the DP2x one step by step, and map the DP2 task-6 result protocol (`0x37fe78` values).
2. **Relocator.** `tools/port_af.py`: take the DP2x functions' bytes and rewrite every `ldi:32` constant, `call`/branch
   displacement and delay-slot call through an address map (DP2x -> DP2 or -> new cave address). Fail on any
   unmapped constant. Re-disassemble the output and diff it against the source instruction by instruction.
3. **Hooks.**
   - Task-table entry 6 -> ported `0x38b1d4`.
   - DP2 half-press AF block -> calls to the ported start/wait/end, returning results in the form the DP2 code expects.
   - Leave the old DP2 AF module in place (unused), like Sigma did on the DP2x.
4. **Motor settings.** Start with the DP2x async move but the **DP2's own** motor-chip values (`0x8597`/`0x11f`).
   Try the DP2x values only as a separate test build.
5. **Verify without a camera.** Run the ported engine on the FR emulator (tools used for the fallback check) with
   synthetic contrast curves. Compare its lens-move sequence with the DP2x original on the same inputs.
6. Only then: the DP2x extras (`--af-refine 8`, `--af-5`, `--af-fallback`) re-targeted to the ported addresses.
   The lens patch and shot counter can be ported separately.

Risk: the first flash can only be tested on a real DP2. A crash during AF is recoverable only if the updater still runs.
It runs from the same image, so boot must stay untouched: no hooks on the power-on path.

## 6. Interface map (step 1, done 2026-09-27)

The port has two halves:
- **Front half (hardware), kept DP2-native:**
  - sensor AF mode (DP2 `0x37c7e8(1)`, DP2x `0x387310(1)`)
  - AF readout window
  - contrast filter window and coefficients (DP2 SetHpfWindowSize `0x27f92a`, called from the mode/UI code
    `0x37fd7c`/`0x3835dc`/`0x383668`, as on the DP2x)
- **Back half (algorithm), taken from the DP2x:** task-6 loop, state machine, scan/refine, peak logic,
  per-frame contrast store, lens-move decisions, the AF library tuning table.

**Motor: use the DP2's own primitives. Don't port the DP2x motor code.**
- The DP2 already has the non-blocking move.
  - DP2 `0x381164` == DP2x `0x38ab64` (minus direction, `[0xb000|n, 0x8597]`).
  - DP2 `0x381350` == DP2x `0x38abf4` (plus direction, `0x85b7`, clamp 364, home-sensor check).
  - Both wait on flag `0x35` and arm the Timer32 motor-done timer through `0x380fbc` (== DP2x `0x38a64c`).
  - Its callback `0x380f84` (== DP2x `0x38a614`) sets state 2, sets flag `0x35` and stops the timer.
  - The DP2 also claims motor power with `0x3836bc(2,0)` ("PowerFace") and releases it on hold.
- The one DP2x addition: its timer callback also calls `0x38a594`, which during a coarse scan issues the next step
  from the interrupt (continuous stepping). Port `0x38a594`. Point the callback pointer that `0x380fbc` loads
  (`ldi:32 0x380f84` at `0x381014`) to a wrapper: call `0x380f84`, then the ported `0x38a594`.
- DP2x hold `0x38a1a0` (`0x850a|dir<<5` to device 8) is replaced by a new DP2 hold:
  - wait for flag `0x35`
  - `0x250c28(8, 0x8517|dir<<5)`
  - `0x3836bc(2,1)`
  - motor state byte `0x6a0195b9` = 0
  This follows the DP2 hold sequence in `0x381048`/`0x381228`.
- The motor settings stay DP2 (`0x8597`/`0x85b7`, regA `0x11f`) in the first build.

**Code references (DP2x -> DP2):**

| DP2x | DP2 | what |
|---|---|---|
| `0x30c21e` | `0x30e724` | AF contrast library (identical) |
| `0x307da6` | `0x30c6fa` | library per-window call (identical) |
| `0x30837c` | `0x302e3c` | focus-range/macro getter (`0x8015b99a` -> `0x8015a91a`); check its writer |
| `0x374dc8` | `0x36a476` | memset(p, v, n) (byte-identical) |
| `0x387520` | `0x37ca00` | identical |
| `0x38a084` / `0x38968c` | `0x37eb90` | meter (the DP2x wrapper only calls the meter) |
| `0x38ab64` / `0x38abf4` | `0x381164` / `0x381350` | focus move -/+ (see above) |
| `0x38a1a0` | new hold routine | see above |
| `0x38b000` | ported as-is | AF block re-arm (`[0x40000006] \|= 0x20`) |
| `0x2525d4`/`0x252650`, `0x2b4xxx` | not needed | only used by DP2x motor code that isn't ported |

**UI calls in the DP2x AF end `0x38b090`: dropped.** On the DP2 the half-press code draws the result itself.
The twins, for reference:

| DP2x | DP2 |
|---|---|
| green frame `0x27007c` | `0x26eec2` |
| red frame `0x2700a4` | `0x26eeee` |
| beeps `0x397fd8` / `0x398018` | `0x38ed64` / `0x38ed24` |
| LED bits `0x238a8a` / `0x238ab0` | `0x382558` / `0x382570` |

The DP2x also calls the AF point xy `0x2130e4` and a flag `0x23941c` there. The DP2 AF point is an index 0-8
at `0x801018d4` (`0x213068`), not x/y.

**RAM (DP2x -> DP2):**
- Shared with DP2 lens code:
  - focus position `0x6a019a78` -> `0x6a0195a4` (word)
  - motor state `0x6a019a70` (word) -> `0x6a0195b9` (**byte**: rewrite `ld`/`st` to `ldub`/`stb` in
    `0x38b00c`, `0x38b038`, `0x38b21c`, `0x38a614` code)
  - init flag `0x6a019a74` (DP2x: 2 = homed) vs DP2 `0x6a0195a0` (1 = homed). Only used inside the move functions,
    which are not ported.
- New, allocated in the old AF module's scan buffer `0x801574f4` (0x3254 B):
  - That buffer is reachable only through `0x304314` (getter), whose only caller is the old engine start
    `0x2800da`, which is replaced. So nothing can reach it after the port.
  - Contents: engine context `0x80150f9c-0x80151547` (0x5ac), library params `0x8015528c-0x801552eb` (0x60),
    lens/task variables `0x6a019a64-0x6a019a8c` (0x2a), `0x6a01b78c`.
  - The AF-library tuning graph `0x6a01b79c-0x6a01b9fb` (0x260 B, 12 objects with internal pointers) is copied
    with its pointers fixed up.
  - The library work buffer `0x6a025980` is still to be sized.
  - These are .bss, so initialise at task start the ones the DP2x has in .data: window `0x6a019a84`=0x131,
    `a86`=0x21, `a88`=0x131, last dir `a8c`=1, tuning pointer `0x6a01b798` -> copy of `0x6a01b79c`.
- Task 6's stack is 0x1600 on both cameras. The per-frame wrapper needs 0x47c plus the library.

**Control interface (DP2x lens task):**
- `0x38b038` start:
  - result `0x6a019a68` = 0
  - wait up to 338 ms while the motor state is 1; if it times out, `0x38b090(2)`
  - otherwise task state `0x6a019a6c` = 1
- `0x38b090(r)` end, r = 1 OK / 2 fail / 3 abort:
  - result = 2 / 1 / 3
  - `0x38ae80(0)`, then `0x284466(r)`, then task state 3
- `0x38b188` poll: 2 = busy (task state != 0), else result 2 -> 0 (OK), 1 or 3 -> 1, 0 -> 2.
- Half-press order on the DP2x: `0x387310(1)`, then engine start `0x28425a`, then `0x206166`/`0x206182` wait,
  then `0x38b038`, then poll every 1-30 ms. If S1 is released: `0x213cc6()`, `0x38b090(3)`, wait until not busy.

**DP2 hooks (replace three DP2 functions with adapters; the half-press code stays untouched):**
- `0x37fdfc` start (called once from half-press `0x238750`): ported `0x28425a()`, then ported `0x38b038()`.
- `0x37fe78` poll (DP2 meaning: 0 = running, >0 = in focus, -1 = failed):
  ported `0x38b188()` 2 -> 0, 0 -> 1, 1 -> -1.
  - In focus: in the DP2 9-point auto mode (`0x213014()`==1) the value is a window bitmask, 1<<index
    (`0x237dcc`). Return the centre, index 4 = `0x10`.
- `0x37fe5c` abort: ported `0x38b090(3)`.
- Task-table entry 6 -> ported `0x38b1d4`. DP2 task 6 `0x3817a0` also ran `0x27f7da` and message `0x308392` after
  each frame for the old module; not needed.
- In the old block: `0x280254` becomes a `return 0` stub. `0x280acc` (24 B setter) stays.

**Open questions, resolved 2026-09-27:**
1. **9-point auto AF: decided by the user, centre point.**
   - AF mode is `0x801018d0`: 1 = auto, 2 = selected point. It is only meaningful in some shooting modes
     (`0x213014`).
   - In auto mode the poll adapter returns `0x10`, which is 1<<4, the centre.
   - Still to do: make sure the front half programs the centre window in auto mode.
2. **Contrast scale: same AF block setup on both cameras.**
   - The HPF coefficients (DP2 `0x6a01ad5c` / DP2x `0x6a01b5bc`, loader `0x27f82e` == `0x283418`) are identical.
   - The AF-mode window templates are identical: mode1, normal, and (`0x28`, `0xda`). DP2 `0x3835dc` ==
     DP2x `0x38e3dc` -> `0x38a908(1)`.
   - So the contrast numbers differ only by the sensor/AFE signal level, which firmware can't show.
   - The DP2x has an extra *small-frame* window (`0x14`, `0x78`); the DP2 has no frame-size setting.
   - **The contrast library tuning differs.** Settings object: DP2x `**0x6a01b798` = `0x6a01b7bc`, +8 ->
     `0x6a01b7ec`; DP2 `0x6a01af28`, +8 -> `0x6a01af58`. Same layout: two blocks of
     (`0x71000`, `0x50000`, lo, hi, `0xc0022`, curve table ptr), then `0x5f5fff`, and so on.
     - DP2x: lo/hi `0x350`/`0x31f` and `0x378`/`0x39b`.
     - DP2: `0x3ba`/`0x6c3` and `0x3fe`/`0x4a9`.
     - The curve tables differ too.
     - `0x2d905e` copies settings +0..+0x30 into the library parameters at `0x801552ac`-`0x801552e8`;
       the window count is `0x6a01b7bc`+0x18.
     - Plan: port the DP2x settings (the default). Add a build option that uses the DP2's own settings,
       in case the values are sensor-specific.
   - In the pointer walk, `0x6a01b930`-`0x6a01b9bb` (RAM pointers, and a function table `0x28307a`-`0x28338a`)
     is the old module's callback objects, which are dead in the DP2x (the counterpart of the DP2 `0xc3644`).
     It isn't part of the settings.
3. **Frame wait.** The DP2 `0x382100` waits for AF-done (`0x40000006` bit `0x20`) *and then* bit `0x08`, probably
   the next frame start. The DP2x `0x38af12` waits only for `0x20`. Use the DP2x logic: the AF block is identical,
   and not waiting for the next frame is part of the speed-up.
4. **Macro/range flag.** It is written by the half-press/mode code in both cameras (DP2x `0x38a8d0` ->
   `0x308372`; DP2 `0x37fd34` -> `0x302e32`), outside the engine. Reading it with `0x302e3c` is correct.
