# 🚽 FlushTracker - 2026 Fidget Camp Showcase

An interactive, real-time wastewater tracking system created for the **2026 Fidget Camp Showcase**.

Press the physical button (mounted on a toilet) → a Raspberry Pi creates a flush record in Firebase Cloud Firestore, prints a 58mm thermal receipt with a scannable QR code, and visitors follow their deposit live as it journeys from 1417 15th St through the SFPUC collection system, the Southeast Treatment Plant (SEP), and out the Pier 80 deepwater outfall into San Francisco Bay.

Live Web App: **[https://flushbase.web.app](https://flushbase.web.app)**
Live Dashboard: **[https://flushbase.web.app/dashboard.html](https://flushbase.web.app/dashboard.html)**

---

## 🗺️ How the Pieces Fit Together

```
[Button on GPIO 17] → pi_button.py (systemd service on the Pi)
                          ├─→ writes flush doc to Firestore ("flushes" collection)
                          ├─→ falls back to local_flushes.json if Firebase unconfigured
                          └─→ prints ESC/POS receipt + QR to /dev/usb/lp0

[Visitor scans QR] → flushtracker.html?id=FLUSH-XXXXXX
                          └─→ live Firestore listener + animated map journey

[You, watching]    → dashboard.html
                          └─→ real-time feed of all flushes + browser notifications
```

### Project Layout
| File | Purpose |
| :--- | :--- |
| `pi_button.py` | Pi button listener, Firestore writer, receipt/QR printer |
| `flushtracker.html` | **Source** for the tracker web app |
| `dashboard.html` | **Source** for the live flush dashboard |
| `toilet.png` | Logo — used on the web pages AND printed on receipts (must be on the Pi too) |
| `public/` | What Firebase Hosting actually deploys — generated, don't edit by hand |
| `sync-public.sh` | Copies the source files into `public/`; runs automatically before every deploy |
| `firebase_config.js` | Firebase web credentials (shared by both pages) |
| `firestore.rules` | Public read/create, no update/delete (flush records are immutable) |
| `local_flushes.json` | Local fallback DB when Firebase isn't configured |
| `receipt_counter.json` | Persistent "satisfied customer #N" counter (lives on the Pi) |

> **Editing the web pages:** edit `flushtracker.html` and `dashboard.html` in the
> repo root. `public/` holds generated copies (`public/index.html` *is*
> `flushtracker.html`) and `sync-public.sh` refreshes them. It's registered as a
> hosting `predeploy` hook in `firebase.json`, so a plain
> ```bash
> firebase deploy --only hosting
> ```
> re-syncs first and can't ship a stale page. Run `./sync-public.sh` yourself if
> you want the copies updated without deploying.

---

## 🛠️ Raspberry Pi Setup (from scratch)

### 1. System packages
```bash
sudo apt update
sudo apt install -y python3-gpiozero python3-lgpio liblgpio-dev
```
`liblgpio-dev` matters: without it, `pip install lgpio` fails with
`/usr/bin/ld: cannot find -llgpio`.

### 2. Clone & install Python dependencies
```bash
git clone https://github.com/akauffm/fidget_flushbase.git
cd fidget_flushbase
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
`gpiozero` needs the `lgpio` pin-driver backend (both are in requirements.txt).
If you ever see startup errors like `No module named 'lgpio'` / `'RPi'` /
`'pigpio'`, that's gpiozero failing to find a backend — install `lgpio` into
whatever Python is running the script.

### 3. Printer permissions
The receipt printer appears as `/dev/usb/lp0` and is owned by the `lp` group:
```bash
sudo usermod -a -G lp $USER
```
(Then restart the service / re-login. Avoid `chmod 666` — it resets when the
printer re-enumerates.)

### 4. Make sure `toilet.png` is next to `pi_button.py`
It's printed at the top of every receipt. If missing, the receipt still prints,
just without the graphic.

---

## 🔌 Hardware Wiring (Physical Flush Button)

Uses the Pi's internal pull-up — no external resistors.

| Button Terminal | Raspberry Pi Connection | Pin Header Location |
| :--- | :--- | :--- |
| **Wire 1** (Signal) | **GPIO 17** | **Physical Pin 11** |
| **Wire 2** (Ground) | **GND** | **Physical Pin 9** (or Pin 14 / Pin 6) |

```text
         3.3V  [01]  [02]  5V
    GPIO2/SDA  [03]  [04]  5V
    GPIO3/SCL  [05]  [06]  GND
        GPIO4  [07]  [08]  GPIO14/TXD
          GND  [09]  [10]  GPIO15/RXD  <-- Ground Connection
 (WIRE 1) GPIO17 [11]  [12]  GPIO18
       GPIO27  [13]  [14]  GND         <-- Alternative Ground
```

**Normally-open vs normally-closed doesn't matter.** At startup the script
samples the button's resting state and fires a flush on any change *away* from
rest (check `journalctl` for the `[i] Button wiring detected: ...` line). Two
consequences:
- Don't hold the button down while the service starts — whatever state exists
  at boot *is* "resting". Restart the service if that happens.
- A `FLUSH_COOLDOWN_SECONDS = 5` guard in `pi_button.py` drops rapid repeat
  events so a bouncy switch can't spool out receipts.

---

## 🚀 Running It

### As a service (production — starts on boot, restarts on crash)
Create `/etc/systemd/system/flushtracker.service`. Adjust the paths and `User=`
to your actual username (`whoami`) — a wrong user fails with `status=217/USER`:

```ini
[Unit]
Description=FlushTracker Button Listener
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/home/admin/Desktop/Pyth/venv/bin/python /home/admin/Desktop/Pyth/pi_button.py --pi
WorkingDirectory=/home/admin/Desktop/Pyth
User=admin
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Notes on the non-obvious lines:
- `ExecStart` must use the **venv's** python, or the venv-installed packages
  (lgpio, qrcode, requests) won't be found.
- `--pi` forces GPIO mode so the service can never fall into the interactive
  keyboard mode (which exits immediately under systemd — no terminal).
- `Environment=PYTHONUNBUFFERED=1` — without it, Python buffers `print` output
  ~8KB at a time and `journalctl` appears mysteriously silent.
- Every `Key=Value` line must sit *below* a `[Section]` header, or systemd
  ignores it with "Assignment outside of section".

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now flushtracker

systemctl status flushtracker          # should be "active (running)"
journalctl -u flushtracker -f          # live logs; press the button and watch
```

After changing `pi_button.py`: `sudo systemctl restart flushtracker`
(plus `daemon-reload` first if the `.service` file itself changed).

### Manually (debugging)
The service holds GPIO 17 — a manual run will fail with **"GPIO busy"** until
you stop it:
```bash
sudo systemctl stop flushtracker
python pi_button.py            # full listener
python pi_button.py --test     # wiring diagnostic: prints raw pressed/released
sudo systemctl start flushtracker
```
On a laptop (no Pi hardware), it runs in workstation mode: press ENTER to
simulate a button push. Force modes with `--pi` / `--workstation`.

### Deploying script changes from the laptop
```bash
scp pi_button.py toilet.png admin@pi3.local:/home/admin/Desktop/Pyth/
ssh admin@pi3.local sudo systemctl restart flushtracker
```

---

## 🖨️ Receipt Tuning (top of `pi_button.py`)

| Constant | Meaning |
| :--- | :--- |
| `RECEIPT_WIDTH = 30` | Text columns. 58mm printers fit ~30-32 at Font A; if dividers wrap to two lines, reduce it. Centered lines auto-adjust. |
| `FLUSH_COOLDOWN_SECONDS = 5` | Minimum seconds between flushes |
| `BUTTON_GPIO_PIN = 17` | Button GPIO (BCM numbering) |
| `FIREBASE_PROJECT_ID` / `PUBLIC_HOST_URL` | Where flush records go / QR target URL |

The toilet graphic prints 240px wide (the `target_width=240` passed to
`pil_image_to_escpos_raster` in `print_receipt`; the QR code uses 256). Widths
are clamped to the source image's own width and rounded down to a multiple of
8, so with the 400x400 `toilet.png` anything up to ~380 works on 58mm paper.
Receipt layout preview without a printer: run in workstation mode and press
ENTER — the receipt text and an ASCII QR render in the terminal, and a copy is
saved to `last_receipt.txt`.

---

## 🌐 Web App

Two pages, both talking to the same Firestore `flushes` collection:

- **Tracker** (`flushtracker.html` → deployed as `index.html`): the visitor
  page from the receipt QR (`?id=FLUSH-XXXXXX`). Animates the flush along the
  real sewer route in real time. Timing is physics-derived — distances measured
  from the map route, ~2.5 ft/s in the house lateral, **8 ft/s in the mains**
  (≈35 min to SEP), then a 24-hour treatment cycle. Tune via the
  `MAINS_FT_PER_SEC` constants in the script. Also contains the accelerated
  liquid/solid simulator. Real flushes are type-agnostic ("1.6 G Flush") since
  the button only senses the flush.
- **Dashboard** (`dashboard.html`): live feed of every flush with stats and
  journey stage. "Enable Notifications" uses the browser Notification API —
  fires whenever a flush lands *while the page is open* (background tab OK; no
  backend needed). True closed-browser push would require FCM + Cloud
  Functions (Blaze plan) — intentionally not built.

There is no build step. Both pages load Tailwind, FontAwesome, Leaflet and the
Firebase SDK (compat build, pinned at 10.7.1) directly from CDNs via script
tags, and nothing is installed from npm at runtime — `package.json` carries
deploy scripts only.

Deploy: `firebase deploy --only hosting` (the predeploy hook syncs `public/`
for you).

Firestore layout — `flushes/{FLUSH-XXXXXX}`:
`timestamp` (ms epoch, int) · `formatted_time` (string) · `gpf` (1.6) ·
`waste_type` ("unknown") · `origin` (string). Rules allow public read/create,
never update/delete. The Pi writes via the Firestore REST API (no service
account needed).

---

## 🔧 Troubleshooting Quick Reference

| Symptom | Cause / Fix |
| :--- | :--- |
| `No module named 'lgpio'/'RPi'/'pigpio'` at startup | gpiozero has no pin backend — `pip install lgpio` (in the venv) or `sudo apt install python3-lgpio` |
| `pip install lgpio` fails: `cannot find -llgpio` | `sudo apt install liblgpio-dev` first |
| `'GPIO busy'` when running manually | The systemd service already owns the pin — `sudo systemctl stop flushtracker` first |
| Service fails, `status=217/USER` | `User=` in the unit doesn't exist on this Pi — set your real username |
| journal: "Assignment outside of section" | A `Key=Value` line is above its `[Section]` header in the .service file |
| Service runs but journal shows nothing | Missing `Environment=PYTHONUNBUFFERED=1`, or old code — check `grep "wiring detected" pi_button.py` on the Pi |
| `Permission denied accessing /dev/usb/lp0` | `sudo usermod -a -G lp <user>` then restart the service |
| Button fires on release instead of press | Expected with normally-closed wiring — the auto-detect handles it; check the "Button wiring detected" journal line |
| Receipt prints a solid black square instead of logo | `toilet.png` lost its transparency handling — the converter flattens alpha onto white; make sure you deployed the current `pi_button.py` |
| Web changes don't show up after deploy | You edited `flushtracker.html`/`dashboard.html` but didn't copy into `public/` before `firebase deploy` |

---

## 📄 License & Credits
Created for the **2026 Fidget Camp Showcase**.
*Remember: Only Flush the 3 P's: Poop, Pee, and Paper!*
