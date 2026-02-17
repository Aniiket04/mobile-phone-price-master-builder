#!/usr/bin/env python3
"""
gsmarena_launch_date_scraper.py

Personal-portfolio style GSM Arena launch-date scraper.

Features:
- Fresh / Resume / Error-list modes
- Robust safe_get with optional proxy rotation
- Precise model matching (prefix-based)
- Conservative launch-date extraction (regex around 'announced'/'launched')
- Periodic save and manual save (Ctrl+S)
- Neutral naming (no company-specific references)
"""

# ----------------------------
# IMPORTS
# ----------------------------
# Standard library imports for regex, timing, randomization, and system operations
import re
import time
import random
import os
import sys
import traceback

# GUI imports for file selection dialogs
import tkinter as tk
from tkinter import filedialog

# Data handling
import pandas as pd

# Selenium imports for web automation
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException

# Keyboard hook for manual save interruption (Ctrl+S)
import keyboard  # pip install keyboard

# Date and threading utilities
from datetime import datetime
import threading

# ----------------------------
# CONFIGURATION
# ----------------------------
# Toggles for browser visibility and testing scope
HEADLESS_MODE = False
TEST_MODE = False
TEST_N = 5  # Number of rows to process in test mode

# Intervals for browser refreshing and data saving to prevent stale sessions/data loss
REFRESH_EVERY = 80
SAVE_EVERY = 100
MAX_RETRIES = 3

# Path to the specific ChromeDriver executable
CHROMEDRIVER_PATH = r"C:\Users\anike\OneDrive\Project\chromedriver-win64\chromedriver.exe"

# Debugging configuration: Enable screenshots on errors for visual debugging
DEBUG_SAVE_SCREENSHOT = True
SCREENSHOT_DIR = os.path.join(os.getcwd(), "debug_screenshots_gsm")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Optional proxy list for IP rotation (currently empty placeholder)
PROXY_LIST = [
    # "host:port",
    # "user:pass@host:port"
]

# Flag to prevent multiple manual saves from overlapping
_manual_save_in_progress = False

# ----------------------------
# HELPER FUNCTIONS
# ----------------------------

def log(msg):
    """
    Prints messages to the console with a precise timestamp.
    Helps in tracking the execution flow and debugging timing issues.
    """
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def save_screenshot(driver, name_prefix="error"):
    """
    Captures a screenshot of the current browser state.
    Used primarily when exceptions occur or elements aren't found.
    """
    if not DEBUG_SAVE_SCREENSHOT:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(SCREENSHOT_DIR, f"{name_prefix}_{ts}.png")
    try:
        driver.save_screenshot(path)
        log(f"📸 Screenshot saved: {path}")
        return path
    except Exception as e:
        log(f"❌ Failed to save screenshot: {e}")
        return None

def normalize_text_spaces(s: str):
    """
    Cleans text by converting to lowercase, replacing dashes with spaces,
    and collapsing multiple spaces into one. Essential for consistent string matching.
    """
    return re.sub(r"\s+", " ", (s or "").lower().replace("-", " ")).strip()

def model_matches_title(make_model: str, title: str) -> bool:
    """
    Validates if a search result title matches the requested model.
    Uses strict prefix-based matching to avoid partial matches (e.g., preventing
    'iPhone 13' from matching 'iPhone 13 Pro').
    """
    mm = normalize_text_spaces(make_model)
    tt = normalize_text_spaces(title)
    if not mm or not tt:
        return False
    model_tokens = mm.split()
    title_tokens = tt.split()
    n = len(model_tokens)
    
    # If the title is shorter than the search query, it's not a match
    if n == 0 or len(title_tokens) < n:
        return False
    
    # Check if the title starts with the model name tokens
    if title_tokens[:n] == model_tokens:
        # Exact match
        if len(title_tokens) == n:
            return True
        # Allow '5g' suffix (common in phone naming conventions)
        if title_tokens[n] == "5g":
            return True
        
        # Reject if the next word indicates a different variant (Pro, Max, etc.)
        variant_keywords = {'pro','max','mini','plus','ultra','lite','fe','edge','note','fold','flip','se'}
        if title_tokens[n] in variant_keywords:
            return False
        return True
    return False

# ----------------------------
# DRIVER SETUP + PROXY MANAGEMENT
# ----------------------------

def get_random_proxy():
    """Selects a random proxy from the list if available."""
    return random.choice(PROXY_LIST) if PROXY_LIST else None

def init_driver(proxy=None):
    """
    Initializes the Chrome WebDriver with specific anti-detection and performance options.
    """
    options = Options()
    if HEADLESS_MODE:
        options.add_argument("--headless")
    
    # Standard settings for stability and evasion
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Mock user-agent to look like a standard browser
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    options.add_argument(f"user-agent={ua}")
    
    # Remove automation flags that Selenium usually adds
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    if proxy:
        options.add_argument(f"--proxy-server=http://{proxy}")
        log(f"🌐 Using proxy: {proxy}")
    else:
        log("🌐 No proxy (direct connection)")

    try:
        service = Service(CHROMEDRIVER_PATH)

        # 🟢 Performance Optimization: Eager load strategy doesn't wait for all images/stylesheets
        options.page_load_strategy = "eager"   # <-- add this line

        driver = webdriver.Chrome(service=service, options=options)

        # 🔥 CRITICAL STABILITY FIX:
        # Prevents the Selenium client from freezing if the ChromeDriver socket hangs.
        # This ensures the script can recover even if the browser process becomes unresponsive.
        try:
            driver.command_executor.set_timeout(20)   # seconds
        except Exception:
            # Fallback for different Selenium versions
            try:
                driver.command_executor._conn.timeout = 20
            except Exception:
                pass

        # Standard timeouts for page loading and script execution
        driver.set_page_load_timeout(20)
        driver.set_script_timeout(20)

        # JavaScript injection to hide the 'navigator.webdriver' property (anti-bot detection)
        try:
            driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            })
        except Exception:
            pass
        return driver
    
    except Exception as e:
        log(f"❌ Failed to start ChromeDriver: {e}")
        raise

def restart_driver_with_new_proxy(old_driver):
    """
    Gracefully quits the current driver and starts a new one with a fresh proxy.
    Used when retries are exhausted or purely for rotation.
    """
    try:
        old_driver.quit()
    except:
        pass
    proxy = get_random_proxy()
    return init_driver(proxy)

# ----------------------------
# SAFE NAVIGATION LOGIC
# ----------------------------

def safe_get(url, driver, retries=MAX_RETRIES, backoff=1.5):
    """
    A robust wrapper around driver.get() that handles timeouts and socket errors.
    """
    attempt = 1

    while attempt <= retries:
        try:
            driver.get(url)
            # Randomized sleep to mimic human behavior
            time.sleep(random.uniform(2, 4))
            return True, driver

        except TimeoutException:
            log(f"⏱️ Page load timeout for {url} (attempt {attempt}/{retries})")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
            save_screenshot(driver, "timeout")
            attempt += 1
            time.sleep(backoff * attempt)

        except WebDriverException as e:
            msg = str(e).lower()
            log(f"⚠️ WebDriver error for {url}: {e}")

            # 🔴 CRITICAL RECOVERY:
            # If the driver socket is "poisoned" (disconnected/timed out), standard retries won't work.
            # We must restart the entire browser process immediately.
            if "read timed out" in msg or "httpconnectionpool" in msg:
                log("💥 ChromeDriver socket poisoned — restarting driver immediately")
                save_screenshot(driver, "driver_poisoned")

                try:
                    driver.quit()
                except Exception:
                    pass

                driver = restart_driver_with_new_proxy(driver)
                return False, driver

            # Standard WebDriver errors -> retry with backoff
            attempt += 1
            time.sleep(backoff * attempt)

    # If all retries fail, restart the driver to clear any state issues
    log("♻️ Retries exhausted — restarting driver")
    try:
        driver.quit()
    except Exception:
        pass
    driver = restart_driver_with_new_proxy(driver)
    return False, driver

# ----------------------------
# GSMARENA SPECIFIC LOGIC
# ----------------------------

from urllib.parse import quote_plus
def qencode(s):
    """URL encodes the search string."""
    return quote_plus(s)

def search_gsmarena_selenium(driver, make_model):
    """
    Performs a search on GSMArena and parses the results to find a matching device URL.
    """
    q = qencode(make_model)
    search_url = f"https://www.gsmarena.com/results.php3?sQuickSearch=yes&sName={q}"
    success, driver = safe_get(search_url, driver)
    if not success:
        return None, driver

    # Extract all device links from the results container
    try:
        links = driver.find_elements(By.CSS_SELECTOR, "div.makers a")
    except Exception:
        links = []

    best_url = None
    for a in links:
        try:
            # Attempt to get the device name from the span or text
            title_el = None
            try:
                title_el = a.find_element(By.TAG_NAME, "span")
                title = title_el.text.strip()
            except Exception:
                title = a.text.strip()
            
            href = a.get_attribute("href")
            if not title or not href:
                continue
            
            # Check if this result matches our specific model requirements
            if model_matches_title(make_model, title):
                best_url = href if href.startswith("http") else "https://www.gsmarena.com/" + href
                log(f"[GSMArena] Matched '{title}' -> {best_url}")
                break
        except Exception:
            continue

    return best_url, driver

def get_launch_from_gsmarena_selenium(driver, detail_url):
    """
    Visits a specific device page and extracts the launch date using Regex.
    Scans for keywords like 'announced' or 'launched'.
    """
    success, driver = safe_get(detail_url, driver)
    if not success:
        return None, driver

    try:
        body_text = driver.find_element(By.TAG_NAME, "body").text
    except Exception:
        body_text = driver.page_source or ""

    lowered = body_text.lower()
    
    # Locate the "announced" or "release" section in the text
    idx = lowered.find("announced")
    anchors = ["announced", "launched", "release date", "release"]
    
    # Create a small text window around the keyword to narrow down the Regex search
    window = body_text
    if idx != -1:
        start = max(0, idx - 120)
        end = min(len(body_text), idx + 260)
        window = body_text[start:end]
    else:
        # Fallback: look for other anchor words
        for a_word in anchors:
            ai = lowered.find(a_word)
            if ai != -1:
                start = max(0, ai - 120)
                end = min(len(body_text), ai + 260)
                window = body_text[start:end]
                break

    # Regex patterns ordered from most specific (full date) to least specific (year only)
    patterns = [
        r"\b(\d{4}\s*,?\s*[A-Za-z]+\s+\d{1,2})\b",  # 2021, March 23 or 2021 March 23
        r"\b(\d{1,2}\s+[A-Za-z]+\s+\d{4})\b",       # 23 March 2021
        r"\b([A-Za-z]+\s+\d{1,2}\s*,?\s*\d{4})\b",  # March 23, 2021
        r"\b(\d{4}\s*,?\s*[A-Za-z]+)\b",            # 2021 March
        r"\b([A-Za-z]+\s+\d{4})\b",                 # March 2021
        r"\b(\d{4})\b",                             # 2021
    ]

    for pat in patterns:
        m = re.search(pat, window, re.I)
        if m:
            return m.group(1).strip(), driver

    return None, driver

# ----------------------------
# MODEL PROCESSING HANDLER
# ----------------------------

def fetch_launch_for_model_selenium(driver, make_model):
    """
    Orchestrates the search and extraction for a single model.
    Returns: (date_str, source_name, url, driver_instance)
    """
    date = source = url = None

    try:
        url_g, driver = search_gsmarena_selenium(driver, make_model)
        if url_g:
            date, driver = get_launch_from_gsmarena_selenium(driver, url_g)
            if date:
                return date, "GSMArena", url_g, driver
    except Exception as e:
        # Log error and take screenshot, but don't crash the whole script
        log(f"Error with GSMArena for '{make_model}': {e}")
        save_screenshot(driver, f"gsmarena_error_{make_model[:20]}")

    return None, None, None, driver

# ----------------------------
# DATA PERSISTENCE
# ----------------------------

def save_progress_launch(df_master, file_path):
    """Saves the DataFrame to Excel, overwriting the 'Master' sheet."""
    try:
        with pd.ExcelWriter(file_path, mode="a", engine="openpyxl", if_sheet_exists="replace") as writer:
            df_master.to_excel(writer, sheet_name="Master", index=False)
        log("💾 Progress saved successfully!")
    except Exception as e:
        log(f"❌ Error during saving: {e}")

# Thread callback to avoid blocking main loop
def manual_save_thread(df_master_copy, file_path):
    try:
        save_progress_launch(df_master_copy, file_path)
    except Exception:
        traceback.print_exc()

def manual_save(df_master, file_path):
    """
    Triggered by Ctrl+S. Creates a deep copy of the data and saves it in a separate thread
    so the scraping process isn't interrupted.
    """
    global _manual_save_in_progress
    if _manual_save_in_progress:
        return
    _manual_save_in_progress = True
    try:
        df_copy = df_master.copy(deep=True)
        t = threading.Thread(target=manual_save_thread, args=(df_copy, file_path), daemon=True)
        t.start()
    except Exception as e:
        log(f"❌ Manual save failed to start: {e}")
        _manual_save_in_progress = False

# ----------------------------
# MAIN EXECUTION
# ----------------------------

def get_file_path_dialog():
    """Attempts to get the file path via CLI args, GUI dialog, or manual input."""
    # 1) CLI arg
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if os.path.exists(file_path) and file_path.lower().endswith(('.xlsx', '.xls')):
            log(f"📂 Using file from command line: {file_path}")
            return file_path
        else:
            log(f"⚠️ Invalid file path provided: {file_path}")

    # 2) File dialog
    try:
        root = tk.Tk()
        root.withdraw()
        file_path = filedialog.askopenfilename(
            title="Select Master Excel File",
            filetypes=[("Excel files", "*.xlsx *.xls")]
        )
        root.destroy()
        if file_path:
            log(f"📂 Selected file: {file_path}")
            return file_path
    except Exception as e:
        log(f"⚠️ File dialog not available: {e}")

    # 3) Manual input
    log("\n" + "="*60)
    log("Please provide the Excel file path (or drag & drop):")
    log("="*60)
    try:
        file_path = input("Enter file path: ").strip().strip('"').strip("'")
        if os.path.exists(file_path) and file_path.lower().endswith(('.xlsx', '.xls')):
            return file_path
        log("❌ File not found or invalid format.")
    except KeyboardInterrupt:
        log("Operation cancelled.")
    return None

def availability_label(date_str, source):
    """Determines the status label based on whether a date was found."""
    if date_str and source:
        return "Found"
    elif (not date_str) and source:
        return "No exact date"
    else:
        return "Not found"

def main():
    tk.Tk().withdraw()
    file_path = get_file_path_dialog()
    if not file_path:
        raise SystemExit("No file selected — exiting.")

    log(f"Selected file: {file_path}")
    try:
        df_master = pd.read_excel(file_path, sheet_name="Master")
    except Exception as e:
        log(f"❌ Error reading Master sheet: {e}")
        raise SystemExit(1)

    # Normalize column name for consistency
    if "Make Model" in df_master.columns and "Make-Model" not in df_master.columns:
        df_master.rename(columns={"Make Model": "Make-Model"}, inplace=True)
    elif "Make-Model" not in df_master.columns:
        raise SystemExit("Master sheet must contain 'Make Model' or 'Make-Model' column.")

    # Ensure launch columns exist in the DataFrame
    for col in ["Launch_Date_India", "Launch_Source", "Launch_URL", "Launch_Availability", "Launch_Date_Scrapped"]:
        if col not in df_master.columns:
            df_master[col] = ""

    # Mode selection: Fresh vs Resume
    choice = input("\nChoose mode:\n1 — Fresh Start (scrape all)\n2 — Resume (scrape remaining only)\nEnter 1 or 2: ").strip()
    if choice == "1":
        log("Mode: Fresh Start — clearing launch columns and flags")
        df_master["Launch_Date_India"] = ""
        df_master["Launch_Source"] = ""
        df_master["Launch_URL"] = ""
        df_master["Launch_Availability"] = ""
        df_master["Launch_Date_Scrapped"] = "No"
    else:
        log("Mode: Resume — scraping only remaining")
        df_master["Launch_Date_Scrapped"] = df_master["Launch_Date_Scrapped"].replace("", "No")

    # Test mode: limits the run to a small number of rows
    if TEST_MODE:
        df_master = df_master.head(TEST_N)
        log(f"TEST_MODE ON — processing first {len(df_master)} rows")

    # Optional error-list: retry specific models from a separate error file
    use_error_list = input("Run only for error-list models? (y/n): ").strip().lower() == "y"
    error_models = None
    if use_error_list:
        err_path = filedialog.askopenfilename(title="Select Error List", filetypes=[("Excel files", "*.xlsx"),("CSV", "*.csv")])
        if err_path:
            try:
                if err_path.lower().endswith(".csv"):
                    df_err = pd.read_csv(err_path)
                else:
                    df_err = pd.read_excel(err_path)
                # expect column with Make-Model or similar
                col_candidates = [c for c in df_err.columns if "make" in c.lower() or "model" in c.lower()]
                if col_candidates:
                    error_models = set(df_err[col_candidates[0]].astype(str).str.strip().tolist())
                    log(f"Loaded {len(error_models)} error-list models")
                else:
                    log("No suitable column found in error list; ignoring.")
            except Exception as e:
                log(f"Could not load error list: {e}")

    # Initialize WebDriver
    driver = init_driver(get_random_proxy())

    # Bind manual save hotkey (Ctrl+S)
    try:
        keyboard.add_hotkey('ctrl+s', lambda: manual_save(df_master, file_path))
        log("Press Ctrl+S anytime to manually save progress.")
    except Exception:
        log("Manual hotkey binding unavailable (keyboard).")

    # Main Scraping Loop
    try:
        for idx, row in df_master.iterrows():
            try:
                make_model = str(row.get("Make-Model", "")).strip()
                if not make_model:
                    df_master.loc[idx, "Launch_Date_Scrapped"] = "Yes"
                    continue

                # Skip if already scraped in resume mode
                if str(row.get("Launch_Date_Scrapped", "")).strip().lower() == "yes":
                    continue

                # Filter by error-list if active
                if error_models and make_model not in error_models:
                    df_master.loc[idx, "Launch_Date_Scrapped"] = row.get("Launch_Date_Scrapped", "")
                    continue

                log(f"--- [{idx}] Getting launch date for: {make_model}")

                # Refresh GSMArena homepage periodically to keep session fresh
                if idx > 0 and idx % REFRESH_EVERY == 0:
                    log("🔄 Periodic refresh of GSMArena home")
                    success, driver = safe_get("https://www.gsmarena.com", driver)
                    # continue regardless of success

                date_str = source = url = None
                # Only GSMArena
                try:
                    d, s, u, driver = fetch_launch_for_model_selenium(driver, make_model)
                    date_str, source, url = d, s, u
                except Exception as e:
                    log(f"Exception while fetching: {e}")

                # Update DataFrame with results
                df_master.loc[idx, "Launch_Date_India"] = date_str or ""
                df_master.loc[idx, "Launch_Source"] = source or ""
                df_master.loc[idx, "Launch_URL"] = url or ""
                df_master.loc[idx, "Launch_Availability"] = availability_label(date_str, source)
                df_master.loc[idx, "Launch_Date_Scrapped"] = "Yes"

                log(f"Result -> Date: {date_str} | Source: {source} | URL: {url}")

                # Periodic Save & Proxy Rotation
                if idx > 0 and idx % SAVE_EVERY == 0:
                    log("Periodic save and restart (rotating proxy).")
                    save_progress_launch(df_master, file_path)
                    try:
                        driver.quit()
                    except:
                        pass
                    driver = restart_driver_with_new_proxy(driver)

            except Exception as e:
                log("❌ Exception in per-row loop — logged and continuing")
                traceback.print_exc()
                save_screenshot(driver, name_prefix=f"exception_launch_{idx}")
                df_master.loc[idx, "Launch_Date_Scrapped"] = "Yes"
                time.sleep(random.uniform(1.0, 3.0))
                continue

    finally:
        # Cleanup: Quit driver and perform final save
        try:
            driver.quit()
        except:
            pass
        save_progress_launch(df_master, file_path)
        log("✅ Done — Launch date scraping complete!")

if __name__ == "__main__":
    main()
