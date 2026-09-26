# DP2X Longevity Firmware update

Modified firmware for the **Sigma DP2x** camera. The lens stays out when you turn
the camera off, so the lens motor and gears don't have to push it in and out every
time you use the camera.

There are two versions to choose from:

- **Lens-only**: just the lens change above.
- **Full**: the lens change plus extra features (shot counter, 25 focus points and more; see
  [Full version: extra features](#full-version-extra-features)).

> [!WARNING]
> **PLEASE READ BEFORE USING. NO WARRANTY. USE ENTIRELY AT YOUR OWN RISK.**
>
> This is unofficial, modified firmware. It is **not made, endorsed, supported or
> approved by SIGMA Corporation**. Installing it can permanently damage your camera
> ("brick" it), and may void your warranty and any right to repair service from SIGMA.
>
> **The authors and contributors are NOT responsible for any damage to your camera,
> lens, memory card, pictures or anything else, or for any loss from using it.
> This includes a bricked camera, lens or mechanism failure, and lost images.**
> By downloading or installing this firmware, you accept full responsibility for the result.
>
> **Always follow SIGMA's official firmware update instructions and procedures for
> the DP2x.** In particular:
> - use a fully charged battery;
> - use an SD card formatted in the camera;
> - never turn the camera off, open the battery or card door, or take the card out
>   while the update is running.
>
> Both versions have been tested on **one** camera so far. The full version changes more of
> the camera, so treat it as more experimental. It is provided "AS IS", WITHOUT
> WARRANTY OF ANY KIND. If you are not comfortable with the risk, please don't use it.

## What changes

| | Original Sigma firmware | With this update |
|---|---|---|
| Turning the camera **off** | Lens goes back in | **Lens stays out.** The camera also turns off faster |
| Turning the camera **on** (lens already out) | Lens goes all the way in, then back out | **Lens doesn't move** |
| Turning the camera **on** (lens in) | Lens comes out, and warns you if the lens cap is on | Same as before |
| Autofocus | Works normally | Same. You may hear a short focus-motor sound at switch-on; that's normal |
| Leaving the camera in **playback** for a long time | Lens goes back in | Same as before |

Everything else in the camera works as normal. The update is based on Sigma's
DP2x firmware **version 1.02**.

## Full version: extra features

Everything in the lens-only version, plus:

| Feature | Original Sigma firmware | Full version |
|---|---|---|
| **Shot counter** | The version screen in the setup menu shows the serial number | It shows how many pictures the camera has taken (**Shots: n**) |
| **ISO choices** | Auto, 50, 100, 200, 400, 800, 1600, 3200 | **Auto, 50, 100, 200** only (the settings that give the cleanest pictures) |
| **Autofocus** | Checks focus again from 16 steps past the sharpest point | Checks again from 8 steps past, so the lens moves less |
| **Evaluative metering** | Meters normally | Pictures come out **1/2 stop darker**, to protect highlights. Only in Evaluative; your EV compensation still works on top, and the screen still shows 0 |
| **Focus points** | 9 points (3 x 3), all the same size | **25 points (5 x 5).** The centre box is the normal size, the other 24 are the smaller box, with a wider gap each side of the centre |
| **Back to the centre point** | Not available | On the focus-point screen, **DISPLAY** jumps back to the centre box. Press it again on the centre to switch to free-move mode (the original DISPLAY action) |

**Before installing the full version:**

- **Set ISO to Auto, 50, 100 or 200 first.** The menus only offer those settings afterwards.
- **Know the trade-off.** Sigma's factory service calibration tool (used for repairs over USB) can't run
  with the full version installed, because its space in the firmware now holds the new
  features. Your camera's own calibration is still used as normal. Installing the original
  Sigma firmware brings the tool back.

## How to install

1. **Download one update file.** Both are called **DP2X102.BIN**; that's the name the camera needs.
   - **Lens-only:** [**DP2X102.BIN**](https://github.com/marcuslow/DP2X-Longevity-Firmware-update/raw/main/build/DP2X102.BIN)
   - **Full (lens + extra features):** [**DP2X102.BIN**](https://github.com/marcuslow/DP2X-Longevity-Firmware-update/raw/main/build/full/DP2X102.BIN)
2. **Fully charge** the camera battery.
3. **Format** your SD card in the camera. This erases the card, so save your pictures first.
4. Put the card in your computer and copy **DP2X102.BIN** onto it. Put it on the
   main level of the card, **not** inside any folder, and don't rename it.
5. Put the card back in the camera and **run the firmware update the same way as
   SIGMA's official update instructions for the DP2x.**
6. Wait until the update has **completely finished**. Don't touch any buttons or doors
   while it runs.
7. Turn the camera on. The first time, the lens comes out as usual. From then on it
   stays out.

## What if I want the lens to retract?

Sometimes you'll want the lens in, for example before putting the camera in a bag
or fitting the lens cap. There are two ways to do it.

**Option 1: the setup dial (quickest)**

1. Turn the mode dial to **SETUP**.
2. Switch the camera on. The **lens retracts**.
3. You can now turn the camera off. The lens stays in.

**Option 2: wait in playback**

1. Switch the camera to **playback** mode (viewing your pictures).
2. Leave it alone. Don't press any buttons.
3. When the playback timeout is reached, the **lens retracts by itself**.
4. You can now turn the camera off. The lens stays in.

**Tip:** if the camera switches itself off before the lens goes in, set **auto power
off** to a longer time, or turn it off, in the camera's menu. Otherwise the camera
turns off with the lens still out.

Either way, the next time you switch on in shooting mode, the lens comes out again
as normal.

## How to go back to the original

Download the official **DP2x firmware 1.02** from SIGMA's support website and
install it the same way. The camera will then behave exactly as it did before.

To switch between the lens-only and full versions, just install the other file the same way.

## Good to know

- **The lens now stays out when the camera is off.** Handle and store the camera
  carefully, don't press on the lens, and note that the lens cap may not fit.
- **Lens cap:** if the lens is in and you switch on with the cap still on, you get
  the usual "Remove the front cap" message. Take the cap off and switch on again.
- **If the lens ever gets stuck part-way out**, for example after the battery
  came out while the lens was moving:
  1. Retract it using the playback method above.
  2. Switch back to shooting, and the lens comes out properly again.

  If that doesn't help, go back to the original Sigma firmware (see above).
- Not affiliated with SIGMA Corporation. The DP2x firmware is SIGMA's copyright;
  "SIGMA" and "DP2x" are SIGMA's trademarks, used here only to name the camera.

---

<sub>For developers: technical notes are in [`NOTES.md`](NOTES.md) and the patch tools are in [`tools/`](tools/). The full version is built from branch [`experimental-af25`](https://github.com/marcuslow/DP2X-Longevity-Firmware-update/tree/experimental-af25), which has the full notes and patch script.</sub>
