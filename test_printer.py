#!/usr/bin/env python3
"""
FlushTracker Thermal Receipt Test Utility for Laptop & Pi
Usage:
    python3 test_printer.py                    # Auto-detects printer / port or saves binary output
    python3 test_printer.py --port /dev/cu.usbserial-1410
    python3 test_printer.py --printer <name>  # Prints via macOS CUPS (lpr -P <name> -o raw)
    python3 test_printer.py --list             # List available ports & CUPS printers
    python3 test_printer.py --cols 40          # Tune receipt character width (e.g. 40 for 80mm, 32 for 58mm)
"""

import sys
import os
import glob
import time
import datetime
import argparse
import subprocess

try:
    import qrcode
    from PIL import Image
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False


def find_available_printers():
    """Scans for macOS CUPS printers and USB serial/raw device files."""
    ports = []
    # Check /dev device nodes
    dev_patterns = ['/dev/cu.usb*', '/dev/tty.usb*', '/dev/usb/lp*', '/dev/usbserial*', '/dev/ttyUSB*']
    for pattern in dev_patterns:
        ports.extend(glob.glob(pattern))

    # Check CUPS printers via lpstat
    cups_printers = []
    try:
        res = subprocess.run(['lpstat', '-p'], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith('printer '):
                    parts = line.split()
                    if len(parts) >= 2:
                        cups_printers.append(parts[1])
    except Exception:
        pass

    return sorted(list(set(ports))), cups_printers


def pil_image_to_escpos_raster(pil_img, max_width=384):
    """Converts a PIL image object into ESC/POS GS v 0 raster bit-image commands."""
    w, h = pil_img.size
    aspect = h / float(w)
    new_w = min(w, max_width)
    new_w = (new_w // 8) * 8  # Width must be a multiple of 8
    new_h = int(new_w * aspect)

    bw = pil_img.convert('L').resize((new_w, new_h))
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

    cmd = bytearray([0x1D, 0x76, 0x30, 0x00, xL, xH, yL, yH])
    cmd.extend(raster_data)
    return bytes(cmd)


def generate_escpos_native_qr(url):
    """Generates standard native ESC/POS QR code command sequence (GS ( k)."""
    url_bytes = url.encode('utf-8')
    num_bytes = len(url_bytes) + 3
    pL = num_bytes & 0xFF
    pH = (num_bytes >> 8) & 0xFF

    cmd = bytearray()
    cmd.extend(b"\x1d\x28\x6b\x04\x00\x31\x41\x32\x00")  # Model 2
    cmd.extend(b"\x1d\x28\x6b\x03\x00\x31\x43\x06")      # Size 6
    cmd.extend(b"\x1d\x28\x6b\x03\x00\x31\x45\x31")      # Error level M
    cmd.extend(bytes([0x1D, 0x28, 0x6B, pL, pH, 0x31, 0x50, 0x30]))
    cmd.extend(url_bytes)
    cmd.extend(b"\x1d\x28\x6b\x03\x00\x31\x51\x30")      # Print
    return bytes(cmd)


def build_test_receipt(cols=40, flush_id="FLUSH-TEST01", customer_num=1):
    """Builds receipt text formatted to the specified character column width."""
    divider = "=" * cols
    dash_line = "-" * cols

    title = "FIDGET CAMP FLUSH TRACKER"
    subtitle = "1417 15th St ➔ Pier 80 Outfall"

    centered_title = title.center(cols)
    centered_sub = subtitle.center(cols)
    now_str = datetime.datetime.now().strftime("%b %d, %Y %I:%M:%S %p")

    url = f"https://flushbase.web.app/flushtracker.html?id={flush_id}"

    header_text = f"""{divider}
{centered_title}
{centered_sub}
{divider}
 TICKET ID:    {flush_id}
 TIMESTAMP:    {now_str}
 FLUSH TYPE:   Liquid Stream (1.6 GPF)
 ORIGIN:       1417 15th St (Mission Dist)
 DESTINATION:  SF Bay Outfall (800 ft)
{dash_line}
 SCAN QR CODE TO TRACK YOUR FLUSH LIVE:
"""

    p_notice = "Remember: Only Flush the 3 P's:\n     Poop, Pee, and Paper!"
    cust_notice = f"You are satisfied customer number {customer_num}"

    footer_text = f"""
 {url}
{dash_line}
 {p_notice}
{dash_line}
 {cust_notice}
{divider}
\n\n"""

    return header_text, footer_text, url


def main():
    parser = argparse.ArgumentParser(description="Test Thermal Receipt Printer formatting from Laptop or Pi")
    parser.add_argument("--port", type=str, help="Specify direct USB/serial device path (e.g., /dev/cu.usbserial-1410)")
    parser.add_argument("--printer", type=str, help="Specify macOS CUPS printer name (e.g. EPSON_TM_T20)")
    parser.add_argument("--cols", type=int, default=40, help="Character width per line (default: 40 for 80mm, 32 for 58mm)")
    parser.add_argument("--list", action="store_true", help="List detected USB ports and CUPS printers")
    args = parser.parse_args()

    ports, cups_printers = find_available_printers()

    if args.list:
        print("\n🔍 --- DETECTED HARDWARE PORTS ---")
        if ports:
            for p in ports:
                print(f"  • Device Port: {p}")
        else:
            print("  (No direct /dev/cu.usb* or /dev/usb/lp* ports detected)")

        print("\n🖨️ --- INSTALLED CUPS PRINTERS ---")
        if cups_printers:
            for c in cups_printers:
                print(f"  • CUPS Printer: {c}")
        else:
            print("  (No CUPS printers found via lpstat)")
        print()
        sys.exit(0)

    print("\n==================================================")
    print(" 🧾 Thermal Receipt Format & Printer Test Runner")
    print(f" Target Column Width: {args.cols} columns")
    print("==================================================")

    flush_id = f"FLUSH-TEST{int(time.time()) % 1000:03d}"
    header_text, footer_text, url = build_test_receipt(cols=args.cols, flush_id=flush_id)

    # Render Terminal Preview
    print("\n--- TERMINAL RECEIPT PREVIEW ---")
    print(header_text)
    print("      [ 📷 QR CODE GRAPHIC WOULD PRINT HERE ]      ")
    print(footer_text)

    # Generate QR Code image
    qr_img = None
    qr_filename = f"qr_{flush_id}.png"
    if QRCODE_AVAILABLE:
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_img.save(qr_filename)
        print(f" Saved QR graphic preview to: {qr_filename}")

    # Build raw ESC/POS payload
    raw_payload = bytearray()
    raw_payload.extend(b"\x1b\x40")  # Initialize printer
    raw_payload.extend(header_text.encode('utf-8'))
    raw_payload.extend(b"\x1b\x61\x01")  # Center align for QR

    if qr_img:
        raster_bytes = pil_image_to_escpos_raster(qr_img)
        raw_payload.extend(raster_bytes)
    else:
        raw_payload.extend(generate_escpos_native_qr(url))

    raw_payload.extend(b"\x1b\x61\x00")  # Left align
    raw_payload.extend(footer_text.encode('utf-8'))
    raw_payload.extend(b"\x1d\x56\x41\x03")  # Cut paper

    # Save to binary file
    bin_file = "test_receipt_output.bin"
    with open(bin_file, "wb") as f:
        f.write(raw_payload)
    print(f" Saved raw ESC/POS binary stream to: {bin_file}")

    # Determine Output Device
    target_port = args.port
    if not target_port and ports:
        target_port = ports[0]

    printed_success = False

    # Try CUPS printer name if provided
    if args.printer:
        print(f"\n Attempting print via macOS CUPS printer '{args.printer}'...")
        try:
            res = subprocess.run(['lpr', '-P', args.printer, '-o', 'raw', bin_file], capture_output=True, text=True)
            if res.returncode == 0:
                print(f" SUCCESS: Sent binary payload to CUPS printer '{args.printer}'!")
                printed_success = True
            else:
                print(f" CUPS lpr error: {res.stderr}")
        except Exception as e:
            print(f" Could not invoke lpr: {e}")

    # Try Direct Serial / USB port
    if not printed_success and target_port:
        print(f"\n Attempting direct raw write to '{target_port}'...")
        try:
            with open(target_port, "wb") as printer:
                printer.write(raw_payload)
            print(f" SUCCESS: Printed to device '{target_port}'!")
            printed_success = True
        except Exception as e:
            print(f" Could not write directly to '{target_port}': {e}")

    if not printed_success:
        print("\n💡 TIP for testing on your Mac:")
        print(" 1. Run `python3 test_printer.py --list` to see available ports and installed printers.")
        print(" 2. To print directly to a CUPS printer: `python3 test_printer.py --printer PRINTER_NAME`")
        print(f" 3. Or send raw bytes via command line: `lpr -o raw {bin_file}` or `cat {bin_file} > /dev/your_usb_device`")


if __name__ == "__main__":
    main()
