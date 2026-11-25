# 📸 ESP32-CAM Attendance Scanner with RapidOCR

A low-cost, automated student attendance system built using the **ESP32-CAM**, **Python**, and **RapidOCR**. This project scans existing student ID cards, extracts text using a Neural Network OCR engine, verifies the student against a local database, and logs attendance directly to **Google Sheets**.

---

## 📖 Table of Contents
- [Why this Project?](#-why-this-project)
- [Features](#-features)
- [How It Works (System Architecture)](#-how-it-works-system-architecture)
- [Hardware Requirements](#-hardware-requirements)
- [Software Requirements](#-software-requirements)
- [Replication Guide (How to build it)](#-replication-guide-how-to-build-it)
- [Hardware & Software Hacks](#-hardware--software-hacks)
- [Limitations & Future Work](#-limitations--future-work)

---

## 🎯 Why this Project?
Traditional manual attendance (passing around a book) is slow, disrupts class time, and is highly prone to forgery (proxy attendance). 

While commercial solutions like RFID or Facial Recognition exist, they require expensive hardware, special cards, or raise privacy concerns. This project solves the problem by utilizing **existing Student ID cards** and cheap off-the-shelf components, keeping the total cost under **RM80**.

---

## ✨ Features
- **AI-Powered OCR:** Uses **RapidOCR** (ONNX runtime) instead of legacy Tesseract. It can read text even through plastic card holders, glare, and slight blur.
- **Smart Matching:** Uses exact IC matching and Strict Fuzzy Name matching (ignores common Malaysian name components like "Bin", "Binti", "Muhammad").
- **Daily Duplicate Filter:** Python script checks Google Sheets before logging to ensure a student is only marked present once per day.
- **Instant Physical Feedback:** Green LED + 1 Beep for success; Red LED + 2 Beeps for failure.
- **Cloud Logging:** Automatically sends Name, IC Number, and Timestamp to Google Sheets via API.
- **Hardware Stability:** Implements a 220µF capacitor across power to prevent ESP32 WiFi brownouts, and uses PWM to dim the built-in flash LED.

---

## ⚙️ How It Works (System Architecture)
1. **Trigger:** User presses the physical push button (GPIO 13).
2. **Capture:** ESP32-CAM captures a JPEG image using the dimmed flash (PWM).
3. **Transmit:** ESP32 hosts an HTTP Web Server. Python polls `/capture` over WiFi.
4. **Process:** Python uses OpenCV to rotate and upscale the image, then feeds it to RapidOCR.
5. **Verify:** Python extracts the Name/IC and checks against `students.json`.
6. **Feedback:** Python sends an HTTP GET request back to the ESP32 (`/feedback?status=success` or `fail`), triggering the LEDs and Buzzer.
7. **Log:** If successful, the data is appended to Google Sheets.

---

## 🛠️ Hardware Requirements
*   **ESP32-CAM** (AI-Thinker module)
*   **Micro-USB Cable** (Data & Power)
*   **Push Button** (Momentary switch)
*   **LEDs:** 1x Red, 1x Green
*   **Active Buzzer**
*   **Resistors:** 220Ω (or 330Ω) for LEDs, 100Ω for Buzzer
*   **Capacitor:** 220µF Electrolytic (Highly recommended across 5V and GND to stabilize WiFi)
*   **3D Printed Casing** (or a generic project box)

### Pin Mapping
| Component | ESP32 GPIO | Note |
| :--- | :--- | :--- |
| Push Button | 13 | Connected to GND (Internal Pull-up) |
| Red LED | 2 | Series with 220Ω resistor |
| Green LED | 12 | Series with 220Ω resistor |
| Buzzer | 14 | Series with 100Ω resistor |
| Built-in Flash | 4 | Controlled via PWM |

---

## 💻 Software Requirements
### ESP32 (Arduino IDE)
*   **ESP32 Board Core:** Version **3.2.1** (Must downgrade from 3.3.x to avoid camera memory crashes).
*   **Board:** AI-Thinker ESP32-CAM
*   **Partition Scheme:** Huge APP (3MB No OTA)

### Python (Host PC)
*   Python 3.8+
*   Libraries: `opencv-python`, `rapidocr`, `onnxruntime`, `requests`, `gspread`, `oauth2client`

---

## 🚀 Replication Guide (How to build it)

### 1. Google Sheets Setup
1. Create a Google Cloud Project.
2. Enable the **Google Drive API** and **Google Sheets API**.
3. Create a **Service Account**, download the `credentials.json` key, and place it in the project folder.
4. Create a Google Sheet, share it with the Service Account email (as Editor).

### 2. Student Database (`students.json`)
Create a `students.json` file in the root directory:
```json
[
    {"name": "HENRY", "ic": "0123456789"},
    {"name": "MICHEAL", "ic": "0123456789"}
]
```

### 3. Flash the ESP32
1. Open `esp32_code.ino` in Arduino IDE.
2. Update your `ssid` and `password`.
3. Upload to the ESP32-CAM (make sure to press the IO0 button to put it in flash mode).

### 4. Run the Python Script
1. Update the `ESP32_IP` in the Python script to match the IP shown in the Arduino Serial Monitor.
2. Run: `python3 main.py`
3. Press the button on the ESP32-CAM and watch the Google Sheet update!

---

## 🧠 Hardware & Software Hacks (The "Gotchas")
Building with the ESP32-CAM is notoriously difficult. Here is how this project solves common issues:
*   **The Camera Crash:** Core 3.3.x has a bug causing `frame buffer malloc failed`. We downgraded to Core 3.2.1 and configured `CAMERA_FB_IN_PSRAM` to fix this.
*   **The Flash Whiteout:** The built-in flash is too bright at close range. We used **PWM (`ledcWrite`)** to dim the LED to ~10% brightness, preventing glare.
*   **Memory Limits:** Initialized the camera *before* WiFi. If WiFi starts first, it fragments the memory, causing the camera to fail.
*   **Tesseract vs RapidOCR:** Tesseract failed on blurry/plastic-covered cards. RapidOCR uses a Neural Network that handles noise and glare incredibly well.

---

## ⚠️ Limitations & Future Work
*   **The "Bearer Token" Problem:** Like any card system, a student can hand their card to a friend. Solving this requires tying the student card to other physical campus assets (like cafeteria payments or library access) to give it real-world value.
*   **Hardware Upgrades:** Implementing a Raspberry Pi Zero 2 W would allow the entire system to be fully portable, removing the need for a laptop to run Python.
*   **Screen Integration:** Adding an OLED or LCD screen to display the student's name upon successful scan.

---
*Created as part of a Diploma in Electronic Technology final year project at Kolej Vokasional Beaufort.*
