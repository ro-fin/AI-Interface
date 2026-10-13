# Hand-sign workflow

How one camera frame becomes a desktop action (Windows or Linux X11) in the Cameo runtime (`cameo/`).

```
Camera (30 fps, MJPG 640x480)
  -> grabber thread keeps only the NEWEST frame (no stale driver queue = no growing lag)
  -> dark frame (mean brightness under 80)? gamma-lifted first; the HUD says "low light"
  -> MediaPipe Hand Landmarker, VIDEO mode, up to 2 hands, ~25 fps (detect .5, keep tracking down to .4)
  -> landmark protocol (HandTracker): a one-frame size jump over 45 % is a misdetection and skipped; all 21 points
     One Euro smoothed (steady when still, no lag when moving); a miss under 0.3 s keeps the finger states
       image landmarks (21 points)  -> pointer position
       world landmarks (21 points, metres, 3-D) -> finger shape
  -> PoseClassifier: finger up/down with hysteresis -> pose label
  -> GestureEngine: 2-frame debounce, 0.3 s dropout tolerance, timed rules -> events; distances in palm widths
     SignMachine (signs.py): the measured signs (level lines, circle, sleep): one lifecycle, one sign in control at a time
  -> Runtime: guarded input through control/system.py (SendInput on Windows, XTest on Linux; pointer, clicks, window actions)
     Levels (levels.py): volume / brightness set to base + measured change, rate limited, every set read back
  -> HUD: skeleton and sign name on the overlay; last action or the reason it was refused on the rail
```

## Poses

| Pose | Hand shape |
| --- | --- |
| point | Index up; middle, ring, little folded; thumb folded |
| zoom | Index up with thumb out (still moves the pointer until a real pinch change), or thumb and a partly straight index pinched |
| two | Index and a clearly raised middle finger up; ring and little folded |
| palm | All four fingers and thumb out |
| four | Four fingers up, thumb folded |
| three | Thumb, index and middle out (grab) |
| fist | All fingers folded; thumb folded or only a little out (a loose fist) |
| thumb | Four fingers folded, thumb held clearly out (straighter and farther than a loose fist's) |
| neutral | Anything else (no action) |

Finger states use two thresholds (on above 160 degrees, off below 145 degrees, with a matching tip-distance ratio), so a finger near the limit keeps its previous state instead of flickering. Angles come from MediaPipe's 3-D world landmarks, so tilting the hand toward the camera does not change them.

## Signs and timing

Generated from the code (`python -m cameo signs`; `tests/test_catalog.py` fails if this table and the code differ).
pw = palm width, about 8 cm: distances mean the same near the camera and far from it.

<!-- signs:begin -->
| Sign | Rule | Action | Checked by |
| --- | --- | --- | --- |
| Point | every frame | Move the pointer (One Euro filtered); held still while a measured sign is in control | guarded input |
| Point, flick the middle finger up and down | point ≥ 0.25 s, middle up < 0.45 s | Left click where the index pointed before the flick; two flicks = double-click | sent (no read-back exists) |
| Hold index + middle still | 0.6 s, travel < 0.3 pw (arc fills) | Right click where the index pointed | sent (no read-back exists) |
| Point, tap the thumb on the index | pinch closes below 0.33 and opens above 0.45 within 1 s, palm travel < 0.4 pw | Open what is under the pointer (double-click; taskbar buttons single) | sent (no read-back exists) |
| Open palm, then fist | palm 0.45 s, fist 0.5 s (arc fills), within 4 s | Minimize the window under the pointer | read back |
| Fist, then open palm | palm within 1.5 s of a new fist, held 0.45 s | Restore the window last minimized by sign | read back |
| Thumb + index, pinch or spread | settle 0.5 s, hand still (< 0.2 pw), change ≥ 0.3 | Zoom in / out (Ctrl+wheel) under the pointer | sent (no read-back exists) |
| Four fingers, then move the hand up / down | four fingers 0.3 s; free play 0.15 pw | Scroll under the pointer: 2/s at 0.15 pw, 6/s at 0.5 pw, 14/s at 1 pw (exact at any frame rate) | sent (no read-back exists) |
| Index + middle up, draw a straight line right / left | 0.6 pw along the horizontal, drift ≤ 0.5 of the length, turning ≤ 35°; then continuous. Ends: fingers released or still 0.5 s = keep; fist, or hand lost within 0.3 s of starting = back to where it was | Volume up / down: 25 % per palm width from the level at the start, both ways; bar shows start, target, actual | read back |
| Index + middle up, draw a straight line up / down | 0.6 pw along the vertical, drift ≤ 0.5 of the length, turning ≤ 35°; then continuous. Ends: fingers released or still 0.5 s = keep; fist, or hand lost within 0.3 s of starting = back to where it was | Brightness up / down: 40 % per palm width from the level at the start, both ways; bar shows start, target, actual | read back |
| Index + middle up, draw a circle | turn ≥ 270°, fitted radius ≥ 0.35 pw, RMS off the circle ≤ 20 % of the radius | Clockwise opens a new Edge window, anticlockwise closes the ones Cameo opened; once per gesture, 1.5 s apart | read back |
| Thumb + index + middle out, then close to a fist | open ≥ 0.12 s | Grab the window or icon under the pointer; open the fingers to drop | read back |
| Four fingers closed, thumb out; open palm drops | thumb sign 0.35 s (arc fills) | Grab and drop here or on a paired device | read back |
| Grab, then move the closed hand out of view | hand gone 0.3 s while closed | Carry it to a paired device | read back |
| Little finger alone, held still | countdown from 1 s, sleep at 1.5 s; moving > 0.2 pw or another pose cancels | The computer sleeps | sent (no read-back exists) |
| Clap (two hands meet) | apart > 2 hand sizes, then touching within 0.6 s | Screenshot to Pictures/Screenshots; other signs wait 0.8 s | read back |
<!-- signs:end -->

Measured signs (the lines, the circle, sleep) run in one sign machine (`gestures/signs.py`): each has a start, a value
and a named end, and while one is in control nothing else acts and the pointer holds still. Holds show a filling arc at
the fingertip; a level line shows a bar with the level at the start, the target and the level the device reports.
The full design is in `docs/SIGN-PROTOCOL-PLAN.md`.

While index + middle draw, the HUD shows the fingertip path in cyan. Once the drawing is recognised it is shown clean, in green: a
circle as a true circle (least-squares fit on screen pixels, so it is round on any screen), a line as a straight vertical or
horizontal line that follows the fingers while the line goes on. The clean shape stays up for a second.

## Stability rules

- A new pose counts only after two consecutive frames agree.
- Losing the hand for under 0.3 s (a detection gap) keeps holds, clicks and minimize arming alive; longer resets everything.
- One action per hold: a sign fires once until the pose changes.
- Moving the physical mouse pauses gestures for 1.5 s. Held mouse buttons or modifier keys block injected input.
- Ctrl+Alt+P pauses and resumes; Ctrl+Alt+Q quits (camera and microphone are released).

## Tuning

- **Hand area**: how much of the camera frame maps to the whole screen. Smaller = less hand movement, more jitter.
- **Pointer smoothing**: One Euro cutoff from 4 Hz (responsive) to 1 Hz (steady). Beta 0.02 lets fast movement through without lag.
- Sit 50-80 cm from the camera, hand at chest height, away from the face, with light on the hand.

## Tests

`tests/test_classic_signs.py` (engine and classifier rules), `tests/test_shortcut_signs.py` (circle, pinch key, clap, sleep),
`tests/test_sign_machine.py` (level lines: ratio, ends, focus), `tests/test_levels.py` (set and read-back with a fake device),
`tests/test_hand_tracker.py` (landmark protocol), `tests/test_replay.py` (record and replay), `tests/test_catalog.py` (this table), `tests/test_classic_runtime.py` (synthetic frames through the full runtime to real input on a test window, on Windows and Linux).
