# 🚽 FlushTracker - 2026 Fidget Camp Showcase

An interactive, real-time wastewater tracking system created for the **2026 Fidget Camp Showcase**. 

FlushTracker allows venue visitors to trigger a simulated wastewater deposit from 1417 15th St in San Francisco, print a physical 58mm thermal receipt with a scannable QR code, and follow their deposit live as it journeys through the SFPUC collection system, Southeast Water Pollution Control Plant (SEP), and final discharge into the San Francisco Bay.

Live Web App: **[https://flushbase.web.app](https://flushbase.web.app)**

---

## 🛠️ Raspberry Pi Setup & Installation

### 1. Install System Dependencies
On your Raspberry Pi terminal, update your package list and install system GPIO build tools:
```bash
sudo apt update
sudo apt install -y swig python3-gpiozero python3-rpi.gpio python3-lgpio
```

### 2. Printer Permissions (Direct USB Access)
Grant direct read/write access for USB thermal printers (`/dev/usb/lp0`):
```bash
sudo chmod 666 /dev/usb/lp0
# Or permanently add your user to the lp group:
sudo usermod -a -G lp $USER
```

### 3. Clone Repository & Install Python Dependencies
```bash
git clone https://github.com/akauffm/fidget_flushbase.git
cd fidget_flushbase

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## 🔌 Hardware Wiring Guide (Physical Flush Button)

The hardware button uses the Raspberry Pi's internal pull-up resistor (no external resistors required).

| Button Terminal | Raspberry Pi Connection | Pin Header Location |
| :--- | :--- | :--- |
| **Wire 1** (Signal) | **GPIO 17** | **Physical Pin 11** |
| **Wire 2** (Ground) | **GND** | **Physical Pin 9** (or Pin 14 / Pin 6) |

### 40-Pin Header Layout:
```text
         3.3V  [01]  [02]  5V
    GPIO2/SDA  [03]  [04]  5V
    GPIO3/SCL  [05]  [06]  GND
        GPIO4  [07]  [08]  GPIO14/TXD
          GND  [09]  [10]  GPIO15/RXD  <-- Ground Connection
 (WIRE 1) GPIO17 [11]  [12]  GPIO18
       GPIO27  [13]  [14]  GND         <-- Alternative Ground
```

---

## 🚀 Running the System

### Start the Pi Listener
```bash
python3 pi_button.py
```

### Test Button Wiring (Live Diagnostic Mode)
To verify that your physical switch state is changing when pressed:
```bash
python3 pi_button.py --test-gpio
```

### Run as a Background Service on Boot (`systemd`)
Create a service configuration file:
```bash
sudo nano /etc/systemd/system/flushtracker.service
```

Paste the following:
```ini
[Unit]
Description=FlushTracker Button Listener
After=network.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/fidget_flushbase/pi_button.py
WorkingDirectory=/home/pi/fidget_flushbase
StandardOutput=inherit
StandardError=inherit
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

Enable and start the background service:
```bash
sudo systemctl enable flushtracker
sudo systemctl start flushtracker
```

---

## 🖨️ Laptop Thermal Printer Testing Utility

If you want to test and tune thermal receipt formatting from your laptop without a Raspberry Pi, run `test_printer.py`:

```bash
# List available USB devices & installed CUPS printers
python3 test_printer.py --list

# Render terminal preview & generate raw test receipt output
python3 test_printer.py

# Test 58mm (32/28 columns) or 80mm (40 columns) layout
python3 test_printer.py --cols 28
```

---

## 🌐 Web App & Firebase Deployment

The web application is hosted on Firebase Hosting and syncs in real-time with Cloud Firestore.

To deploy web updates:
```bash
firebase deploy
```

---

## 📄 License & Credits
Created for the **2026 Fidget Camp Showcase**.  
*Remember: Only Flush the 3 P's: Poop, Pee, and Paper!*
