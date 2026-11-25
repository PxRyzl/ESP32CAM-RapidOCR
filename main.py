import cv2
import numpy as np
import requests
import time
import json
import re
import os
from difflib import SequenceMatcher
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from rapidocr import RapidOCR

# ===== CONFIGURATION =====
ESP32_IP = "http://192.168.x.x" # <-- CHANGE THIS TO YOUR ESP32 IP
JSON_FILE = "students.json"
CREDENTIALS_FILE = "credentials.json"
GOOGLE_SHEET_NAME = "YOUR_SHEET_NAME" # <-- CHANGE THIS TO YOUR GOOGLE SHEET NAME
POLL_INTERVAL = 2.0
IC_FUZZY_THRESHOLD = 0.85
STARTUP_SYNC_DELAY = 3
# =========================

last_processed_version = -1
today_logged_ics = set()

# ===== INITIALIZE OCR ENGINE =====
print("⏳ Loading RapidOCR models...")
ocr_engine = RapidOCR()
print("✅ RapidOCR ready!")

# ===== STOPWORDS (Common Malaysian name components) =====
NAME_STOPWORDS = {
    "bin", "binti", "bt", "b", "a", "l", "p",
    "muhammad", "muhamad", "mohamad", "mohamed", "mohammad",
    "mohd", "md", "moh", "muhammed",
    "abdul", "abd", "abdu", "abdullah", "abdollah",
    "nur", "nurul", "nurr", "nor", "norl", "nora",
    "wan", "ahmad", "ahmed", "haji", "hajah", "hj", "hjh",
    "syed", "sharifah", "tun", "tengku", "raja",
    "amir", "amirul", "afif", "awangku", "awang",
    "ag", "dayang", "abang",
}

# ===== GOOGLE SHEETS INIT =====
sheet = None
try:
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(GOOGLE_SHEET_NAME).sheet1
    print(f"✅ Connected to Google Sheet: '{GOOGLE_SHEET_NAME}'")
except FileNotFoundError:
    print(f"⚠️ {CREDENTIALS_FILE} not found. Google Sheets disabled.")
except Exception as e:
    print(f"⚠️ Google Sheets error: {e}")
    print("   Continuing without Sheets logging...")

# ===== LOAD TODAY'S LOGS (with retry) =====
def load_todays_logs(retries=3, delay=3):
    global today_logged_ics
    if sheet is None:
        return

    for attempt in range(retries):
        try:
            all_rows = sheet.get_all_values()
            today = time.strftime("%Y-%m-%d")
            new_set = set()

            for row in all_rows:
                if len(row) >= 3:
                    timestamp = row[2].strip()
                    ic = row[1].strip()
                    if re.match(r'^\d{4}-\d{2}-\d{2}', timestamp) and timestamp.startswith(today) and ic:
                        new_set.add(ic)

            today_logged_ics = new_set
            print(f"📋 Already logged today: {len(today_logged_ics)} student(s)")
            if today_logged_ics:
                print(f"   ICs: {', '.join(sorted(today_logged_ics))}")
            return

        except Exception as e:
            print(f"⚠️ Attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)

    print("⚠️ Could not load today's logs after retries.")

# ===== LOAD STUDENTS FROM JSON =====
def load_students():
    try:
        with open(JSON_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ {JSON_FILE} not found.")
        return []
    except Exception as e:
        print(f"❌ Error loading JSON: {e}")
        return []

# ===== HELPER: Extract distinctive words =====
def get_distinctive_words(full_name):
    words = re.sub(r'[^a-zA-Z\s]', ' ', full_name.lower()).split()
    return [w for w in words if len(w) >= 4 and w not in NAME_STOPWORDS]

# ===== FUZZY NAME MATCHING (STRICT) =====
def fuzzy_match_name(ocr_text, student_list):
    ocr_clean = re.sub(r'[^a-zA-Z\s]', ' ', ocr_text.lower())
    ocr_words = [w for w in ocr_clean.split() if len(w) >= 4 and w not in NAME_STOPWORDS]

    if not ocr_words:
        return None, 0

    word_owners = {}
    for idx, student in enumerate(student_list):
        for w in get_distinctive_words(student["name"]):
            word_owners.setdefault(w, []).append(idx)

    results = []
    for idx, student in enumerate(student_list):
        student_words = get_distinctive_words(student["name"])
        if not student_words:
            continue

        matched_words = []
        for ocr_w in ocr_words:
            best_nw = None
            best_score = 0
            for nw in student_words:
                score = SequenceMatcher(None, ocr_w, nw).ratio()
                if score > best_score:
                    best_score = score
                    best_nw = nw
            if best_score >= 0.8:
                matched_words.append((ocr_w, best_nw, best_score))

        if matched_words:
            results.append({
                "student": student,
                "matched": matched_words,
                "count": len(matched_words),
                "avg": sum(m[2] for m in matched_words) / len(matched_words)
            })

    if not results:
        return None, 0

    results.sort(key=lambda x: (x["count"], x["avg"]), reverse=True)
    best = results[0]

    if best["count"] < 2:
        if best["count"] == 1:
            only_word = best["matched"][0][1]
            owners = word_owners.get(only_word, [])
            if len(owners) == 1:
                return best["student"], best["avg"]
            else:
                return None, 0
        return None, 0

    if len(results) > 1:
        second = results[1]
        if second["count"] == best["count"] and (best["avg"] - second["avg"]) < 0.1:
            return None, 0

    return best["student"], best["avg"]

# ===== EXACT IC MATCHING =====
def match_ic(ocr_text, student_list):
    ic_pattern = r'\b(\d{6})-?(\d{2})-?(\d{4})\b'
    matches = re.findall(ic_pattern, ocr_text)
    if matches:
        ic_number = ''.join(matches[0])
        print(f"   🔍 Detected IC pattern: {ic_number}")
        for student in student_list:
            if student["ic"] == ic_number:
                return student
    return None

# ===== FUZZY IC MATCHING (CONSERVATIVE) =====
def fuzzy_match_ic(ocr_text, student_list, threshold=IC_FUZZY_THRESHOLD):
    ic_pattern_full = r'\b(\d{6})-?(\d{2})-?(\d{4})\b'
    matches_full = re.findall(ic_pattern_full, ocr_text)

    if matches_full:
        detected_ic = ''.join(matches_full[0])
        print(f"   🔍 Detected full IC: {detected_ic}")
        birthdate = detected_ic[:6]

        candidates = [s for s in student_list if len(s["ic"]) == 12 and s["ic"][:6] == birthdate]

        if len(candidates) == 1:
            score = SequenceMatcher(None, detected_ic, candidates[0]["ic"]).ratio()
            return candidates[0], score
        elif len(candidates) > 1:
            best = None
            best_score = 0
            for s in candidates:
                score = SequenceMatcher(None, detected_ic, s["ic"]).ratio()
                if score > best_score:
                    best_score = score
                    best = s
            if best_score >= threshold:
                return best, best_score

    # Partial fallback
    ic_pattern_partial = r'\b(\d{6})\b'
    matches_partial = re.findall(ic_pattern_partial, ocr_text)
    if matches_partial:
        for partial in matches_partial:
            candidates = [s for s in student_list if len(s["ic"]) == 12 and s["ic"][:6] == partial]
            if len(candidates) == 1:
                return candidates[0], 0.75

    return None, 0

# ===== SEND FEEDBACK TO ESP32 =====
def send_feedback(status):
    try:
        requests.get(f"{ESP32_IP}/feedback?status={status}", timeout=2)
    except:
        pass

# ===== GOOGLE SHEETS LOGGING =====
def log_to_sheets(student):
    global today_logged_ics
    name = student["name"]
    ic = student["ic"]
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    if sheet is None:
        print(f"📝 [DRY RUN] Would log: {name} ({ic}) at {timestamp}")
        return True

    if ic in today_logged_ics:
        print(f"⏭️  {name} already logged today — skipping duplicate")
        return False

    try:
        sheet.append_row([name, ic, timestamp])
        today_logged_ics.add(ic)
        print(f"✅ Logged to Google Sheets: {name} ({ic})")
        return True
    except Exception as e:
        print(f"❌ Google Sheets error: {e}")
        return False

# ===== PROCESS IMAGE (RAPIDOCR VERSION) =====
def process_image(image_bytes):
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None

        # 1. Rotate 180 because your box is upside down (Only use this if your camera is mounted upside down)
        img = cv2.rotate(img, cv2.ROTATE_180)

        # 2. Upscale 2x (Helps RapidOCR read small text)
        img = cv2.resize(img, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)

        # 3. Run RapidOCR directly (No thresholding or erosion needed!)
        result = ocr_engine(img)

        if result.txts is None:
            return None

        full_text = " ".join(result.txts)
        return full_text.strip()

    except Exception as e:
        print(f"❌ Processing error: {e}")
        return None

# ===== STARTUP =====
print("🚀 OCR System Ready (RapidOCR + Strict Matching + Duplicate Check)")
print(f"📡 ESP32: {ESP32_IP}")
print(f"📄 JSON: {JSON_FILE}")
print(f"⏳ Waiting {STARTUP_SYNC_DELAY} seconds for Google Sheets to sync...")
time.sleep(STARTUP_SYNC_DELAY)
load_todays_logs()

print("\n✅ Ready. Press button on ESP32... (Ctrl+C to stop)\n")

# ===== MAIN LOOP =====
while True:
    try:
        resp = requests.get(f"{ESP32_IP}/capture", timeout=10)

        # Handle ESP32 memory failure / crashes
        if resp.status_code != 200:
            print("⚠️ ESP32 camera failed to capture. Retrying...")
            time.sleep(POLL_INTERVAL)
            continue

        version_str = resp.headers.get("X-Image-Version")
        if version_str is None:
            print("⚠️ No version header!")
            time.sleep(POLL_INTERVAL)
            continue

        current_version = int(version_str)
        if current_version == last_processed_version:
            print(".", end="", flush=True)
            time.sleep(POLL_INTERVAL)
            continue

        print(f"\n\n📸 ===== New image! (Version: {current_version}) =====")
        last_processed_version = current_version

        students = load_students()
        if not students:
            print("⚠️ No students in JSON.")
            send_feedback("fail")
            continue

        ocr_text = process_image(resp.content)

        if not ocr_text or len(ocr_text.strip()) < 2:
            print("❌ OCR returned nothing.")
            send_feedback("fail")
            continue

        print("--- 📝 OCR Raw Text ---")
        print(ocr_text)
        print("----------------------")

        # ===== MATCHING LOGIC =====
        print("\n--- MATCHING ---")
        matched = match_ic(ocr_text, students)
        if matched:
            print(f"✅ EXACT IC MATCH: {matched['name']} ({matched['ic']})")
            send_feedback("success")
            log_to_sheets(matched)
        else:
            matched, ic_score = fuzzy_match_ic(ocr_text, students)
            if matched:
                print(f"✅ FUZZY IC MATCH: {matched['name']} ({matched['ic']}) [Score: {ic_score:.2f}]")
                send_feedback("success")
                log_to_sheets(matched)
            else:
                matched, name_score = fuzzy_match_name(ocr_text, students)
                if matched:
                    print(f"✅ NAME MATCH: {matched['name']} ({matched['ic']}) [Score: {name_score:.2f}]")
                    send_feedback("success")
                    log_to_sheets(matched)
                else:
                    print("❌ No match found in JSON.")
                    send_feedback("fail")

        print("⏳ Waiting for next press...\n")

    except requests.exceptions.ConnectionError:
        print("\n❌ ESP32 unreachable. Check IP.")
        time.sleep(POLL_INTERVAL)
    except requests.exceptions.Timeout:
        print("\n⏱️ Request timed out.")
        time.sleep(POLL_INTERVAL)
    except Exception as e:
        print(f"\n⚠️ Error: {e}")
    time.sleep(0.1)
