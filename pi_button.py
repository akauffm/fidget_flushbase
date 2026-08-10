#!/usr/bin/env python3
"""
Raspberry Pi Flush Tracker Trigger & Receipt Generator
------------------------------------------------------
Listens for a physical button press on GPIO 17 (or interactive ENTER key on PC/Mac),
creates a unique flush record in Firebase Cloud Firestore, generates a printable QR code,
and formats an ESC/POS receipt for thermal printing.
"""

import os
import sys
import time
import random
import datetime
import json
import subprocess
import requests

# Try importing qrcode and PIL Image for QR generation
try:
    import qrcode
    from PIL import Image
    QRCODE_AVAILABLE = True
except ImportError:
    qrcode = None
    Image = None
    QRCODE_AVAILABLE = False

# Configuration Settings
FIREBASE_PROJECT_ID = "flushbase"  # Replace with your Firebase Project ID
PUBLIC_HOST_URL = "https://flushbase.web.app" # Replace with your deployed app URL (or http://<pi-ip>:8000)
BUTTON_GPIO_PIN = 17 # GPIO pin connected to button (active low with internal pull-up)
FLUSH_COOLDOWN_SECONDS = 5 # Minimum seconds between flushes (guards against bouncy/miswired buttons)
RECEIPT_WIDTH = 30 # Receipt text width in characters (58mm printers typically fit 30-32 at Font A)

# Network behaviour. Both numbers are deliberately small: the button callback is
# synchronous, so every second spent retrying is a second the receipt is not printing.
FIRESTORE_TIMEOUT_SECONDS = 4 # Per-attempt HTTP timeout
FIRESTORE_ATTEMPTS = 2        # Attempts before giving up and queueing the flush locally
PENDING_QUEUE_LIMIT = 50      # Max queued flushes to re-send after connectivity returns

# Fixed fields on every flush record (the button cannot sense what was flushed)
FLUSH_GPF = 1.6
FLUSH_WASTE_TYPE = "unknown"
FLUSH_ORIGIN = "1417 15th St, San Francisco, CA"

def is_pi_hardware():
    """Detects Raspberry Pi hardware from system device tree or cpuinfo."""
    if os.path.exists('/proc/device-tree/model') or os.path.exists('/sys/firmware/devicetree/base/model'):
        return True
    try:
        with open('/proc/cpuinfo', 'r') as f:
            content = f.read()
            if 'Raspberry Pi' in content or 'BCM' in content or 'Hardware\t: BCM' in content:
                return True
    except Exception:
        pass
    return False

# Explicit mode overrides via command-line flags
FORCE_PI_MODE = "--pi" in sys.argv
FORCE_WORKSTATION_MODE = "--workstation" in sys.argv or "--dev" in sys.argv

IS_RASPBERRY_PI = (FORCE_PI_MODE or is_pi_hardware()) and not FORCE_WORKSTATION_MODE

def generate_flush_id():
    """Generates a unique, short tracking ID like FLUSH-4E9A12"""
    hex_code = ''.join(random.choices('0123456789ABCDEF', k=6))
    return f"FLUSH-{hex_code}"

def data_path(filename):
    """Absolute path to a data file next to this script (works from any cwd)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def read_json(path, default):
    """Reads a JSON file, returning `default` if it is missing or corrupt."""
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f" [!] Could not read {os.path.basename(path)} ({e}); starting fresh.")
        return default


def write_json(path, data):
    """Writes a JSON file, reporting rather than raising on failure."""
    try:
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f" [!] Could not write {os.path.basename(path)}: {e}")
        return False


def build_flush_record(flush_id, timestamp_ms, formatted_time):
    """The canonical flush record — one shape for Firestore, the local log and the queue."""
    return {
        "id": flush_id,
        "timestamp": timestamp_ms,
        "formatted_time": formatted_time,
        "gpf": FLUSH_GPF,
        "waste_type": FLUSH_WASTE_TYPE,
        "origin": FLUSH_ORIGIN,
    }


def firestore_payload(record):
    """Maps a flush record onto the Firestore REST API's typed-value format."""
    return {
        "fields": {
            "timestamp": {"integerValue": str(record["timestamp"])},
            "formatted_time": {"stringValue": record["formatted_time"]},
            "gpf": {"doubleValue": record["gpf"]},
            "waste_type": {"stringValue": record["waste_type"]},
            "origin": {"stringValue": record["origin"]},
        }
    }


def push_to_firestore(record, attempts=FIRESTORE_ATTEMPTS, verbose=True):
    """Writes one flush document to Cloud Firestore via the REST API. Returns True on success."""
    if FIREBASE_PROJECT_ID == "YOUR_PROJECT_ID":
        if verbose:
            print(" [NOTE] Firebase Project ID not configured — keeping this flush on the Pi only.")
        return False

    flush_id = record["id"]
    url = (f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}"
           f"/databases/(default)/documents/flushes/{flush_id}")

    for attempt in range(1, attempts + 1):
        try:
            response = requests.patch(url, json=firestore_payload(record),
                                      timeout=FIRESTORE_TIMEOUT_SECONDS)
            if response.status_code in (200, 201):
                if verbose:
                    print(f" Successfully committed {flush_id} to Cloud Firestore!")
                return True
            # 4xx responses are deterministic (bad payload, rules rejection) — retrying won't help.
            if 400 <= response.status_code < 500:
                print(f" Firestore rejected {flush_id}: HTTP {response.status_code} {response.text}")
                return False
            print(f" Firestore HTTP Error {response.status_code} (attempt {attempt}/{attempts})")
        except Exception as e:
            print(f" Error connecting to Firestore API (attempt {attempt}/{attempts}): {e}")
        if attempt < attempts:
            time.sleep(0.5)
    return False


def save_to_local_log(record):
    """Mirrors every flush into local_flushes.json so the Pi keeps a complete record."""
    path = data_path("local_flushes.json")
    flushes = read_json(path, {})
    if not isinstance(flushes, dict):
        flushes = {}
    flushes[record["id"]] = record
    write_json(path, flushes)


def queue_pending(record):
    """Adds an unsynced flush to the re-send queue (newest entries win if it overflows)."""
    path = data_path("pending_flushes.json")
    pending = read_json(path, [])
    if not isinstance(pending, list):
        pending = []
    pending.append(record)
    if len(pending) > PENDING_QUEUE_LIMIT:
        dropped = len(pending) - PENDING_QUEUE_LIMIT
        pending = pending[-PENDING_QUEUE_LIMIT:]
        print(f" [!] Pending queue full — dropped {dropped} oldest unsynced flush(es).")
    write_json(path, pending)
    print(f" [~] {record['id']} queued for upload ({len(pending)} awaiting network).")


def drain_pending_queue():
    """Best-effort re-send of previously queued flushes. Call AFTER printing the receipt."""
    path = data_path("pending_flushes.json")
    pending = read_json(path, [])
    if not isinstance(pending, list) or not pending:
        return

    print(f" [~] Retrying {len(pending)} queued flush(es)...")
    still_pending = []
    for record in pending:
        # One attempt each: if the network is still down, fail fast rather than
        # blocking the button for the length of the whole backlog.
        if not push_to_firestore(record, attempts=1, verbose=False):
            still_pending.append(record)

    synced = len(pending) - len(still_pending)
    if synced:
        print(f" [✓] Uploaded {synced} previously queued flush(es).")
    if still_pending:
        write_json(path, still_pending)
    elif os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            write_json(path, [])


def save_flush(record):
    """Persists a flush: always to the local log, to Firestore if reachable, queued if not.

    Returns True when the record reached Firestore, so the receipt can tell the
    visitor whether their tracking QR code will actually resolve.
    """
    save_to_local_log(record)
    if push_to_firestore(record):
        return True
    queue_pending(record)
    return False

def generate_qr_code(tracking_url, flush_id):
    """Generates a QR code image and terminal ASCII preview"""
    qr_filename = data_path(f"qr_{flush_id}.png")

    if QRCODE_AVAILABLE:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=2,
        )
        qr.add_data(tracking_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        img.save(qr_filename)
        print(f" QR Code saved to: {qr_filename}")

        # ASCII QR Code for terminal preview
        print("\n--- TERMINAL QR CODE PREVIEW ---")
        qr_ascii = qrcode.QRCode(border=1)
        qr_ascii.add_data(tracking_url)
        qr_ascii.print_ascii(invert=True)
    else:
        print(f" [!] 'qrcode' library not installed. Install with: pip install qrcode[pil]")
        print(f" Target Tracking URL: {tracking_url}")

    return qr_filename

def pil_image_to_escpos_raster(img_filename, target_width=200):
    """Converts a PNG image file into ESC/POS GS v 0 raster bit-image commands for 58mm thermal printers (200px width)."""
    try:
        from PIL import Image
    except ImportError:
        return b""

    if not img_filename or not os.path.exists(img_filename):
        return b""

    try:
        with Image.open(img_filename) as im:
            # Flatten transparency onto white, otherwise transparent pixels print black
            if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
                bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im.convert('RGBA')).convert('RGB')
            w, h = im.size
            aspect = h / float(w)
            new_w = min(w, target_width)
            new_w = (new_w // 8) * 8  # Width must be a multiple of 8
            new_h = int(new_w * aspect)

            bw = im.convert('L').resize((new_w, new_h))
            bw = bw.point(lambda p: 255 if p > 128 else 0, mode='1')

            width_bytes = new_w // 8
            raster_data = bytearray()
            pixels = bw.load()

            for y in range(new_h):
                for x_byte in range(width_bytes):
                    byte_val = 0
                    for bit in range(8):
                        x = x_byte * 8 + bit
                        if pixels[x, y] == 0:  # Black pixel
                            byte_val |= (1 << (7 - bit))
                    raster_data.append(byte_val)

            xL = width_bytes & 0xFF
            xH = (width_bytes >> 8) & 0xFF
            yL = new_h & 0xFF
            yH = (new_h >> 8) & 0xFF

            # ESC/POS GS v 0 0 command header
            cmd = bytearray([0x1D, 0x76, 0x30, 0x00, xL, xH, yL, yH])
            cmd.extend(raster_data)
            return bytes(cmd)
    except Exception as e:
        print(f" [!] Raster image conversion error: {e}")
        return b""

def generate_escpos_native_qr(url):
    """Generates standard native ESC/POS QR code command sequence (GS ( k)."""
    url_bytes = url.encode('utf-8')
    num_bytes = len(url_bytes) + 3
    pL = num_bytes & 0xFF
    pH = (num_bytes >> 8) & 0xFF

    cmd = bytearray()
    cmd.extend(b"\x1d\x28\x6b\x04\x00\x31\x41\x32\x00")  # Model 2
    cmd.extend(b"\x1d\x28\x6b\x03\x00\x31\x43\x04")      # Size 4 for 58mm paper
    cmd.extend(b"\x1d\x28\x6b\x03\x00\x31\x45\x31")      # Error level M
    cmd.extend(bytes([0x1D, 0x28, 0x6B, pL, pH, 0x31, 0x50, 0x30]))
    cmd.extend(url_bytes)
    cmd.extend(b"\x1d\x28\x6b\x03\x00\x31\x51\x30")      # Print
    return bytes(cmd)

def get_next_customer_number():
    """Reads, increments, and persists the local receipt counter."""
    counter_file = data_path("receipt_counter.json")
    count = 0
    if os.path.exists(counter_file):
        try:
            with open(counter_file, 'r') as f:
                data = json.load(f)
                count = data.get("customer_number", 0)
        except Exception:
            count = 0
    count += 1
    try:
        with open(counter_file, 'w') as f:
            json.dump({"customer_number": count}, f, indent=2)
    except Exception as e:
        print(f" [!] Could not save receipt counter: {e}")
    return count

def print_receipt(flush_id, formatted_time, tracking_url, qr_filename=None, synced=True):
    """Formats and prints an ESC/POS styled thermal receipt for 58mm paper (RECEIPT_WIDTH columns)

    `synced` reflects whether the flush reached Firestore. When it did not, the
    receipt says so — the QR code will not resolve until the Pi is back online.
    """
    customer_num = get_next_customer_number()
    width = RECEIPT_WIDTH
    divider = "=" * width
    dash_line = "-" * width

    def center(text):
        return text.center(width).rstrip()

    toilet_png = data_path("toilet.png")
    greeting_text = "Thank you, come again!\n\n"

    header_text = f"""
{divider}
{center("FIDGET CAMP PUSH NOTIFICATION")}
{center("1417 15th St -> Pier 80")}
{divider}
ID:       {flush_id}
DATE:     {formatted_time}
TYPE:     1.6 G Flush
ORIGIN:   1417 15th St, SF
DEST:     Pier 80 Outfall
{dash_line}
SCAN QR CODE TO TRACK LIVE:
"""

    sync_notice = "" if synced else f"""{dash_line}
{center("!! OFFLINE - NOT YET SYNCED !!")}
{center("Saved on the device; it will")}
{center("upload when back online.")}
"""

    footer_text = f"""
{center(PUBLIC_HOST_URL.rstrip('/'))}
{center("/flushtracker.html?id=")}
{center(flush_id)}
{sync_notice}{dash_line}
{center("Remember: Only Flush 3 P's:")}
{center("Poop, Pee, and Paper!")}
{dash_line}
{center(f"You are satisfied customer #{customer_num}")}
{divider}
"""
    full_receipt_text = "\n    [ TOILET GRAPHIC ]\n   " + greeting_text + header_text + "\n [ QR CODE GRAPHIC ]\n" + footer_text
    print(full_receipt_text)
    
    # Save receipt copy (never let a read-only filesystem stop the actual printing)
    try:
        with open(data_path("last_receipt.txt"), 'w') as f:
            f.write(full_receipt_text)
    except Exception as e:
        print(f" [!] Could not save last_receipt.txt: {e}")

    # Send raw ESC/POS bytes to connected USB thermal receipt printer (/dev/usb/lp0)
    if os.path.exists("/dev/usb/lp0"):
        raw_bytes = bytearray()
        raw_bytes.extend(b"\x1b\x40")  # ESC @

        # Centered toilet graphic + greeting at the very top
        raw_bytes.extend(b"\x1b\x61\x01")  # ESC a 1 (Center)
        toilet_raster = pil_image_to_escpos_raster(toilet_png, target_width=240)
        if toilet_raster:
            raw_bytes.extend(b"\n")
            raw_bytes.extend(toilet_raster)
        raw_bytes.extend(greeting_text.encode('utf-8'))
        raw_bytes.extend(b"\x1b\x61\x00")  # ESC a 0 (Left)

        raw_bytes.extend(header_text.encode('utf-8'))
        raw_bytes.extend(b"\x1b\x61\x01")  # ESC a 1 (Center)

        printed_qr = False
        if qr_filename and os.path.exists(qr_filename):
            raster_bytes = pil_image_to_escpos_raster(qr_filename, target_width=256)
            if raster_bytes:
                raw_bytes.extend(raster_bytes)
                printed_qr = True

        if not printed_qr:
            raw_bytes.extend(generate_escpos_native_qr(tracking_url))

        raw_bytes.extend(b"\x1b\x61\x00")  # ESC a 0 (Left)
        raw_bytes.extend(footer_text.encode('utf-8'))
        raw_bytes.extend(b"\x1d\x56\x41\x03")  # GS V (Cut)

        try:
            with open("/dev/usb/lp0", "wb") as printer:
                printer.write(raw_bytes)
            print(" Printed thermal receipt with QR Code graphic to /dev/usb/lp0!")
        except PermissionError:
            print(" [!] Permission denied accessing /dev/usb/lp0!")
            print("     Add your user to the 'lp' group, then restart the service:")
            print("     sudo usermod -a -G lp $USER")
            print("     (Avoid 'chmod 666' — it resets when the printer re-enumerates.)")
            # Attempt CUPS fallback
            try:
                raw_bin = data_path("last_receipt.bin")
                with open(raw_bin, "wb") as f:
                    f.write(raw_bytes)
                res = subprocess.run(["lpr", "-o", "raw", raw_bin], capture_output=True)
                if res.returncode == 0:
                    print(" [✓] Successfully printed via CUPS fallback (lpr -o raw)!")
            except Exception:
                pass
        except Exception as err:
            print(f" Could not write to /dev/usb/lp0: {err}")

def trigger_flush_event():
    """Main workflow executed on each button push"""
    print("\n" + "="*50)
    print(" 🚽 BUTTON PUSH DETECTED! Generating New Flush...")
    print("="*50)

    flush_id = generate_flush_id()
    now = datetime.datetime.now()
    timestamp_ms = int(now.timestamp() * 1000)
    formatted_time = now.strftime("%b %d, %Y %I:%M %p").strip()

    # Public tracking URL
    base_url = PUBLIC_HOST_URL.rstrip('/')
    tracking_url = f"{base_url}/flushtracker.html?id={flush_id}"

    # 1. Persist the flush (local log always; Firestore if reachable, queued if not)
    record = build_flush_record(flush_id, timestamp_ms, formatted_time)
    synced = save_flush(record)

    # 2. Generate QR code
    qr_filename = generate_qr_code(tracking_url, flush_id)

    # 3. Print thermal receipt and cleanup temporary files
    try:
        print_receipt(flush_id, formatted_time, tracking_url, qr_filename, synced=synced)
    finally:
        try:
            if qr_filename and os.path.exists(qr_filename):
                os.remove(qr_filename)
            raw_bin = data_path("last_receipt.bin")
            if os.path.exists(raw_bin):
                os.remove(raw_bin)
        except Exception:
            pass

    # 4. Now that the visitor has their receipt, catch up on anything queued earlier.
    if synced:
        drain_pending_queue()

def create_button(pin_num, bounce_time):
    """Creates a gpiozero Button, or exits with install instructions if no GPIO backend is available."""
    try:
        from gpiozero import Button
        return Button(pin_num, pull_up=True, bounce_time=bounce_time)
    except Exception as e:
        print(f" [!] Could not set up GPIO pin {pin_num} via gpiozero: {e}")
        print()
        if "busy" in str(e).lower():
            print(" The pin is already claimed by another process — most likely the")
            print(" flushtracker systemd service is already running. Check with:")
            print("   systemctl status flushtracker")
            print(" To run this script manually, stop the service first:")
            print("   sudo systemctl stop flushtracker")
            sys.exit(1)
        print(" gpiozero needs a pin-driver backend (lgpio) to talk to the hardware.")
        print(" Fix it with ONE of the following, then re-run:")
        print("   System Python:   sudo apt install python3-gpiozero python3-lgpio")
        print("   This venv:       pip install gpiozero lgpio")
        print("   Or recreate venv with access to apt packages:")
        print("                    python3 -m venv --system-site-packages venv")
        sys.exit(1)

def test_gpio(pin_num=BUTTON_GPIO_PIN):
    """Continuously prints raw GPIO state to debug button wiring."""
    print(f"\n🔍 Testing GPIO Pin {pin_num} (Physical Pin 11)...")
    print(" Press your physical button to see live state changes. (Press Ctrl+C to exit)\n")
    btn = create_button(pin_num, bounce_time=0.05)
    prev_state = None
    try:
        while True:
            is_pressed = btn.is_pressed
            if is_pressed != prev_state:
                if is_pressed:
                    print(" 🟢 [BUTTON PRESSED!] (Signal connected to GND)")
                else:
                    print(" ⚪ [BUTTON RELEASED] (Signal resting HIGH)")
                prev_state = is_pressed
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n Exiting GPIO test.")

def main():
    if "--test-gpio" in sys.argv or "--test" in sys.argv:
        test_gpio()
        sys.exit(0)

    print("==================================================")
    print("  San Francisco FlushTracker Raspberry Pi System")
    print("==================================================")

    if IS_RASPBERRY_PI:
        print(f" [✓] Raspberry Pi Hardware Detected! Setting up GPIO Pin {BUTTON_GPIO_PIN}...")
        button = create_button(BUTTON_GPIO_PIN, bounce_time=0.2)

        # Sample the button's resting state at startup so both normally-open and
        # normally-closed wiring work: a flush fires on any change AWAY from the
        # resting state, and the button must return to rest before it can fire again.
        time.sleep(0.5)  # let the pull-up settle
        resting_state = button.is_pressed
        wiring = "CLOSED at rest (normally-closed)" if resting_state else "OPEN at rest (normally-open)"
        print(f" [i] Button wiring detected: {wiring}. Flush fires when that state changes.")

        last_flush = [0.0]

        def on_state_change():
            now_ts = time.monotonic()
            if now_ts - last_flush[0] < FLUSH_COOLDOWN_SECONDS:
                print(f" [~] Button event ignored (cooldown, {FLUSH_COOLDOWN_SECONDS}s between flushes).")
                return
            last_flush[0] = now_ts
            trigger_flush_event()

        if resting_state:
            button.when_released = on_state_change
        else:
            button.when_pressed = on_state_change

        print(f" [✓] Connected via gpiozero! Listening for button presses on GPIO {BUTTON_GPIO_PIN}...")
        print(" Press physical button to generate a flush. (Press Ctrl+C to exit)\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n Exiting FlushTracker Pi listener.")
    else:
        print(" Running in Workstation / Development Mode.")
        print(" Press [ENTER] in terminal to trigger a new button flush! (Ctrl+C to quit)\n")
        try:
            while True:
                input(">>> Press [ENTER] to push button... ")
                trigger_flush_event()
        except (KeyboardInterrupt, EOFError):
            print("\n Exiting FlushTracker Dev listener.")

if __name__ == "__main__":
    main()
