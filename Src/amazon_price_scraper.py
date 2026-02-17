# Amazon Product Price Scraper 
# A personal project for automated price monitoring on Amazon
# Description: Scrapes product prices from Amazon search results with advanced filtering and variant detection
# FEATURES:
# - Multiple scraping modes: Fresh Start, Resume
# - Separate extraction of selling prices and MRPs (Maximum Retail Price)
# - Intelligent product matching (fuzzy logic with token overlap)
# - Variant-aware price aggregation (lowest/highest selling price, max MRP)
# - Non-blocking manual save with Ctrl+S
# - Anti-detection measures: Random user agents, periodic refreshes, random delays
# - Outlier filtering and sanity checks for prices

import re
import time
import random
import os
import sys
import traceback
import tkinter as tk
from tkinter import filedialog
import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import keyboard  # pip install keyboard
from datetime import datetime
import threading  # For non-blocking manual save

# ====================================
# CONFIGURATION SETTINGS
# ====================================
# Visibility settings
HEADLESS_MODE = False  # Set to False to SEE the browser in action (recommended for debugging)
                       # Set to True to run invisibly in background

# Test mode settings - useful for debugging
TEST_MODE = False  # Set to True to scrape only a small subset of products
TEST_N = 5  # Number of products to scrape in test mode

# Scraping limits and intervals
PRODUCTS_TO_CHECK = 8  # Maximum number of search results to process per model
REFRESH_EVERY = 80  # Refresh browser after this many searches to avoid detection
SAVE_EVERY = 100  # Save progress after processing this many products
MAX_RETRIES = 3  # Maximum number of retry attempts for failed operations

# Keyword filtering - exclude accessories and non-relevant items to ensure we scrape PHONES
EXCLUDE_KEYWORDS = [
    "cover", "case", "charger", "screen protector", "cable", "earphone",
    "headphone", "tempered glass", "skin", "stand", "bag", "adapter",
    "power bank", "holder", "mount", "pouch", "warranty", "insurance"
]

# Chrome WebDriver configuration
# Update this path to point to your ChromeDriver executable location
CHROMEDRIVER_PATH = r"C:\Users\anike\OneDrive\Project\chromedriver-win64\chromedriver.exe"

# Debug screenshot settings - helpful for troubleshooting selector issues
DEBUG_SAVE_SCREENSHOT = True
SCREENSHOT_DIR = os.path.join(os.getcwd(), "debug_screenshots_amazon")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Global flag to prevent concurrent manual saves
_manual_save_in_progress = False

# ====================================
# UTILITY FUNCTIONS
# ====================================

def log(msg):
    """Print timestamped log messages for better tracking"""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def only_digits_int(s: str):
    """Extract only numeric digits from a string and convert to integer"""
    if not s:
        return 0
    nums = re.sub(r"[^\d]", "", str(s))
    return int(nums) if nums else 0

def extract_price(price_text: str):
    """Parse price text and extract numeric value"""
    try:
        return only_digits_int(price_text)
    except:
        return 0

def save_screenshot(driver, name_prefix="error"):
    """
    Save a screenshot for debugging purposes.
    Useful when selectors fail or prices aren't found.
    """
    if not DEBUG_SAVE_SCREENSHOT:
        return
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{name_prefix}_{timestamp}.png"
        filepath = os.path.join(SCREENSHOT_DIR, filename)
        driver.save_screenshot(filepath)
        log(f"  📸 Screenshot: {filename}")
    except Exception as e:
        log(f"  ⚠️ Screenshot failed: {e}")

def safe_get(url, driver, max_retries=3):
    """
    Load a URL with retry logic and exponential backoff.
    Handles network issues and temporary site unavailability.
    """
    for attempt in range(max_retries):
        try:
            driver.get(url)
            time.sleep(random.uniform(2.0, 4.0))  # Random delay to mimic human behavior
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                log(f"  ⚠️ Retry {attempt+1}/{max_retries}")
                time.sleep(random.uniform(3, 5))
            else:
                log(f"  ❌ Navigation failed: {e}")
                return False
    return False

# ====================================
# MATCHING LOGIC
# ====================================

def normalize_for_matching(s: str) -> str:
    """
    Aggressive normalization for string matching.
    Removes common keywords ('5g', 'mobile') to focus on the specific model name.
    
    Args:
        s: Input string (search query or product title)
        
    Returns:
        Normalized string with noise words removed
    """
    if not s:
        return ""
    
    s = s.lower()
    
    # List of generic words that don't help in distinguishing specific models
    junk_words = [
        'sponsored', 'visit', 'the', 'store', 'brand', 'new', 'original',
        'genuine', 'authentic', 'official', 'latest', 'smartphone', 'mobile',
        'phone', 'cell', 'dual', 'sim', '5g', '4g', 'lte', 'volte'
    ]
    
    # Remove junk words
    for word in junk_words:
        s = re.sub(r'\b' + word + r'\b', ' ', s)
    
    # Remove punctuation and specs like "128GB" which might differ between title and search
    s = re.sub(r'[^\w\s]', ' ', s)
    s = re.sub(r'\d+\s*(gb|tb|ram|rom|mah)', ' ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    
    return s

def simple_match(search_query: str, product_title: str) -> tuple:
    """
    Check if product title matches search query using token overlap.
    
    Strategy:
    1. Normalize both strings (remove "5G", "Mobile", specs).
    2. Check how many words from the search query exist in the product title.
    3. Require at least 70% of search terms to be present.
    4. Check for critical variant mismatches (e.g., searching for "Pro" but finding non-Pro).
    
    Returns:
        tuple: (is_match (bool), reason (str), score (float))
    """
    search_norm = normalize_for_matching(search_query)
    title_norm = normalize_for_matching(product_title)
    
    if not search_norm or not title_norm:
        return (False, "Empty input", 0.0)
    
    # Split into individual words (tokens)
    search_tokens = [t for t in search_norm.split() if len(t) > 1]
    title_tokens = title_norm.split()
    
    if not search_tokens:
        return (False, "No valid search tokens", 0.0)
    
    matches = 0
    missing = []
    
    # Calculate overlap
    for token in search_tokens:
        found = False
        for title_token in title_tokens:
            if token in title_token or title_token in token:
                found = True
                break
        
        if found:
            matches += 1
        else:
            missing.append(token)
    
    # Calculate match score
    match_percentage = matches / len(search_tokens) if search_tokens else 0.0
    
    # Threshold check
    if match_percentage < 0.7:
        return (False, f"Only {matches}/{len(search_tokens)} tokens match. Missing: {missing}", match_percentage)
    
    # Variant safeguards: Ensure we don't match "iPhone 13" with "iPhone 13 Pro" if not requested
    variant_keywords = ['pro', 'max', 'mini', 'plus', 'ultra', 'lite', 'fe', 'edge', 'note']
    search_lower = search_query.lower()
    
    for variant in variant_keywords:
        if variant in title_norm and variant not in search_lower:
            return (False, f"Has variant '{variant}' not in search", 0.3)
    
    return (True, f"Match: {matches}/{len(search_tokens)} tokens ({match_percentage:.0%})", match_percentage)

def is_mobile_phone_product(driver):
    """
    Verify if current product page is actually a mobile phone.
    Checks breadcrumbs and page content to avoid scraping laptops or accessories.
    """
    try:
        # Check Amazon breadcrumbs
        breadcrumbs = driver.find_elements(By.XPATH, "//div[@id='wayfinding-breadcrumbs_feature_div']//a")
        for bc in breadcrumbs:
            text = bc.text.lower()
            if 'mobile' in text or 'phone' in text or 'smartphone' in text:
                return True
        
        # Fallback: Check body text for negative keywords (laptop, notebook)
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            if ('laptop' in page_text[:500] or 'notebook' in page_text[:500]):
                # Allow if it also explicitly mentions mobile/phone
                if 'mobile' not in page_text[:300] and 'phone' not in page_text[:300]:
                    return False
        except:
            pass
        
        return True
    except:
        return True

# ====================================
# SEARCH PAGE EXTRACTION
# ====================================

def extract_product_links_minimal_filter(driver):
    """
    Extract product links from Amazon search results.
    Uses multiple selector strategies to handle Amazon's dynamic DOM.
    Filters out obvious accessories based on title keywords.
    """
    log(f"  → Extracting product links")
    
    all_anchors = []
    
    # Strategy 1: Standard search result cards
    try:
        cards = driver.find_elements(By.XPATH, "//div[@data-component-type='s-search-result']")
        if cards:
            log(f"  ✓ Found {len(cards)} product cards")
            for card in cards:
                try:
                    # Find the main link within the card
                    link = card.find_element(By.XPATH, ".//h2//a[contains(@class, 'a-link-normal')]")
                    all_anchors.append(link)
                except:
                    pass
    except:
        pass
    
    # Strategy 2: Fallback to generic link classes
    if not all_anchors:
        try:
            anchors = driver.find_elements(By.XPATH, "//a[contains(@class, 'a-link-normal') and contains(@class, 's-underline-text')]")
            if anchors:
                log(f"  ✓ Found {len(anchors)} products (fallback)")
                all_anchors = anchors
        except:
            pass
    
    # Strategy 3: URL pattern matching (/dp/ = detail page)
    if not all_anchors:
        try:
            potential = driver.find_elements(By.XPATH, "//a[contains(@href, '/dp/')]")
            for a in potential:
                href = a.get_attribute("href") or ""
                # Avoid review links
                if '/dp/' in href and '#customerReviews' not in href:
                    all_anchors.append(a)
            if all_anchors:
                log(f"  ✓ Found {len(all_anchors)} products (/dp/ fallback)")
        except:
            pass
    
    if not all_anchors:
        log("  ❌ No product links found")
        return []
    
    # Filter accessories (cases, chargers, etc.)
    filtered_anchors = []
    accessories_skipped = 0
    
    for anchor in all_anchors:
        try:
            card_text = anchor.text.strip().lower()
            # Check against exclusion list
            is_accessory = any(keyword in card_text for keyword in EXCLUDE_KEYWORDS)
            
            if not is_accessory:
                filtered_anchors.append(anchor)
            else:
                accessories_skipped += 1
        except:
            filtered_anchors.append(anchor)
    
    log(f"  ✓ Kept {len(filtered_anchors)} products (filtered {accessories_skipped} accessories)")
    
    return filtered_anchors

# ====================================
# PRODUCT PAGE EXTRACTION - FIXED
# ====================================

def extract_clean_title_from_product_page(driver):
    """
    Extract the main product title from the detail page.
    Tries standard ID first, then fallback to H1 tag.
    """
    try:
        elem = driver.find_element(By.ID, "productTitle")
        title = elem.text.strip()
        if title:
            return title
    except:
        pass
    
    try:
        elem = driver.find_element(By.TAG_NAME, "h1")
        title = elem.text.strip()
        if title:
            return title
    except:
        pass
    
    return ""

def is_valid_phone_price(price: int) -> bool:
    """
    Validate if a price is within reasonable range for mobile phones.
    Helps filter out random numbers (e.g., zip codes) or accessory prices.
    """
    return 3000 <= price <= 200000

def is_reasonable_mrp(selling_price: int, mrp: int) -> bool:
    """
    Validate if MRP makes sense relative to selling price.
    MRP should be:
    - Higher than selling price
    - Not more than 3x the selling price (unrealistic discount)
    - Not less than selling price
    """
    if mrp <= 0 or selling_price <= 0:
        return False
    
    # MRP should be higher than selling price
    if mrp <= selling_price:
        return False
    
    # MRP shouldn't be more than 3x selling price (too high)
    if mrp > selling_price * 3:
        return False
    
    return True

def extract_prices_from_product_page(driver):
    """
    FIXED: Extract selling price and MRP with validation.
    
    Logic:
    1. Find Selling Price (The large, main price).
    2. Find MRP (The crossed-out price).
    3. Validate that MRP > Selling Price and is reasonable.
    
    Returns: 
        tuple: (selling_price, mrp)
    """
    selling_prices = []
    mrp_prices = []
    
    # Extract selling price FIRST (more reliable)
    try:
        # Common Amazon price selectors
        main_sels = [
            "//span[@class='a-price aok-align-center reinventPricePriceToPayMargin priceToPay']//span[@class='a-offscreen']",
            "//span[@class='a-price-whole']",
            "//span[@class='a-price']//span[@class='a-offscreen']"
        ]
        
        for sel in main_sels:
            try:
                elems = driver.find_elements(By.XPATH, sel)
                # Only check FIRST element to avoid grabbing wrong prices (e.g., "Save X amount")
                if elems:
                    elem = elems[0]
                    text = elem.get_attribute("textContent").strip()
                    if not text:
                        text = elem.text.strip()
                    price_val = extract_price(text)
                    if is_valid_phone_price(price_val):
                        selling_prices.append(price_val)
                        log(f"  → Selling Price: ₹{price_val}")
                        break  # Stop after finding first valid price
            except:
                continue
    except:
        pass
    
    # Extract MRP (strikethrough) - ONLY if we have selling price
    if selling_prices:
        selling_price = min(selling_prices)
        
        try:
            # Look for strikethrough prices
            strikethrough_sels = [
                "//span[contains(@class, 'a-text-price')]//span[@class='a-offscreen']",
                "//span[@data-a-strike='true']//span[@class='a-offscreen']",
                "//span[@data-a-strike='true']"
            ]
            
            for sel in strikethrough_sels:
                try:
                    elems = driver.find_elements(By.XPATH, sel)
                    for elem in elems:
                        text = elem.get_attribute("textContent").strip()
                        if not text:
                            text = elem.text.strip()
                        if '₹' in text:
                            price_val = extract_price(text)
                            # Validate: MRP should be reasonable relative to selling price
                            if is_valid_phone_price(price_val) and is_reasonable_mrp(selling_price, price_val):
                                mrp_prices.append(price_val)
                                log(f"  → MRP: ₹{price_val}")
                except:
                    continue
        except:
            pass
    
    # Get final prices
    selling_price = min(selling_prices) if selling_prices else 0
    mrp_value = max(mrp_prices) if mrp_prices else 0
    
    # If no valid MRP found, don't use selling price as MRP
    # This prevents showing same price for both
    
    return (selling_price, mrp_value)

def extract_variant_links(driver):
    """
    FIXED: Extract variant links with better detection.
    Now checks multiple selectors for color/storage variants.
    Essential for getting price ranges (e.g., 128GB vs 256GB).
    """
    variant_links = []
    
    # Try multiple Amazon variant selectors
    variant_selectors = [
        # Color variants
        "//div[@id='variation_color_name']//li//a",
        "//li[contains(@id, 'color_name')]//a",
        # Size/Storage variants
        "//div[@id='variation_size_name']//li//a",
        "//li[contains(@id, 'size_name')]//a",
        # Style variants
        "//li[contains(@id, 'style_name')]//a",
        # Twister (Amazon's variant selector container)
        "//div[@id='twister']//li//a[contains(@href, '/dp/')]",
        # Generic variant buttons
        "//div[contains(@class, 'a-section')]//ul[contains(@class, 'a-unordered-list')]//li//a[contains(@href, '/dp/')]"
    ]
    
    for selector in variant_selectors:
        try:
            elems = driver.find_elements(By.XPATH, selector)
            for elem in elems:
                try:
                    href = elem.get_attribute("href")
                    if href and '/dp/' in href:
                        # Only add if it's a product URL, not a review or image link
                        if '#customerReviews' not in href and '/images/' not in href:
                            variant_links.append(href)
                except:
                    continue
        except:
            continue
    
    # Remove duplicates while preserving order
    seen = set()
    unique = []
    for link in variant_links:
        # Normalize URL (remove query params for comparison)
        base_link = link.split('?')[0] if '?' in link else link
        if base_link not in seen:
            seen.add(base_link)
            unique.append(link)
    
    # Limit to 5 variants per product to save time
    result = unique[:5]
    
    if len(result) > 0:
        log(f"  → Found {len(result)} variant(s) for this product")
    else:
        log(f"  → No variants found (will use current page only)")
    
    return result

# ====================================
# BROWSER & FILE HANDLING
# ====================================

def init_driver():
    """
    Initialize Chrome WebDriver with optimal settings.
    Includes anti-detection flags to reduce Amazon captchas.
    """
    chrome_options = Options()
    if HEADLESS_MODE:
        chrome_options.add_argument("--headless")
    
    # Anti-detection options
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    # Random User Agent rotation
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]
    chrome_options.add_argument(f"user-agent={random.choice(user_agents)}")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    service = Service(CHROMEDRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.maximize_window()
    return driver

def open_amazon_homepage():
    """
    Navigate to Amazon homepage and handle initial popups.
    Useful for resetting session state or clearing frequent captchas.
    """
    try:
        driver.get("https://www.amazon.in")
        time.sleep(random.uniform(3, 5))
        try:
            # Try to close address selection or login popups
            close_btns = driver.find_elements(By.XPATH, "//button[contains(@class,'close')]")
            for btn in close_btns:
                try:
                    btn.click()
                except:
                    pass
        except:
            pass
        log("✓ Amazon homepage loaded")
    except Exception as e:
        log(f"⚠️ Homepage error: {e}")

def find_search_box(driver, wait):
    """
    Locate the search input box using multiple strategies.
    Amazon changes IDs frequently, so we need fallbacks.
    """
    selectors = [
        "//input[@id='twotabsearchtextbox']",
        "//input[@name='field-keywords']",
        "//input[contains(@placeholder,'Search Amazon')]"
    ]
    for sel in selectors:
        try:
            elem = wait.until(EC.presence_of_element_located((By.XPATH, sel)))
            if elem:
                return elem
        except:
            continue
    return None

def save_progress(out_rows, file_path, df_master):
    """
    Save scraped data to Excel file with proper formatting.
    Updates both the output sheet and the master tracking sheet.
    """
    if not out_rows:
        return
    
    df_out = pd.DataFrame(out_rows, columns=[
        "Model", "Low_Price", "High_Price", "MRP", 
        "Product_URL", "Availability", "Search_URLs"
    ])
    
    try:
        with pd.ExcelWriter(file_path, engine="openpyxl", mode="a", if_sheet_exists="overlay") as writer:
            df_out.to_excel(writer, sheet_name="Amazon", index=False, startrow=0)
            df_master.to_excel(writer, sheet_name="Master", index=False, startrow=0)
        log(f"✅ Saved: {len(out_rows)} products")
    except Exception as e:
        log(f"❌ Save error: {e}")

def manual_save_thread(out_rows_copy, file_path, df_master_copy):
    """
    Thread function for manual save. Runs save operation in background
    to avoid blocking the main scraping loop.
    """
    global _manual_save_in_progress
    try:
        log("🔵 Manual save...")
        save_progress(out_rows_copy, file_path, df_master_copy)
        log("✅ Manual save done")
    except Exception as e:
        log(f"❌ Manual save failed: {e}")
    finally:
        _manual_save_in_progress = False

def manual_save(out_rows, file_path, df_master):
    """
    Callback for manual save (triggered by Ctrl+S hotkey).
    Creates a thread to save progress without stopping scraping.
    """
    global _manual_save_in_progress
    if _manual_save_in_progress:
        return
    _manual_save_in_progress = True
    try:
        out_rows_copy = [row[:] for row in out_rows]  # Deep copy
        df_master_copy = df_master.copy(deep=True)  # Deep copy
    except:
        _manual_save_in_progress = False
        return
    t = threading.Thread(target=manual_save_thread, args=(out_rows_copy, file_path, df_master_copy), daemon=True)
    t.start()

def get_file_path():
    """
    Get Excel file path using multiple methods (command line or file dialog).
    Returns the file path or None if unsuccessful.
    """
    # Method 1: Check command line arguments
    if len(sys.argv) > 1:
        fp = sys.argv[1]
        if os.path.exists(fp) and fp.endswith(('.xlsx', '.xls')):
            log(f"📂 Using: {fp}")
            return fp
    # Method 2: Try Tkinter file dialog
    try:
        root = tk.Tk()
        root.withdraw()
        fp = filedialog.askopenfilename(title="Select Master Excel", filetypes=[("Excel", "*.xlsx *.xls")])
        root.destroy()
        if fp:
            log(f"📂 Selected: {fp}")
            return fp
    except:
        pass
    return None

# ====================================
# MAIN FUNCTION
# ====================================

def main():
    """
    Main execution function. Orchestrates the Amazon scraping process.
    
    Workflow:
    1. Select File -> 2. Choose Mode -> 3. Init Browser -> 
    4. Loop products -> 5. Search & Match -> 6. Extract Variant Prices -> 7. Save
    """
    global driver, wait
    
    log("=" * 80)
    log("AMAZON SCRAPER - FIXED VERSION")
    log("=" * 80)
    log("🔧 Fixes:")
    log("   1. Better variant extraction (min ≠ max)")
    log("   2. MRP validation (prevent unrealistic values)")
    log("   3. Only take FIRST price element (avoid duplicates)")
    log("=" * 80)
    log("")
    
    # Get file path
    file_path = get_file_path()
    if not file_path:
        log("❌ No file selected")
        return
    
    # Load master data
    try:
        df_master = pd.read_excel(file_path, sheet_name="Master", dtype={'Scrapped_Amazon': str})
    except Exception as e:
        log(f"❌ Error reading Master sheet: {e}")
        return
    
    # Validate columns
    if "Make Model" in df_master.columns:
        df_master.rename(columns={"Make Model": "Make-Model"}, inplace=True)
    elif "Make-Model" not in df_master.columns:
        log("❌ Must have 'Make Model' or 'Make-Model' column")
        return
    
    # Initialize status column
    if "Scrapped_Amazon" not in df_master.columns:
        df_master["Scrapped_Amazon"] = "No"
    else:
        df_master["Scrapped_Amazon"] = df_master["Scrapped_Amazon"].fillna("No").astype(str)
        # Clean numeric/float garbage from status column
        df_master.loc[df_master["Scrapped_Amazon"].str.contains(r'^\d+\.?\d*$', na=False, regex=True), "Scrapped_Amazon"] = "No"
    
    df_master["Make-Model-Clean"] = df_master["Make-Model"].astype(str).str.strip()
    df_master["Scrapped_Amazon"] = df_master["Scrapped_Amazon"].astype('object')
    
    # Mode selection
    log("SELECT MODE:")
    mode = input("1 - Fresh Start | 2 - Resume: ").strip()
    
    if mode == "1":
        log("✓ FRESH START")
        df_master["Scrapped_Amazon"] = "No"
    else:
        log("✓ RESUME")
    
    # Filter products to scrape
    if TEST_MODE:
        df_master_to_scrape = df_master[df_master["Scrapped_Amazon"] != "Yes"].head(TEST_N).copy()
        log(f"🧪 TEST MODE: {len(df_master_to_scrape)} products")
    else:
        df_master_to_scrape = df_master[df_master["Scrapped_Amazon"] != "Yes"].copy()
        log(f"✓ Total to scrape: {len(df_master_to_scrape)}")
    
    if len(df_master_to_scrape) == 0:
        log("✅ All models already scraped!")
        return
    
    log(f"\nREADY TO SCRAPE {len(df_master_to_scrape)} MODELS\n")
    
    # Init browser
    log("🌐 Initializing browser...")
    driver = init_driver()
    wait = WebDriverWait(driver, 15)
    open_amazon_homepage()
    
    out_rows = []
    # Register manual save hotkey
    keyboard.add_hotkey('ctrl+s', lambda: manual_save(out_rows, file_path, df_master))
    log("✅ Ctrl+S enabled\n")
    
    total = len(df_master_to_scrape)
    completed = 0
    
    try:
        # Main scraping loop
        for idx, row in df_master_to_scrape.iterrows():
            try:
                make_model = str(row["Make-Model"]).strip()
                completed += 1
                pct = (completed / total) * 100
                
                log("")
                log("="*80)
                log(f"[{completed}/{total} - {pct:.1f}%] {make_model}")
                log("="*80)
                
                # Periodic browser refresh
                if idx > 0 and idx % REFRESH_EVERY == 0:
                    log("🔄 Refreshing")
                    open_amazon_homepage()
                
                # Search execution with retries
                search_urls = []
                for attempt in range(MAX_RETRIES):
                    try:
                        search_box = find_search_box(driver, wait)
                        if not search_box:
                            if attempt < MAX_RETRIES - 1:
                                time.sleep(2)
                                continue
                            break
                        
                        search_box.clear()
                        time.sleep(0.5)
                        # Append 'mobile phone' to query to improve accuracy
                        query = f"{make_model} mobile phone"
                        search_box.send_keys(query)
                        time.sleep(1)
                        search_box.send_keys(Keys.RETURN)
                        time.sleep(random.uniform(3, 5))
                        
                        search_urls.append(driver.current_url)
                        log(f"  → Search: {driver.current_url}")
                        break
                    except Exception as e:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(2)
                
                # Extract results
                product_links = extract_product_links_minimal_filter(driver)
                
                if not product_links:
                    log(f"⚠️ No products found")
                    out_rows.append([make_model, 0, 0, 0, "URL not available", "Not found", ", ".join(search_urls)])
                    df_master.loc[row.name, "Scrapped_Amazon"] = "Yes"
                    time.sleep(2)
                    continue
                
                log(f"  → Will check first {min(len(product_links), PRODUCTS_TO_CHECK)} products")
                log("")
                
                # Price tracking
                variant_selling_prices = []
                variant_mrps = []
                variant_data = []
                products_checked = 0
                
                # Check top N products from search results
                for prod_idx, link in enumerate(product_links[:PRODUCTS_TO_CHECK], 1):
                    try:
                        product_url = link.get_attribute("href")
                        log(f"  [{prod_idx}] Visiting product...")
                        
                        if not safe_get(product_url, driver):
                            continue
                        
                        products_checked += 1
                        
                        # Product page validation
                        title = extract_clean_title_from_product_page(driver)
                        if not title:
                            log(f"  ⚠️ No title found")
                            continue
                        
                        # Category validation
                        if not is_mobile_phone_product(driver):
                            log(f"  ✗ Not a mobile phone")
                            continue
                        
                        # Keyword validation
                        title_lower = title.lower()
                        is_accessory = any(kw in title_lower for kw in EXCLUDE_KEYWORDS)
                        if is_accessory:
                            log(f"  ✗ Accessory")
                            continue
                        
                        # Title matching validation
                        is_match, reason, score = simple_match(make_model, title)
                        
                        if not is_match:
                            log(f"  ✗ No match: {reason}")
                            continue
                        
                        log(f"  ✅ MATCH: {reason}")
                        log(f"     Title: {title[:80]}...")
                        
                        # Check availability
                        availability = "Available"
                        try:
                            av_elem = driver.find_elements(By.XPATH, "//div[@id='availability']//span")
                            if av_elem:
                                availability = av_elem[0].text.strip()
                        except:
                            pass
                        
                        # FIXED: Get variant links with better detection
                        variant_links = extract_variant_links(driver)
                        if not variant_links:
                            variant_links = [product_url]
                        
                        log(f"  → Checking {len(variant_links)} variant(s)...")
                        
                        # Check each variant for prices
                        for v_idx, v_url in enumerate(variant_links, 1):
                            if not safe_get(v_url, driver):
                                continue
                            
                            log(f"     Variant {v_idx}/{len(variant_links)}:")
                            
                            # FIXED: Extract prices with validation
                            selling_price, mrp = extract_prices_from_product_page(driver)
                            
                            if not selling_price:
                                log(f"     ⚠️ No price found")
                                save_screenshot(driver, name_prefix=f"no_price_{completed}_{prod_idx}_{v_idx}")
                            
                            if selling_price > 0:
                                variant_selling_prices.append(selling_price)
                            if mrp > 0:
                                variant_mrps.append(mrp)
                            
                            variant_data.append({
                                "title": title,
                                "selling_price": selling_price,
                                "mrp": mrp,
                                "url": v_url,
                                "availability": availability
                            })
                        
                        log("")
                    
                    except Exception as e:
                        log(f"  ⚠️ Error: {e}")
                        continue
                
                # Aggregate results (Find min/max prices across all variants)
                log(f"  → Summary: Checked {products_checked} products, found {len(variant_data)} variants")
                
                if variant_selling_prices:
                    lowest_price = min(variant_selling_prices)
                    highest_price = max(variant_selling_prices)
                    log(f"  → Price range: ₹{lowest_price} - ₹{highest_price}")
                else:
                    lowest_price = 0
                    highest_price = 0
                
                if variant_mrps:
                    mrp_final = max(variant_mrps)
                    log(f"  → MRP: ₹{mrp_final}")
                else:
                    mrp_final = 0
                    log(f"  → MRP: Not found")
                
                if variant_data:
                    url_final = variant_data[0]["url"]
                    availability_final = variant_data[0]["availability"]
                else:
                    url_final = "URL not available"
                    availability_final = "Not found"
                
                # Store data
                out_rows.append([make_model, lowest_price, highest_price, mrp_final, url_final, availability_final, ", ".join(search_urls)])
                df_master.loc[row.name, "Scrapped_Amazon"] = "Yes"
                
                log(f"✓ FINAL: Low=₹{lowest_price}, High=₹{highest_price}, MRP=₹{mrp_final}")
                
                # Periodic save and browser restart
                if completed > 0 and completed % SAVE_EVERY == 0:
                    log("\n💾 Periodic save")
                    save_progress(out_rows, file_path, df_master)
                    try:
                        driver.quit()
                    except:
                        pass
                    time.sleep(3)
                    driver = init_driver()
                    wait = WebDriverWait(driver, 15)
                    open_amazon_homepage()
            
            except Exception as e:
                log(f"❌ Exception: {e}")
                traceback.print_exc()
                try:
                    save_screenshot(driver, name_prefix=f"exception_{completed}")
                except:
                    pass
                out_rows.append([str(row["Make-Model"]), 0, 0, 0, "URL not available", "Error", str(e)])
                df_master.loc[row.name, "Scrapped_Amazon"] = "Yes"
                time.sleep(1)
                continue
    
    finally:
        # Cleanup
        try:
            keyboard.unhook_all_hotkeys()
        except:
            pass
        try:
            driver.quit()
        except:
            pass
        save_progress(out_rows, file_path, df_master)
        
        # Completion summary
        log("")
        log("=" * 80)
        log("✅ SCRAPING COMPLETE!")
        log("=" * 80)
        log(f"   Processed: {len(out_rows)}")
        log(f"   Successful: {sum(1 for r in out_rows if r[1] > 0)}")
        log(f"   Not found: {sum(1 for r in out_rows if r[1] == 0)}")
        log(f"   Saved to: {file_path}")
        log("=" * 80)

if __name__ == "__main__":
    main()
