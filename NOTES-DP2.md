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
