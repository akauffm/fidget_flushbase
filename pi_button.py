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
import string
import datetime
import json
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

def save_to_firestore(flush_id, timestamp_ms, formatted_time):
    """Writes flush document to Firebase Cloud Firestore via HTTP REST API"""
    if FIREBASE_PROJECT_ID == "YOUR_PROJECT_ID":
        print(" [NOTE] Firebase Project ID not configured yet. Saving flush locally to local_flushes.json.")
        local_db_path = os.path.join(os.path.dirname(__file__), "local_flushes.json")
        flushes = {}
        if os.path.exists(local_db_path):
            try:
                with open(local_db_path, 'r') as f:
                    flushes = json.load(f)
            except Exception:
                pass
        flushes[flush_id] = {
            "id": flush_id,
            "timestamp": timestamp_ms,
            "formatted_time": formatted_time,
            "gpf": 1.6,
            "waste_type": "liquid",
            "origin": "1417 15th St, San Francisco, CA"
        }
        with open(local_db_path, 'w') as f:
            json.dump(flushes, f, indent=2)
        return True

    # Firestore REST API endpoint
    url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/flushes/{flush_id}"
    payload = {
        "fields": {
            "timestamp": {"integerValue": str(timestamp_ms)},
            "formatted_time": {"stringValue": formatted_time},
            "gpf": {"doubleValue": 1.6},
            "waste_type": {"stringValue": "liquid"},
            "origin": {"stringValue": "1417 15th St, San Francisco, CA"}
        }
    }
    
    try:
        response = requests.patch(url, json=payload, timeout=5)
        if response.status_code in (200, 201):
            print(f" Successfully committed {flush_id} to Cloud Firestore!")
            return True
        else:
            print(f" Firestore HTTP Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f" Error connecting to Firestore API: {e}")
        return False

def generate_qr_code(tracking_url, flush_id):
    """Generates a QR code image and terminal ASCII preview"""
    output_dir = os.path.dirname(__file__)
    qr_filename = os.path.join(output_dir, f"qr_{flush_id}.png")

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
    counter_file = os.path.join(os.path.dirname(__file__), "receipt_counter.json")
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

def print_receipt(flush_id, formatted_time, tracking_url, qr_filename=None):
    """Formats and prints an ESC/POS styled thermal receipt for 58mm paper (28 columns max)"""
    customer_num = get_next_customer_number()
    divider = "=" * 28
    dash_line = "-" * 28

    header_text = f"""
{divider}
 FIDGET CAMP FLUSH TRACKER
1417 15th St -> Pier 80
{divider}
ID:       {flush_id}
DATE:     {formatted_time}
TYPE:     Liquid (1.6 GPF)
ORIGIN:   1417 15th St, SF
DEST:     Pier 80 Outfall
{dash_line}
SCAN QR CODE TO TRACK LIVE:
"""

    footer_text = f"""
  https://flushbase.web.app
  /flushtracker.html?id=
       {flush_id}
{dash_line}
  Remember: Only Flush 3 P's:
     Poop, Pee, and Paper!
{dash_line}
You are satisfied customer #{customer_num}
{divider}
"""
    full_receipt_text = header_text + "\n [ QR CODE GRAPHIC ]\n" + footer_text
    print(full_receipt_text)
    
    # Save receipt copy
    receipt_file = os.path.join(os.path.dirname(__file__), "last_receipt.txt")
    with open(receipt_file, 'w') as f:
        f.write(full_receipt_text)

    # Send raw ESC/POS bytes to connected USB thermal receipt printer (/dev/usb/lp0)
    if os.path.exists("/dev/usb/lp0"):
        raw_bytes = bytearray()
        raw_bytes.extend(b"\x1b\x40")  # ESC @
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
            print("     To fix permission, run this command in terminal:")
            print("     sudo chmod 666 /dev/usb/lp0   (or: sudo usermod -a -G lp $USER)")
            # Attempt CUPS fallback
            try:
                raw_bin = os.path.join(os.path.dirname(__file__), "last_receipt.bin")
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

    # 1. Save to online Cloud Firestore
    save_to_firestore(flush_id, timestamp_ms, formatted_time)

    # 2. Generate QR code
    qr_filename = generate_qr_code(tracking_url, flush_id)

    # 3. Print thermal receipt and cleanup temporary files
    try:
        print_receipt(flush_id, formatted_time, tracking_url, qr_filename)
    finally:
        try:
            if qr_filename and os.path.exists(qr_filename):
                os.remove(qr_filename)
            raw_bin = os.path.join(os.path.dirname(__file__), "last_receipt.bin")
            if os.path.exists(raw_bin):
                os.remove(raw_bin)
        except Exception:
            pass

def test_gpio(pin_num=BUTTON_GPIO_PIN):
    """Continuously prints raw GPIO state to debug button wiring."""
    print(f"\n🔍 Testing GPIO Pin {pin_num} (Physical Pin 11)...")
    print(" Press your physical button to see live state changes. (Press Ctrl+C to exit)\n")
    try:
        from gpiozero import Button
        btn = Button(pin_num, pull_up=True, bounce_time=0.1)
        prev_state = None
        while True:
            is_pressed = btn.is_pressed
            if is_pressed != prev_state:
                if is_pressed:
                    print(" 🟢 [BUTTON PRESSED!] (Signal connected to GND)")
                else:
                    print(" ⚪ [BUTTON RELEASED] (Signal resting HIGH)")
                prev_state = is_pressed
            time.sleep(0.05)
    except Exception as e:
        print(f" [!] Error testing GPIO: {e}")

class NativeSysfsButton:
    """Zero-dependency Linux kernel sysfs GPIO button listener for Raspberry Pi."""
    def __init__(self, pin=17):
        self.pin = pin
        self.export_path = f"/sys/class/gpio/gpio{pin}"
        self.setup_pin()

    def setup_pin(self):
        if not os.path.exists(self.export_path):
            try:
                with open("/sys/class/gpio/export", "w") as f:
                    f.write(str(self.pin))
                time.sleep(0.1)
            except Exception:
                pass
        if os.path.exists(f"{self.export_path}/direction"):
            try:
                with open(f"{self.export_path}/direction", "w") as f:
                    f.write("in")
            except Exception:
                pass
        if os.path.exists(f"{self.export_path}/active_low"):
            try:
                with open(f"{self.export_path}/active_low", "w") as f:
                    f.write("1")
            except Exception:
                pass

    def is_pressed(self):
        val_path = f"{self.export_path}/value"
        if os.path.exists(val_path):
            try:
                with open(val_path, "r") as f:
                    return f.read().strip() == "1"
            except Exception:
                pass
        return False

def test_gpio(pin_num=BUTTON_GPIO_PIN):
    """Continuously prints raw GPIO state to debug button wiring."""
    print(f"\n🔍 Testing GPIO Pin {pin_num} (Physical Pin 11)...")
    print(" Press your physical button to see live state changes. (Press Ctrl+C to exit)\n")

    # Try gpiozero first, fallback to native sysfs
    btn_obj = None
    try:
        from gpiozero import Button
        btn_obj = Button(pin_num, pull_up=True, bounce_time=0.1)
        print(" [✓] Using gpiozero backend.")
    except Exception:
        print(" [✓] Using zero-dependency Native Sysfs backend.")
        btn_obj = NativeSysfsButton(pin_num)

    prev_state = None
    try:
        while True:
            is_pressed = btn_obj.is_pressed if hasattr(btn_obj, 'is_pressed') else getattr(btn_obj, 'is_active', False)
            if callable(is_pressed):
                is_pressed = is_pressed()
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
        listener_started = False

        # Backend 1: gpiozero
        try:
            from gpiozero import Button
            button = Button(BUTTON_GPIO_PIN, pull_up=True, bounce_time=0.2)
            button.when_pressed = trigger_flush_event
            print(f" [✓] Connected via gpiozero! Listening for button presses on GPIO {BUTTON_GPIO_PIN}...")
            listener_started = True
            print(" Press physical button to generate a flush. (Press Ctrl+C to exit)\n")
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\n Exiting FlushTracker Pi listener.")
            sys.exit(0)
        except Exception:
            pass

        # Backend 2: RPi.GPIO
        if not listener_started:
            try:
                import RPi.GPIO as GPIO
                GPIO.setmode(GPIO.BCM)
                GPIO.setup(BUTTON_GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

                def on_gpi_event(channel):
                    trigger_flush_event()

                GPIO.add_event_detect(BUTTON_GPIO_PIN, GPIO.FALLING, callback=on_gpi_event, bouncetime=300)
                print(f" [✓] Connected via RPi.GPIO! Listening for button presses on GPIO {BUTTON_GPIO_PIN}...")
                listener_started = True
                print(" Press physical button to generate a flush. (Press Ctrl+C to exit)\n")
                while True:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("\n Exiting FlushTracker Pi listener.")
                sys.exit(0)
            except Exception:
                pass

        # Backend 3: Zero-Dependency Native Linux Sysfs (Fallback)
        if not listener_started:
            print(f" [✓] Connected via Native Linux Sysfs! Listening for button presses on GPIO {BUTTON_GPIO_PIN}...")
            sys_btn = NativeSysfsButton(BUTTON_GPIO_PIN)
            print(" Press physical button to generate a flush. (Press Ctrl+C to exit)\n")
            prev = False
            try:
                while True:
                    cur = sys_btn.is_pressed()
                    if cur and not prev:
                        trigger_flush_event()
                    prev = cur
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print("\n Exiting FlushTracker Pi listener.")
                sys.exit(0)
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
