# Cameo hand signs: from prediction to measurement

An architecture plan for the whole sign pipeline (camera to verified action), written 2026-09-28 after reading
the current code (`cameo/gestures/classic.py`, `cameo/main.py`, `cameo/vision/worker.py`, `cameo/pipeline.py`,
`cameo/control/*`) and the last four live session logs.

## Status (2026-09-28)

Decisions taken by the user: volume 25 % and brightness 40 % per palm width; revert on a fist or on losing the hand
within 0.3 s of starting; the line is the only volume sign (the pinch-hold wrist dial is removed); sleep is two-stage.
Linux volume: `wpctl`, then `pactl`.

| Phase | State |
| --- | --- |
| 0. Corpus | Tools done: `python -m cameo run --trace` records, `python -m cameo replay` reproduces and scores (tests/test_replay.py). **Waiting on the user** for the six labelled real recordings. |
| 1. Levels | Done: `volume()` / `set_volume()` / `muted()` / `set_mute()` on win32 (Core Audio via comtypes) and x11 (wpctl, pactl); `levels.py` (base, absolute sets, 10 / s, read-back, revert); `volume.set` / `display.brightness_set`; rail and voice volume steps read back; HUD level bar; doctor reports both controls. Linux backend not yet run on Linux. |
| 2. Machine | Done: `signs.py` (lifecycle, focus, cooldowns, LevelLine / Circle / Hold specs), `strokes.py` (palm widths, line and circle fits); tests/test_sign_machine.py. |
| 3. Migrate | Done for the signs whose behaviour the plan changes: lines, circle (fitted, palm widths), sleep (two-stage), right click (travel in palm widths, arc), scroll (palm widths, exact rate), zoom and pinch-key stillness (palm widths); wrist dial removed; `pose.py` split out; hold progress arcs. Click, open, minimize / restore, grab and clap keep their live-tuned timing rules in `classic.py` but run under the machine's focus. Moving them into specs waits for the corpus (their acceptance is "corpus unchanged or better"). |
| 4. Poses | Waits for the corpus (a classifier is trained on recorded landmarks). |
| 5. Docs | Done: GESTURES.md's table is generated (`python -m cameo signs`) and checked by tests/test_catalog.py. |

The complaint it answers: signs work "but not properly". A line drawn left to right raises the volume, but the amount
has no ratio to the line, the screen does not show where the level is going, and a line with no clear end keeps
stepping. The same shape of problem exists in most signs, because the engine is built from timed pulses and flags,
not from measured quantities with a beginning, a value and an end.

---

## 1. What the code does today, and why it misfires

### 1.1 Two kinds of actions, only one of them verified

| Path | Signs | What happens | Verified? |
| --- | --- | --- | --- |
| Pipeline → executor → read-back | minimize, restore, Edge open/close, screenshot, sleep, brightness | An `Intent` runs, the executor checks the result (window state, new window, brightness value) and the HUD shows ✓ or the failure | yes |
| Direct `SendInput` from `classic_action` | pointer, click, right click, open, zoom, scroll, **volume (both signs)**, drag | Keys / wheel / mouse are injected; nothing is read back | no: the log says "sent" and the HUD flashes a word |

Volume is the clearest case. `('volume', ±1)` presses the media key and *assumes* 2 % per press. Cameo never knows
the real level, whether the device is muted, or that the level is already at 100 %. A long line sends many presses;
each is a guess.

### 1.2 Quantities are pulses, not values

`trace()` / `slide()` emit one `('line', dir)` event per 0.06 frame widths of travel; `key_pose()` emits one
`('volume', ±1)` per 12° of wrist turn; `scroll()` emits notches at a rate; `zoom` emits a notch per 0.3 pinch change.
None of them knows the value it is controlling. Consequences the user sees:

- **No ratio.** 0.06 frame widths is a different physical distance at every hand distance from the camera. In the
  logs the hand size (wrist to middle knuckle) ranged from 0.08 to 0.46 frame widths, so the same 10 cm swipe was
  worth between one and six steps.
- **No synchronisation.** The HUD flashes "VOLUME +" for 0.6 s; it never shows the level, the start level or the target.
- **No destination.** A line has no end condition except "the pose changed" or "the hand came back 0.05". Holding
  the fingers still at the end of a long stroke is fine, but a slow drift keeps stepping; detection jitter of a still
  hand can step too (`LINE_BURST` only caps the damage).

### 1.3 One 600-line engine of coupled special cases

`GestureEngine` in `classic.py` holds about 40 tunables and 25 state fields (`fired`, `palm_at`, `fist_used`,
`dial_armed`, `key`, `draw`, `grab_pending`, `tap_started`, …). Signs interact through these flags: `hold_drawing`
patches the pose so a circle survives a dipped finger, `key_pose` rewrites fist/neutral into "zoom" while the thumb is
down, `leave_pose` decides grabs from frame counts. Each fix for one sign is a special case that another sign must
know about. That is why a change to one sign breaks another and why the behaviour feels like prediction: the outcome
depends on which flags happened to be set.

### 1.4 Units are frame fractions and seconds since start

Distances are measured in camera-frame fractions (`DRAW_STEP = .012`, `LINE_LENGTH = .15`, `SCROLL_DEAD = .03`,
`KEY_STILL = .08`, `ZOOM_STILL = .04`). The only distance-invariant measure in the engine is `pinch` (divided by hand
size). Everything else changes meaning when the user leans forward.

### 1.5 What the logs show

| Session | Hand-status samples | Hand seen | Most frequent pose |
| --- | --- | --- | --- |
| 2026-09-26 22:26 | 76 | 49 | fist, palm |
| 2026-09-27 13:08 | 61 | 7 | none |
| 2026-09-28 12:54 | 8 | 3 | none |
| 2026-09-28 13:14 | 72 | 23 | none, fist |

Across sessions the hand was not detected in more than half of the samples, and `drag` events dominate the sign log
(652 of 779 in the longest session). The vision worker changes made today (smoothing, glitch rejection, gap tolerance,
low-light lift) address the first line of this; the plan below addresses the rest.

---

## 2. The protocol: eight rules every sign must obey

**R1. One lifecycle for every sign.** IDLE → ARMED → ENGAGED → CONTROLLING → COMMITTED | CANCELLED → COOLDOWN.
A sign is a declarative *spec* (which pose arms it, what engages it, what it controls, how it ends); one shared
`SignMachine` runs all specs. No sign-specific flags in shared code.

**R2. Absolute values, measured at engage.** A sign that controls a quantity (volume, brightness, zoom, window size)
reads the current value when it engages (`base`), maps hand displacement to a `target`, sets the target and reads
the result back. Stopping the hand stops the value; moving back moves it back; letting go commits. There are no
pulses and there is nothing to "run away".

**R3. Hand-size units.** Every distance threshold is expressed in **palm widths** (the vision worker already reports
`size`; the tracker's `palm_size` is the robust version). 1 palm width is about 8–9 cm for an adult hand at any
distance from the camera. Every time is in seconds from the frame timestamp. Every angle is in degrees.

**R4. One writer per quantity, with read-back.** `control/system.py` grows a *levels* API: `volume()`,
`set_volume(fraction)`, `brightness()`, `set_brightness(percent)`, later `zoom` if a host ever exposes it. The
executor verifies each set by reading back within a tolerance; anything that cannot be read back (wheel zoom, scroll)
is reported as `sent`, never as verified, and the HUD says so.

**R5. Continuous feedback while controlling.** From ENGAGED to COMMITTED the HUD shows the quantity: a bar with the
base tick, the target and the actual read-back, plus the sign's name. Arming shows a progress arc (the HUD already has
`arc`). Commit shows the final read-back in a toast; cancel shows the reason. Nothing acts silently.

**R6. Exactly one sign at a time.** The machine holds a *focus*: while one sign is ENGAGED or CONTROLLING, no other
sign can arm, and the pointer is frozen (as the pinch key already does). Priorities decide ties at ARMED. The existing
guards stay: physical mouse priority, held buttons and modifiers, secure desktop, two hands close together.

**R7. Explicit end conditions.** Every spec names its commit and cancel rules. Typical commit: pose released, or hand
still for 0.5 s (value freezes; further movement is ignored until re-arm). Typical cancel: hand lost within 0.3 s of
engage (value reverts to base), a cancel pose (fist) during control, or a timeout. A sign with no destination cannot
exist: if the spec has no end rule the machine refuses to load it.

**R8. Decided by data, not by feel.** Every spec ships with synthetic traces that assert exact outputs (values, times,
counts) and is checked against a corpus of recorded real hand traces with a replay tool that reports, per sign,
how many intended signs fired, how many misfired and how many were missed. Thresholds change only when the corpus
says so.

---

## 3. Target architecture

```
camera ─► vision/worker.py      HandFrame: t, points (mirrored, smoothed), world, palm width, centre, conf, light
            │
            ▼
        gestures/pose.py        PoseClassifier (moved out of classic.py): finger states with hysteresis
            │                   Pose: label, fingers[4], thumb, pinch (palm widths), roll (deg), curl (0..1)
            ▼
        gestures/signs.py       SignMachine + SignSpec registry (one lifecycle, focus, priorities, cooldowns)
        gestures/strokes.py     stroke geometry in palm widths: line / circle fit and residuals (shapes.py grows into this)
            │  SignEvent: (sign, phase, value)   phases: armed, engaged, control, committed, cancelled
            ▼
        main.py Runtime         thin: SignEvent → HUD feedback + Intent; no per-sign branches with flashes
            │
            ▼
        pipeline.py             unchanged: idempotency, pause, fusion with voice, conflicts
            │
            ▼
        executor (main.py)      level.set intents → control/levels (set + read-back), window/app/browser as today
        control/system.py       + volume(), set_volume(), brightness(), set_brightness() on win32 and x11
            │
            ▼
        hud.py                  + LevelWidget (bar: base, target, actual) + ArmIndicator (arc) + StrokeTrail (exists)
        eventlog.py             every SignEvent and every read-back logged; CAMEO_TRACE also records landmarks
```

### 3.1 The sign spec

```python
SignSpec(
    name='volume-line',
    arm=Pose.family('two'),                     # index + middle up (thumb in or out)
    engage=Stroke(axis='x', length=0.6),        # travelled 0.6 palm widths mostly along x (slant ≤ 0.5, bend ≤ 0.2)
    control=Level('volume', gain=0.25),         # target = base + 0.25 per palm width of x displacement, clamped 0..1
    commit=[PoseReleased(), Still(0.5)],        # let go, or hold still half a second: value frozen
    cancel=[Lost(within=0.3), Pose('fist')],    # revert to base
    cooldown=0.4, priority=20, freezes_pointer=True,
)
```

The machine evaluates specs on every frame in priority order. A spec has no code of its own; new signs are new specs,
and the tests for the machine cover every spec the same way.

### 3.2 The levels backend (verified on this laptop today)

| Quantity | Windows | Linux (X11) | Read-back |
| --- | --- | --- | --- |
| Volume | `IAudioEndpointVolume` through comtypes (no pycaw needed): `GetMasterVolumeLevelScalar`, `SetMasterVolumeLevelScalar`, `GetMute`, `GetVolumeStepInfo` (this laptop: 51 steps, 12 ms) | `wpctl get-volume @DEFAULT_AUDIO_SINK@` / `wpctl set-volume` (PipeWire), `pactl` fallback | value within one step, 100 ms |
| Brightness | WMI `WmiMonitorBrightness` / `WmiSetBrightness` (25 ms, built-in panel only) | `brightnessctl`, `xbacklight`, `light`; `/sys/class/backlight` for reading | value moved the right way, 1 s |
| Window rectangle | Win32 (exists) | EWMH (exists) | geometry read-back (exists) |
| Zoom, scroll | wheel (exists) | XTest wheel (exists) | none: reported as `sent` |

New contract actions: `volume.set {target}`, `display.brightness_set {target}`, keeping `volume.up/down` and
`display.brightness_up/down` for the rail buttons and voice (they become "set to current ± one step").

### 3.3 Feedback contract

| Phase | Overlay | Rail |
| --- | --- | --- |
| ARMED | sign name at the fingertip, arc filling toward engage | chip: sign name |
| ENGAGED / CONTROLLING | for levels: horizontal bar, base tick, target marker, actual fill, "42 %"; for strokes: clean shape (exists); for holds: countdown arc | chip: value |
| COMMITTED | toast with the read-back ("Volume 42 % ✓") | log row, green |
| CANCELLED | toast with the reason ("cancelled: hand lost") | log row, dim |
| refused by a guard | "mouse in use", "paused", "low light" (exists) | chip, amber |

---

## 4. Every sign, specified

Distances in palm widths (pw), times in seconds. "Still" = palm centre travel under 0.15 pw over the window.

| Sign | Arm | Engage | Control (measured) | Commit | Cancel | Restriction |
| --- | --- | --- | --- | --- | --- | --- |
| Pointer | point / zoom pose | immediately | pointer = One Euro of index tip through the margin map (exists) | pose leaves | — | mouse priority 1.5 s (exists) |
| Left click | point ≥ 0.25 | middle rises (`two`) | click point = index position at the frame *before* the rise | back to point within 0.45 | > 0.45 or other pose | one click per flick; two flicks = double |
| Right click | `two` still | held 0.6 with travel < 0.3 pw | — | at 0.6 | travel ≥ 0.3 pw (it is a stroke now) | cooldown 0.5 |
| Open (pinch tap) | point, pinch open | pinch closes < 0.33 from > 0.45 within 0.4, still | — | pinch opens again within 1.0 | > 1.0 (becomes wrist dial) or moved > 0.3 pw | palm speed < 0.15 pw/s at close |
| **Volume, line** | `two` | stroke 0.6 pw along x, slant ≤ 0.5, bend ≤ 0.2, turn ≤ 35° | `target = base + 0.25 × Δx(pw)`, clamped 0..1, set when target moves ≥ 1 device step; axis locked at engage; moving back lowers it | pose released, or still 0.5 | lost within 0.3 → revert; fist → revert | pointer frozen; max 10 sets / s |
| **Brightness, line** | `two` | stroke 0.6 pw along y | `target = base + 40 % × Δy(pw)`, clamped 0..100 | same | same | same |
| Volume, wrist dial | point, pinch held ≥ 1.0 still | first 8° of roll | `target = base + Δroll / 180°` (a half turn spans the range) | pinch opens | lost within 0.3 → revert | pointer frozen |
| Circle → Edge | `two` | turn ≥ 270° with least-squares fit radius ≥ 0.6 pw and residual ≤ 20 % of radius | direction = sign of turn | at closure | stroke straightens (becomes a line) | cooldown 1.5 |
| Scroll | `four` still 0.3 | palm moves ≥ 0.15 pw from anchor | rate table by offset: 0.15–0.5 pw → 2–6 notches/s, 0.5–1.0 → 6–14 | pose leaves | — | never after an open palm (dial) |
| Zoom | `zoom` still 0.5 | pinch changes ≥ 0.3 for 3 frames | one notch per 0.3 of pinch, base re-anchored each notch | pose leaves | palm travels > 0.2 pw (it was pointing) | reported `sent` |
| Minimize | palm 0.45 (arms window under pointer) | fist | — | fist held 0.5 within 4 s of the palm | other pose | verified by window state (exists) |
| Restore | fist (not the one that minimized) | palm within 1.5 | — | palm held 0.45 | — | verified (exists) |
| Grab / drag / drop | `three` open ≥ 0.12 or `thumb` 0.35 | fist / closed | carry position = palm centre; window moves by handle, files by real drag | fingers open | hand lost 0.3 → drop 'lost' (device carry) | button always released (exists) |
| Sleep | `pinky` still | held 1.0: countdown shown | — | held 1.5 | any movement > 0.2 pw or other pose | two-stage on purpose: sleep is costly |
| Screenshot (clap) | two hands, gap > 2.0 | gap < 1.0 within 0.6 | — | at touch | — | cooldown 1.5; other signs quiet 0.8 |
| Pause / resume | Ctrl+Alt+P (exists) | — | — | — | — | — |

Numbers in bold rows are the proposed gains; everything in the table is a starting point to be tuned against the
trace corpus (section 6), not a final value.

---

## 5. Why this fixes the volume complaint specifically

1. **Ratio.** 1 palm width of stroke = 25 % of the volume range, at any distance from the camera, because the stroke
   is measured in palm widths and the level is absolute.
2. **Synchronised.** The bar shows base, target and the real read-back as the hand moves. The final toast shows the
   value the device actually reports.
3. **Destination.** The line ends when the fingers release or the hand holds still for half a second. A slow drift
   after that changes nothing. Lost hand early = revert; fist = revert. There is no state in which the value keeps
   moving without the hand moving.

The same three properties apply to brightness, the wrist dial, scroll and zoom by construction, since they are
properties of the machine, not of one spec.

---

## 6. Deciding by data: the trace corpus and replay tool

- `CAMEO_TRACE=1` already writes a per-frame trace. Extend it to include the 21 mirrored landmarks and the world
  landmarks, so a session can be replayed without a camera.
- `tools/replay.py <session>`: feeds the recorded frames through the current pose classifier and sign machine, prints
  every SignEvent with timestamps, and compares against a label file (`<session>.labels.jsonl`: what the user meant
  to do and when, written by hand once per recorded session, 20–40 lines).
- Report per sign: intended / fired / misfired / missed, and the lag from intention to commit.
- Acceptance for any threshold change: no sign's misfire count rises on the corpus.
- Start the corpus with 6 recordings: bright room, dim room, near (40 cm), far (90 cm), left hand, second hand in view.

---

## 7. Migration plan

Each phase leaves the app working; each has an acceptance test. Order chosen so the visible complaint (levels) is
fixed first and the risky refactor (moving the other signs) comes last.

| Phase | Work | Acceptance |
| --- | --- | --- |
| 0. Corpus | landmark recording in the trace, `tools/replay.py`, 6 labelled recordings | replay reproduces today's live events exactly |
| 1. Levels | `volume()` / `set_volume()` on win32 (COM) and x11 (wpctl/pactl); `volume.set`, `display.brightness_set` intents; executor read-back; `LevelWidget` on the HUD; `doctor` reports level support | set 0.42 → read 0.42 ± 1 step within 100 ms, on both OSes (Linux in the Docker rig) |
| 2. Machine | `gestures/signs.py` with SignMachine, focus, cooldowns; `strokes.py` in palm widths; specs for volume-line, brightness-line, wrist dial; `classic.py` delegates those three to the machine | synthetic traces assert exact values (e.g. 0.8 pw stroke from 16 % → 36 %); corpus misfires ≤ today |
| 3. Migrate | move click, right click, open, circle, scroll, zoom, minimize/restore, grab, sleep, clap to specs, one per change; delete the flag fields as they empty; `PoseClassifier` moves to `pose.py` | after each sign: its old tests pass unchanged against the machine; corpus unchanged or better |
| 4. Poses | if the corpus still shows pose misreads, train a MediaPipe Model Maker gesture classifier on the corpus landmarks (it trains only a small classifier on the 21 points; the landmark model stays) and keep the hysteresis in front of it | pose misreads on the corpus drop; latency unchanged |
| 5. Docs | GESTURES.md rewritten from the spec table (generated, so it cannot drift); OPERATOR notes for tuning by replay | — |

Estimated size: phases 0–2 about 900 lines including tests; phase 3 removes more than it adds.

---

## 8. Decisions to make before phase 2

1. **Gains**: 25 % per palm width for volume, 40 % per palm width for brightness, a half turn for the wrist dial.
2. **Cancel semantics**: revert to base on early loss and on a fist; keep the value on any other release.
3. **Keep both volume signs** (line and wrist dial) or one? Two ways to do one thing doubles the misfire surface.
4. **Sleep** stays two-stage (1.0 s arm with countdown, 1.5 s commit) or moves to voice only.
5. **Linux volume backend**: `wpctl` first (PipeWire, Ubuntu 22.10+), `pactl` fallback.

---

## References

- MediaPipe Gesture Recognizer and its Model Maker customisation (classifier on the 21 landmarks):
  https://ai.google.dev/edge/mediapipe/solutions/vision/gesture_recognizer and
  https://ai.google.dev/edge/mediapipe/solutions/customization/gesture_recognizer
- Stroke recognisers ($1 unistroke, $P point cloud, $Q) for shape classification by resampled template distance:
  https://depts.washington.edu/acelab/proj/dollar/index.html and https://dl.acm.org/doi/10.1145/2388676.2388732
- Windows master volume with read-back: `IAudioEndpointVolume::SetMasterVolumeLevelScalar`
  https://learn.microsoft.com/en-us/windows/win32/api/endpointvolume/nf-endpointvolume-iaudioendpointvolume-setmastervolumelevelscalar
- One Euro filter (already used for the pointer and, since today, for all landmarks): Casiez, Roussel, Vogel, CHI 2012.
