# L-ONE WiFi Control

Wireless control for the **Bambu Lab CyberBrick L-ONE** robotic arm, replacing the official remote/app — plus a computer-vision layer that gives the arm the position feedback its hardware doesn't have.

> 🇵🇹 Plain-language walkthrough of the whole system (in Portuguese): [`GUIA.md`](GUIA.md)

## How it works

```
[phone / PC]  --WiFi: HTTP + WebSocket-->  [ESP32-S3]  --ESP-NOW-->  [ESP32-C3]  --PWM-->  servos + motor
   browser                                  "controller"              "receiver"            (the arm)
```

- **ESP32-S3 (controller)** — runs the `larm` MicroPython app: serves the web UI, a real-time WebSocket channel and an HTTP API, and streams setpoints to the arm at ~30 Hz.
- **ESP32-C3 (receiver, inside the arm's base)** — only listens to ESP-NOW and drives the actuators. It never needs to know any WiFi network.
- **Automatic channel discovery** — the S3 announces its radio channel in a beacon and the C3 finds it on its own, so a router changing channels no longer breaks the link.

### The catch: no sensors

| Actuator | Type | Controlled by |
|---|---|---|
| Joint 1, Joint 2 | continuous servos | **speed**, not position |
| Claw | positional servo | angle |
| Base (yaw) | DC motor | speed |

Only the claw knows where it is. Everything else is driven by *speed × time*, which shaped most of the design:

- **Dead-reckoning poses** — the S3 integrates commanded speed over time to estimate each axis; poses and animations are built on that estimate, with a "home sync" to re-anchor all poses at once when it drifts.
- **Learned directional gain** — a back-and-forth calibration learns per-axis asymmetry (lifting costs more than lowering), and moves always finish in the same direction with a slow-down near the target to cancel gear backlash.
- **Phase 2 — vision** (`host/vision.py`): a camera sees ArUco markers on the claw and the target, and the PC closes the loop through the HTTP API (point the base at an object, or reach it with base + joints).

## Safety, in layers

Nothing is ever left spinning on its own:

- Every continuous command is **time-boxed** (default 1.5 s, hard cap 3 s).
- Releasing a button, closing the browser or losing the WebSocket → setpoint returns to neutral.
- **600 ms watchdog on the C3**: no radio traffic while something is moving → it stops by itself (covers a lost STOP packet or a dead S3).
- Conservative mechanical limits are clamped on the C3, the last stop before the hardware.

## Web UI

Three tabs — **Control / Poses / Animations** — with STOP ALL and HOME always visible.

- Hold-to-move per axis, claw open/close + angle slider, speed presets.
- Save, edit and replay named poses; build animations as `pose → pause → pose …`.
- Keyboard on desktop (←/→ base, q/a and w/s joints, e/d claw, space = stop); hiding the tab stops everything.
- Connection pill shows WebSocket status, radio channel and round-trip time.

## HTTP API (excerpt)

| Endpoint | |
|---|---|
| `GET /app/larm/move?idx=&angle=&ms=` | move a servo (claw = angle, continuous = speed, `ms` = fixed nudge) |
| `GET /app/larm/motor?idx=&pct=&ms=` | base motor, −100..100 |
| `GET /app/larm/stopall` | stop joints + base |
| `GET /app/larm/state` | estimated position, home, poses, running animation |
| `GET /app/larm/posesave?name=` · `posego` · `posedel` | poses |
| `POST /app/larm/seq` | run an animation |
| `POST /app/larm/goto` | direct target `{"s1":…,"s2":…,"m1":…,"p3":…}` (used by vision) |

Full list in [`GUIA.md`](GUIA.md); ready-made Postman collection in `host/larm.postman_collection.json`.

## Repository layout

```
s3/apps/larm.py        controller app (UI + WebSocket + ESP-NOW stream)
s3/main.py             S3 boot (registers larm, starts stream/beacon)
c3/main.py             ESP-NOW receiver with watchdog + automatic channel
c3/config.py           mechanical limits and neutral points
c3/bbl/                servo/motor drivers
c3/c3_*.py             deploy/debug helpers for the C3
host/vision.py         phase 2: ArUco vision + autonomous control
host/make_markers_pdf.py   printable A4 ArUco marker sheet
host/larm.py           Python client for the API
docs/                  previous architecture (archived)
```

## Vision quick start

```bash
python host/make_markers_pdf.py          # print at 100% — ID 0 on the claw, ID 1 on the object
python host/vision.py --src 0            # preview what the camera recognises (does not move the arm)
python host/vision.py --point --src 0    # rotate the base to point at the object
python host/vision.py --reach --src 0    # base + joints until the claw overlaps the object
```

Ctrl-C always stops the arm on exit.

## Stack

MicroPython · ESP32-S3 / ESP32-C3 · ESP-NOW · WebSocket · OpenCV (ArUco)

## License

[MIT](LICENSE)
