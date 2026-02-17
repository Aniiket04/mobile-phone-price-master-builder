# Flipkart Product Price Scraper
# A personal project for automated price monitoring on Flipkart
# Author: Personal Portfolio Project
# Description: Scrapes product prices from Flipkart search results with dynamic class detection
#
# FEATURES:
# - Multiple scraping modes: Fresh Start, Resume, Error List
# - Separate extraction of selling prices and MRPs
# - Variant-aware price aggregation (lowest/highest selling price, max MRP)
# - Non-blocking manual save with Ctrl+S
# - Dynamic selector detection to handle Flipkart's changing class names
# - Outlier filtering to remove spurious prices
# - Periodic browser refresh to avoid detection
# - Comprehensive error handling and debug screenshots

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
HEADLESS_MODE = False  # Set to False to SEE the browser in action (recommended for first run)
                       # Set to True to run invisibly in background (faster, but can't see what's happening)

# Test mode settings - useful for debugging
TEST_MODE = False  # Set to True to scrape only a small subset of products
TEST_N = 5  # Number of products to scrape in test mode

# Scraping limits and intervals
MAX_PRODUCTS_PER_MODEL = 5  # Maximum number of product variants to check per search
REFRESH_EVERY = 80  # Refresh browser after this many searches to avoid detection
SAVE_EVERY = 100  # Save progress after processing this many products
MAX_RETRIES = 3  # Maximum number of retry attempts for failed operations

# Keyword filtering - exclude accessories and non-relevant items
EXCLUDE_KEYWORDS = [
    "cover", "case", "charger", "screen protector", "cable", "earphone",
    "headphone", "tempered glass", "skin", "stand", "bag"
]

# Chrome WebDriver configuration
# Update this path to point to your ChromeDriver executable location
CHROMEDRIVER_PATH = r"C:\Users\anike\OneDrive\Project\chromedriver-win64\chromedriver.exe"

# Debug screenshot settings - helpful for troubleshooting selector issues
DEBUG_SAVE_SCREENSHOT = True
SCREENSHOT_DIR = os.path.join(os.getcwd(), "debug_screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

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

def is_mobile_phone_product(driver):
    """
    Verify if the current product page is actually a mobile phone.
    
    This prevents scraping wrong products like laptops when searching for phones.
    Checks breadcrumbs, category, and page elements for mobile/phone keywords.
    
    Returns:
        True if product appears to be a mobile phone, False otherwise
    """
    try:
        # Check breadcrumbs for "Mobiles" category
        breadcrumbs = driver.find_elements(By.XPATH, "//a[contains(@href,'mobile') or contains(@href,'phone')]")
        if breadcrumbs:
            return True
        
        # Check if page contains mobile/phone keywords in prominent places
        mobile_keywords = ['mobile', 'phone', 'smartphone', 'mobiles & accessories']
        
        # Check category links
        try:
            category_links = driver.find_elements(By.XPATH, "//a[contains(@class,'_1BJVlg') or contains(@class,'_2whKao')]")
            for link in category_links[:5]:  # Check first few links
                text = link.text.lower()
                if any(kw in text for kw in mobile_keywords):
                    return True
        except:
            pass
        
        # Check page text for mobile indicators
        try:
            page_text = driver.find_element(By.TAG_NAME, "body").text.lower()
            # Check if "laptop" or "computer" appears prominently (indicates wrong category)
            if 'laptop' in page_text[:500] or 'notebook' in page_text[:500] or 'gaming' in page_text[:200]:
                # But make sure it's not about mobile gaming
                if 'mobile' not in page_text[:300] and 'phone' not in page_text[:300]:
                    log("  ⚠️ Product appears to be a laptop/computer, not a mobile phone")
                    return False
        except:
            pass
        
        # If we can't determine category, assume it's OK (default to allowing)
        return True
        
    except Exception as e:
        # If check fails, assume it's OK (avoid false negatives)
        return True

def normalize_text_spaces(s: str):
    """Normalize text by lowercasing and removing extra whitespace"""
    return re.sub(r"\s+", " ", (s or "").lower().replace("-", " ")).strip()

def model_matches_title(make_model: str, title: str) -> bool:
    """
    Check if product title matches the search query using STRICT prefix-based matching.
    
    CRITICAL: This prevents mixing different model variants!
    - "iPhone 13" will match "Apple iPhone 13 128GB" ✓
    - "iPhone 13" will match "iPhone 13 128GB" ✓
    - "iPhone 13" will NOT match "iPhone 13 Pro" ✗
    - "iPhone 13" will NOT match "iPhone 13 Mini" ✗
    
    Args:
        make_model: The search query (e.g., "Samsung Galaxy S21")
        title: The product title from the search results
    
    Returns:
        True if the title matches exactly (not a different variant), False otherwise
    """
    mm = normalize_text_spaces(make_model)
    tt = normalize_text_spaces(title)

    if not mm or not tt:
        return False

    model_tokens = mm.split()
    title_tokens = tt.split()

    n = len(model_tokens)
    if n == 0:
        return False

    # Find where the model starts in the title
    # This handles cases like "Apple iPhone 13" when searching for "iPhone 13"
    match_position = -1
    for i in range(len(title_tokens) - n + 1):
        if title_tokens[i:i+n] == model_tokens:
            match_position = i
            break
    
    if match_position == -1:
        return False
    
    # Check tokens after the matched model
    tokens_after_match = title_tokens[match_position + n:]
    
    if len(tokens_after_match) == 0:
        # Exact match (e.g., "iPhone 13" matches "iPhone 13")
        return True
    
    next_token = tokens_after_match[0]
    
    # Allow "5g" immediately after model (e.g., "iPhone 13 5G")
    if next_token == "5g":
        # If there's ANOTHER token after "5g", check it's not a variant
        if len(tokens_after_match) > 1:
            token_after_5g = tokens_after_match[1]
            # Variant keywords that indicate a DIFFERENT model
            variant_keywords = [
                'pro', 'max', 'mini', 'plus', 'ultra', 'lite', 'fe', 
                'edge', 'note', 'fold', 'flip', 'prime', 'air', 'se',
                'neo', 'master', 'edition', 'turbo', 'racing', 'gt',
                'carbon', 'explorer', 'speed', 'youth', 'classic'
            ]
            if token_after_5g in variant_keywords:
                return False
        return True
    
    # Variant keywords that indicate a DIFFERENT model (not just storage/color)
    variant_keywords = [
        'pro', 'max', 'mini', 'plus', 'ultra', 'lite', 'fe', 
        'edge', 'note', 'fold', 'flip', 'prime', 'air', 'se',
        'neo', 'master', 'edition', 'turbo', 'racing', 'gt',
        'carbon', 'explorer', 'speed', 'youth', 'classic'
    ]
    
    # If the next token is a variant keyword, this is a DIFFERENT model
    if next_token in variant_keywords:
        return False
    
    # Allow storage sizes, colors, RAM, and other specs
    # Numbers and units (128, 256, gb, tb, ram, etc.) are OK
    # Colors (black, white, blue, etc.) are OK
    # Parentheses, dashes, etc. are OK
    
    return True

def save_screenshot(driver, name_prefix="error"):
    """
    Save a screenshot for debugging purposes.
    Useful when selectors fail or prices aren't found.
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

# ====================================
# DYNAMIC SELECTOR DETECTION
# ====================================
# Flipkart frequently changes CSS class names to prevent scraping.
# These functions dynamically detect current class names using pattern matching.

def collect_candidate_classes(driver, sample_html_size=20000):
    """
    Extract CSS class names from the page source.
    We analyze the HTML to find current class naming patterns.
    
    Args:
        driver: Selenium WebDriver instance
        sample_html_size: Limit HTML parsing to first N characters for performance
    
    Returns:
        Set of candidate class name tokens found in the page
    """
    try:
        html = driver.page_source[:sample_html_size]
    except:
        html = ""
    
    # Find all class="..." occurrences using regex
    candidates = set(re.findall(r'class="([^"]+)"', html))
    class_tokens = set()
    
    # Split multi-class attributes and filter by reasonable length
    for c in candidates:
        for tok in c.split():
            if 2 <= len(tok) <= 30:
                class_tokens.add(tok)
    
    return class_tokens

def find_classes_by_pattern(class_tokens):
    """
    Identify likely CSS classes for products, titles, and prices using heuristic patterns.
    
    Flipkart uses obfuscated class names like 'CGtC98', 'KzDlHZ', etc.
    These patterns are based on common naming conventions observed over time.
    
    Args:
        class_tokens: Set of class names found on the page
    
    Returns:
        Dictionary with lists of candidate classes for each element type
    """
    product_link_candidates = []
    title_candidates = []
    price_candidates = []
    
    # Regex patterns for different element types
    # These patterns match commonly observed Flipkart class naming conventions
    product_patterns = [r'CGtC98', r'VJA3rP', r'_1fQZEK', r'link', r'hover', r'product', r'rlVTrN']
    title_patterns = [r'KzDlHZ', r'VU-ZEz', r'wjcQME', r'prd', r'title', r'row', r'IN0V7']
    price_patterns = [r'Nx9bqj', r'UOCQyV', r'_30jeq3', r'price', r'amount', r'_3I9_wc', r'yRaY8j']
    
    def match_any(tok, patterns):
        """Check if token matches any pattern in the list"""
        for p in patterns:
            if re.search(p, tok):
                return True
        return False

    # Categorize class tokens based on pattern matching
    for tok in class_tokens:
        if match_any(tok, product_patterns):
            product_link_candidates.append(tok)
        if match_any(tok, title_patterns):
            title_candidates.append(tok)
        if match_any(tok, price_patterns):
            price_candidates.append(tok)

    # Additional heuristics for edge cases
    for tok in class_tokens:
        if re.search(r'[A-Za-z0-9]{2,}QZEK', tok) and tok not in product_link_candidates:
            product_link_candidates.append(tok)
        if tok.endswith('HZ') and tok not in title_candidates:
            title_candidates.append(tok)
        if tok.endswith('j') and len(tok) <= 10 and tok not in price_candidates:
            price_candidates.append(tok)

    return {
        "product": product_link_candidates,
        "title": title_candidates,
        "price": price_candidates
    }

def build_xpath_from_classes(kind, class_list):
    """
    Build an XPath expression that checks multiple CSS classes.
    Creates an OR condition to find elements with any of the candidate classes.
    """
    if not class_list:
        return None
    parts = [f"contains(@class,'{c}')" for c in class_list]
    return f".//*[{ ' or '.join(parts) }]"

# ====================================
# WEB SCRAPING HELPER FUNCTIONS
# ====================================

def safe_get(url, driver, retries=MAX_RETRIES, backoff=1.5):
    """
    Load a URL with retry logic and exponential backoff.
    Handles network issues and temporary site unavailability.
    """
    for attempt in range(1, retries+1):
        try:
            driver.get(url)
            time.sleep(random.uniform(2, 4))
            return True
        except Exception as e:
            log(f"⚠️ Failed to load {url}, retrying ({attempt}/{retries})... {e}")
            time.sleep(random.uniform(1, backoff*attempt))
    log(f"❌ Could not load {url} after {retries} attempts.")
    return False

def extract_product_cards_from_search(driver, make_model):
    """
    Extract product links from Flipkart search results page WITH TITLE FILTERING.
    
    CRITICAL CHANGE: Now filters products at the search page level by checking
    if their titles match the search query BEFORE visiting them.
    
    This prevents issues like:
    - Searching for "Acer Super ZX" (mobile) but getting "Acer Predator" (laptop)
    - Searching for "iPhone 13" but getting "iPhone 13 Pro" first
    
    Strategy:
    1. Try known product card class patterns
    2. Fall back to generic container + anchor heuristics
    3. Filter by checking visible title text matches search query
    4. Only return matching product links
    
    Args:
        driver: Selenium WebDriver instance
        make_model: Search query used (for relevance filtering)
    
    Returns:
        List of anchor (link) WebElements for MATCHING product cards only
    """
    log(f"  → Filtering search results for: '{make_model}'")
    
    # Layer 1: Try multiple known product card selector patterns
    known_selectors = [
        "._1fQZEK",
        ".CGtC98",
        ".VJA3rP",
        "._2kHMtA",
        "._1AtVbE",
        "._13oc-S",
        ".s1Q9rs",
        ".cPHDOP",
        ".tUxRFH"
    ]
    
    all_anchors = []
    
    for sel in known_selectors:
        try:
            anchors = driver.find_elements(By.CSS_SELECTOR, f"a{sel}")
            if anchors:
                log(f"  ✓ Found {len(anchors)} products using selector: {sel}")
                all_anchors = anchors
                break
        except:
            pass
    
    # Layer 2: Generic fallback - find all links in product containers
    if not all_anchors:
        try:
            containers = driver.find_elements(By.XPATH, "//*[contains(@class,'col') or contains(@class,'product') or contains(@class,'item')]//a[@href]")
            if containers:
                # Filter links that look like product pages
                product_links = [a for a in containers if '/p/' in (a.get_attribute("href") or "")]
                if product_links:
                    log(f"  ✓ Found {len(product_links)} products using generic container fallback")
                    all_anchors = product_links
        except:
            pass
    
    # Layer 3: Last resort - find any anchor with price symbol or model text
    if not all_anchors:
        try:
            all_page_anchors = driver.find_elements(By.TAG_NAME, "a")
            potential_products = []
            for a in all_page_anchors:
                href = a.get_attribute("href") or ""
                text = a.text.lower()
                # Check if link contains price symbol or matches our search model
                if '/p/' in href and ('₹' in text or any(word in text for word in make_model.lower().split())):
                    potential_products.append(a)
            
            if potential_products:
                log(f"  ✓ Found {len(potential_products)} products using text/price fallback")
                all_anchors = potential_products
        except:
            pass
    
    if not all_anchors:
        log("  ❌ Could not find any product cards on search page")
        return []
    
    # CRITICAL: Filter anchors by checking if their visible text matches the search query
    log(f"  → Checking {len(all_anchors)} products for title match...")
    matching_anchors = []
    
    for anchor in all_anchors:
        try:
            # Get the visible text of the product card
            card_text = anchor.text.strip()
            
            # Skip if no text found
            if not card_text:
                continue
            
            # Check if this product title matches our search query
            if model_matches_title(make_model, card_text):
                matching_anchors.append(anchor)
                log(f"  ✓ MATCH on search page: {card_text[:60]}...")
            else:
                log(f"  ✗ SKIP on search page: {card_text[:60]}...")
        except Exception as e:
            # If we can't get text, skip this product
            continue
    
    if not matching_anchors:
        log(f"  ⚠️ No products matched '{make_model}' on search results page")
        log(f"     This usually means Flipkart returned wrong category results")
        log(f"     Tip: Try a more specific search term or check if product exists")
    else:
        log(f"  ✓ Found {len(matching_anchors)} matching products after filtering")
    
    return matching_anchors

def find_search_box(driver, wait):
    """
    Locate the search input box using multiple selector strategies.
    Flipkart's search box selectors also change periodically.
    """
    selectors = [
        "//input[@placeholder='Search for Products, Brands and More']",
        "//input[@name='q']",
        "//input[@type='text' and contains(@class,'_3704LK')]",
        "//input[@type='text' and contains(@class,'Pke_EE')]",
        "//input[contains(@placeholder,'Search')]"
    ]
    
    for sel in selectors:
        try:
            elem = wait.until(EC.presence_of_element_located((By.XPATH, sel)))
            if elem:
                return elem
        except:
            continue
    return None

def extract_variant_links(driver):
    """
    Extract links to product variants (different colors, storage sizes, etc.).
    Some products have multiple configuration options we want to check.
    """
    variant_links = []
    
    # Strategy 1: Look for variant buttons/links in common locations
    try:
        variant_elems = driver.find_elements(By.XPATH, 
            "//a[contains(@class,'_1fGeJ5') or contains(@href,'/p/') and ancestor::*[contains(@class,'col')]]")
        for elem in variant_elems:
            href = elem.get_attribute("href")
            if href and '/p/' in href:
                variant_links.append(href)
    except:
        pass
    
    # Strategy 2: Look for storage/color option links
    try:
        storage_links = driver.find_elements(By.XPATH, 
            "//li[contains(@class,'col')]//a[@href and contains(text(),'GB')]")
        for elem in storage_links:
            href = elem.get_attribute("href")
            if href:
                variant_links.append(href)
    except:
        pass
    
    # Remove duplicates while preserving order
    seen = set()
    unique_variants = []
    for link in variant_links:
        if link not in seen:
            seen.add(link)
            unique_variants.append(link)
    
    return unique_variants

def extract_title_from_product_page(driver, heuristic_classes):
    """
    Extract product title from product detail page.
    Tries dynamic class detection first, then falls back to common patterns.
    """
    # Try heuristic classes first (dynamically detected)
    if heuristic_classes:
        xpath = build_xpath_from_classes("title", heuristic_classes)
        if xpath:
            try:
                elems = driver.find_elements(By.XPATH, xpath)
                for e in elems:
                    txt = e.text.strip()
                    if len(txt) > 5:  # Reasonable title length
                        return txt
            except:
                pass
    
    # Fallback: Known title selectors
    title_selectors = [
        ".B_NuCI",
        "span.VU-ZEz",
        "span._35KyD6",
        "h1.yhB1nd",
        "//h1[contains(@class,'')]//span",
        "//span[contains(@class,'VU-ZEz')]"
    ]
    
    for sel in title_selectors:
        try:
            if sel.startswith("//"):
                elems = driver.find_elements(By.XPATH, sel)
            else:
                elems = driver.find_elements(By.CSS_SELECTOR, sel)
            
            if elems:
                txt = elems[0].text.strip()
                if txt:
                    return txt
        except:
            continue
    
    return ""

def is_valid_price_text(text: str) -> bool:
    """
    Validate if text looks like a proper price element.
    Helps avoid extracting numbers from random UI elements.
    
    Args:
        text: Text to validate
    
    Returns:
        True if text appears to be a valid price element, False otherwise
    """
    if not text or len(text) > 100:  # Price text shouldn't be too long
        return False
    
    # MUST start with rupee symbol (avoids "Save ₹11,699" type text)
    if not text.strip().startswith('₹'):
        return False
    
    # Extract just the numeric part
    nums = re.sub(r'[^\d]', '', text)
    if not nums or len(nums) < 4:  # Valid phone prices have at least 4 digits
        return False
    
    # Check for promotional keywords in the price text itself
    text_lower = text.lower()
    # If the price element contains these words, it's probably not a real price
    bad_keywords = ['save', 'off', 'discount', 'cashback', 'bank', 'emi', 'extra']
    if any(kw in text_lower for kw in bad_keywords):
        return False
    
    return True

def is_promotional_or_emi_text(text: str) -> bool:
    """
    Detect if price text contains promotional/EMI keywords that inflate apparent prices.
    These should be filtered out to avoid incorrectly high MRP values.
    
    Args:
        text: Price text to check
    
    Returns:
        True if text contains promotional keywords, False otherwise
    """
    text_lower = text.lower()
    promotional_keywords = [
        'emi', 'month', 'installment', 'pay', 'no cost', 
        'offer', 'discount', 'cashback', 'bank', 'card',
        'exchange', 'bonus', 'save', 'extra', 'free', 'off'
    ]
    return any(keyword in text_lower for keyword in promotional_keywords)

def is_valid_phone_price(price: int) -> bool:
    """
    Validate if a price is within reasonable range for mobile phones.
    Helps filter out random numbers that aren't actual prices.
    
    Args:
        price: Price value to validate
    
    Returns:
        True if price is in valid range, False otherwise
    """
    # Mobile phones typically cost between ₹3,000 and ₹2,00,000
    return 3000 <= price <= 200000

def extract_price_and_mrp_from_product_page(driver, heuristic_classes):
    """
    Extract selling price and MRP (striked price) separately from product page.
    
    CRITICAL LOGIC - Modified for correct price aggregation:
    - Selling price: Current discounted price (what customer pays)
    - MRP: Original striked/crossed price (maximum retail price)
    - These are extracted separately and NEVER mixed
    - Uses TARGETED selectors to avoid random numbers
    
    Returns:
        tuple: (selling_price, mrp) both as integers
    """
    selling_price = 0
    mrp_value = 0
    
    # Lists to store prices separately
    selling_prices = []
    mrp_prices = []
    
    # STRATEGY 1: Extract MRP (strikethrough prices) - MOST RELIABLE
    try:
        # Target strikethrough elements very specifically
        strikethrough_selectors = [
            "//div[contains(@class,'_3I9_wc') and contains(text(),'₹')]",  # Known Flipkart MRP class
            "//div[contains(@class,'_30jeq3') and contains(@class,'_16Jk6d')]",  # Another MRP pattern
            "//div[contains(@style,'text-decoration') and contains(@style,'line-through') and contains(text(),'₹')]",
            "//span[contains(@style,'text-decoration') and contains(@style,'line-through') and contains(text(),'₹')]"
        ]
        
        for sel in strikethrough_selectors:
            try:
                elems = driver.find_elements(By.XPATH, sel)
                for elem in elems:
                    text = elem.text.strip()
                    # Only exact price format: starts with ₹ followed by numbers
                    if text.startswith('₹') and is_valid_price_text(text):
                        price_val = extract_price(text)
                        if is_valid_phone_price(price_val):
                            mrp_prices.append(price_val)
                            log(f"  → MRP found: ₹{price_val} from strikethrough element")
            except:
                continue
    except:
        pass
    
    # STRATEGY 2: Extract MAIN selling price - Target the PRIMARY price display
    try:
        # These are the MAIN price containers on Flipkart product pages
        # We specifically look for the largest, most prominent price
        main_price_selectors = [
            "//div[contains(@class,'_30jeq3') and contains(@class,'_1_WHN1')]",  # Main price container
            "//div[contains(@class,'_30jeq3')]/div[contains(@class,'_16Jk6d')]",  # Price value inside container
            "//div[@class='_30jeq3 _16Jk6d']",  # Exact class match
            "//div[contains(@class,'_30jeq3')]//div[not(contains(@style,'line-through'))]",
            "//span[contains(@class,'_30jeq3') and not(contains(@class,'_16Jk6d'))]"
        ]
        
        for sel in main_price_selectors:
            try:
                elems = driver.find_elements(By.XPATH, sel)
                for elem in elems:
                    text = elem.text.strip()
                    # Verify it's a main price element
                    if not text.startswith('₹'):
                        continue
                    if not is_valid_price_text(text):
                        continue
                    
                    # Check it's NOT a strikethrough
                    style = elem.get_attribute('style') or ''
                    class_attr = elem.get_attribute('class') or ''
                    if 'line-through' in style or '_3I9_wc' in class_attr:
                        continue
                    
                    # Check parent isn't strikethrough either
                    try:
                        parent = elem.find_element(By.XPATH, '..')
                        parent_style = parent.get_attribute('style') or ''
                        if 'line-through' in parent_style:
                            continue
                    except:
                        pass
                    
                    price_val = extract_price(text)
                    if is_valid_phone_price(price_val):
                        selling_prices.append(price_val)
                        log(f"  → Selling price found: ₹{price_val} from main price element")
            except:
                continue
    except:
        pass
    
    # STRATEGY 3: If we still haven't found selling price, try heuristic classes
    if not selling_prices and heuristic_classes:
        xpath = build_xpath_from_classes("price", heuristic_classes)
        if xpath:
            try:
                elems = driver.find_elements(By.XPATH, xpath)
                # Get the LARGEST price as selling price (main price is usually biggest)
                temp_prices = []
                for elem in elems:
                    text = elem.text.strip()
                    if not text.startswith('₹'):
                        continue
                    if not is_valid_price_text(text):
                        continue
                    
                    style = elem.get_attribute('style') or ''
                    class_attr = elem.get_attribute('class') or ''
                    
                    # Skip strikethrough
                    if 'line-through' in style or '_3I9_wc' in class_attr:
                        if not mrp_prices:  # Only add to MRP if we haven't found any yet
                            price_val = extract_price(text)
                            if is_valid_phone_price(price_val):
                                mrp_prices.append(price_val)
                        continue
                    
                    # This is likely a selling price
                    price_val = extract_price(text)
                    if is_valid_phone_price(price_val):
                        temp_prices.append(price_val)
                
                # Take the largest price as the main selling price (if multiple found)
                if temp_prices:
                    selling_prices.extend(temp_prices)
                    log(f"  → Selling prices from heuristics: {temp_prices}")
            except:
                pass
    
    # STRATEGY 4: Last resort - but only for LARGE font sizes (main price is usually large)
    if not selling_prices:
        try:
            # Find divs with rupee that are likely to be main price (large text)
            all_price_divs = driver.find_elements(By.XPATH, "//div[starts-with(text(),'₹')]")
            
            for elem in all_price_divs:
                text = elem.text.strip()
                if not is_valid_price_text(text):
                    continue
                
                # Check font size - main price is usually larger
                try:
                    font_size = elem.value_of_css_property('font-size')
                    # Convert font size to number (e.g., "28px" -> 28)
                    font_num = int(re.sub(r'[^\d]', '', font_size)) if font_size else 0
                    
                    # Main selling price usually has font size >= 24px
                    if font_num < 24:
                        continue
                except:
                    pass  # If can't get font size, skip this check
                
                # Check it's not strikethrough
                style = elem.get_attribute('style') or ''
                if 'line-through' in style:
                    continue
                
                price_val = extract_price(text)
                if is_valid_phone_price(price_val):
                    selling_prices.append(price_val)
                    log(f"  → Selling price found (large text): ₹{price_val}")
        except:
            pass
    
    # Remove duplicates and sort
    selling_prices = sorted(list(set([p for p in selling_prices if is_valid_phone_price(p)])))
    mrp_prices = sorted(list(set([p for p in mrp_prices if is_valid_phone_price(p)])))
    
    # Filter outliers in selling prices - if we have prices that vary wildly, 
    # keep only the higher cluster (lower values likely from "Save ₹X" text)
    if len(selling_prices) > 1:
        # If max is more than 3x the min, filter out the low outliers
        if max(selling_prices) > 3 * min(selling_prices):
            # Keep prices that are at least 50% of the maximum
            threshold = max(selling_prices) * 0.5
            filtered_prices = [p for p in selling_prices if p >= threshold]
            if filtered_prices:
                log(f"  ⚠️ Filtered outliers from {selling_prices} → {filtered_prices}")
                selling_prices = filtered_prices
    
    # Debug logging
    log(f"  → Final extracted - Selling: {selling_prices}, MRP: {mrp_prices}")
    
    # Determine final selling price
    if selling_prices:
        selling_price = min(selling_prices)
    
    # Determine final MRP
    if mrp_prices:
        mrp_value = max(mrp_prices)
    
    # Validation: MRP should be >= selling price
    if selling_price and mrp_value and mrp_value < selling_price:
        # If MRP < selling price, swap them
        log(f"  ⚠️ MRP ({mrp_value}) < Selling ({selling_price}), swapping...")
        selling_price, mrp_value = mrp_value, selling_price
    
    # If no separate MRP found, use selling price
    if selling_price and not mrp_value:
        mrp_value = selling_price
    
    # If no selling price but have MRPs, use minimum MRP as selling price
    if not selling_price and mrp_prices:
        selling_price = min(mrp_prices)
        mrp_value = max(mrp_prices)
    
    return selling_price, mrp_value

# ====================================
# BROWSER INITIALIZATION
# ====================================

def init_driver():
    """
    Initialize Chrome WebDriver with optimal settings for web scraping.
    Uses headless mode (invisible) or visible mode based on HEADLESS_MODE setting.
    """
    chrome_options = Options()
    
    # Headless mode - controlled by configuration
    if HEADLESS_MODE:
        chrome_options.add_argument("--headless")
        log("🔧 Running in HEADLESS mode (browser hidden)")
    else:
        log("🔧 Running in VISIBLE mode (you can see the browser)")
    
    # Essential options for scraping
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")  # Hide automation flags
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # User agent to appear as regular browser
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Additional preferences
    prefs = {}
    if HEADLESS_MODE:
        # Only disable images in headless mode for speed
        prefs["profile.managed_default_content_settings.images"] = 2
    chrome_options.add_experimental_option("prefs", prefs)
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    try:
        service = Service(CHROMEDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=chrome_options)
        log("✅ Chrome WebDriver initialized successfully")
    except Exception as e:
        log(f"❌ Failed to initialize ChromeDriver: {e}")
        log("Make sure ChromeDriver is installed and the path is correct in CHROMEDRIVER_PATH")
        sys.exit(1)
    
    return driver

# ====================================
# FILE I/O AND PROGRESS MANAGEMENT
# ====================================

# Global flag to prevent concurrent manual saves
_manual_save_in_progress = False

def save_progress(out_rows, file_path, df_master):
    """
    Save scraped data to Excel file with proper formatting.
    Updates both the output sheet and the master tracking sheet.
    """
    if not out_rows:
        log("No data to save yet.")
        return
    
    df_out = pd.DataFrame(out_rows, columns=[
        "Model", "Low_Price", "High_Price", "MRP", 
        "Product_URL", "Availability", "Search_URLs"
    ])
    
    try:
        with pd.ExcelWriter(file_path, engine="openpyxl", mode="a", if_sheet_exists="overlay") as writer:
            df_out.to_excel(writer, sheet_name="Flipkart", index=False, startrow=0)
            df_master.to_excel(writer, sheet_name="Master", index=False, startrow=0)
        log(f"✅ Progress saved: {len(out_rows)} products scraped")
    except Exception as e:
        log(f"❌ Error saving file: {e}")

def manual_save_thread(out_rows_copy, file_path, df_master_copy):
    """
    Thread function for manual save. Runs save operation in background.
    """
    global _manual_save_in_progress
    
    try:
        log("🔵 Manual save triggered (Ctrl+S) - saving in background...")
        save_progress(out_rows_copy, file_path, df_master_copy)
        log("✅ Manual save completed! Script will continue scraping.")
    except Exception as e:
        log(f"❌ Manual save failed: {e}")
        traceback.print_exc()
    finally:
        _manual_save_in_progress = False

def manual_save(out_rows, file_path, df_master):
    """
    Callback for manual save (triggered by Ctrl+S hotkey).
    Allows user to save progress at any time during scraping.
    Uses threading to avoid blocking the main scraping process.
    """
    global _manual_save_in_progress
    
    # Prevent concurrent saves
    if _manual_save_in_progress:
        log("⚠️ Manual save already in progress, please wait...")
        return
    
    _manual_save_in_progress = True
    
    # Create copies of data for thread safety
    try:
        out_rows_copy = [row[:] for row in out_rows]  # Deep copy of list of lists
        df_master_copy = df_master.copy(deep=True)  # Deep copy of DataFrame
    except Exception as e:
        log(f"❌ Failed to copy data for save: {e}")
        _manual_save_in_progress = False
        return
    
    # Run save in a separate thread so it doesn't block scraping
    save_thread = threading.Thread(
        target=manual_save_thread,
        args=(out_rows_copy, file_path, df_master_copy),
        daemon=True
    )
    save_thread.start()

# ====================================
# MAIN SCRAPING FUNCTION
# ====================================

def get_file_path():
    """
    Get Excel file path using multiple methods (command line or file dialog).
    Returns the file path or None if unsuccessful.
    """
    # Method 1: Check command line arguments
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        if os.path.exists(file_path) and file_path.endswith(('.xlsx', '.xls')):
            log(f"📂 Using file from command line: {file_path}")
            return file_path
        else:
            log(f"⚠️ Invalid file path provided: {file_path}")
    
    # Method 2: Try Tkinter file dialog (if available)
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
    
    # Method 3: Manual input
    log("\n" + "="*60)
    log("Please provide the Excel file path:")
    log("(Drag and drop the file here, or paste the full path)")
    log("="*60)
    
    try:
        file_path = input("Enter file path: ").strip().strip('"').strip("'")
        if os.path.exists(file_path) and file_path.endswith(('.xlsx', '.xls')):
            log(f"📂 Using file: {file_path}")
            return file_path
        else:
            log(f"❌ File not found or invalid format: {file_path}")
            return None
    except KeyboardInterrupt:
        log("\n❌ Operation cancelled by user.")
        return None

def main():
    """
    Main execution function. Orchestrates the entire scraping process.
    
    SCRAPING MODES:
    1. Fresh Start  - Resets all scraping status and starts from scratch
    2. Resume       - Continues from where you left off (default)
    3. Error List   - Scrapes only specific models from an error file
    
    Usage:
        python flipkart_price_scraper.py                    # Opens file dialog or prompts for input
        python flipkart_price_scraper.py "path/to/file.xlsx"  # Uses specified file
    
    Workflow:
    1. Select Excel file
    2. Choose scraping mode (Fresh/Resume)
    3. Optionally select error list
    4. For each product:
        - Search on Flipkart
        - Extract product links
        - Visit each product page
        - Extract prices and details
        - Handle variants
    5. Save results periodically
    6. Clean up and exit
    """
    log("=" * 60)
    log("FLIPKART PRICE SCRAPER - STARTING")
    log("=" * 60)
    
    # Get file path using available method
    file_path = get_file_path()
    
    if not file_path:
        log("❌ No file selected. Exiting.")
        log("\nTip: You can run the script with a file path:")
        log('   python flipkart_price_scraper.py "C:\\path\\to\\your\\file.xlsx"')
        return
    
    # Load master data
    try:
        df_master = pd.read_excel(file_path, sheet_name="Master", dtype={'Scrapped_Flipkart': str})
    except Exception as e:
        log(f"❌ Error reading Master sheet: {e}")
        return
    
    # Handle column name variations
    if "Make Model" in df_master.columns:
        df_master.rename(columns={"Make Model": "Make-Model"}, inplace=True)
    elif "Make-Model" not in df_master.columns:
        log("❌ Master sheet must contain column 'Make Model' or 'Make-Model'")
        return
    
    # Initialize scraping status column if not exists
    if "Scrapped_Flipkart" not in df_master.columns:
        df_master["Scrapped_Flipkart"] = "No"
    else:
        # Ensure the column is string type and fill NaN with "No"
        df_master["Scrapped_Flipkart"] = df_master["Scrapped_Flipkart"].fillna("No").astype(str)
        # Replace any float values (like 0.0) with "No"
        df_master.loc[df_master["Scrapped_Flipkart"].str.contains(r'^\d+\.?\d*$', na=False, regex=True), "Scrapped_Flipkart"] = "No"
    
    # Clean product names
    df_master["Make-Model-Clean"] = df_master["Make-Model"].astype(str).str.strip()
    
    # Ensure Scrapped_Flipkart column is object/string type (critical for avoiding dtype errors)
    df_master["Scrapped_Flipkart"] = df_master["Scrapped_Flipkart"].astype('object')
    
    # ========================================
    # MODE SELECTION: Fresh Start, Resume, or Error List
    # ========================================
    log("")
    log("=" * 60)
    log("SELECT SCRAPING MODE")
    log("=" * 60)
    
    mode_choice = input("\n1 - Fresh Start (reset all and scrape everything)\n2 - Resume (scrape only remaining models)\n\nEnter 1 or 2: ").strip()
    
    if mode_choice == "1":
        log("✓ Mode: FRESH START - Resetting all Scrapped_Flipkart status...")
        df_master["Scrapped_Flipkart"] = "No"
    elif mode_choice == "2":
        log("✓ Mode: RESUME - Continuing from where you left off...")
    else:
        log("⚠️ Invalid input, defaulting to RESUME mode")
    
    # Ask about error list mode
    error_mode = input("\nDo you want to scrape only ERROR models? (y/n): ").strip().lower()
    
    # Determine which products to scrape
    if TEST_MODE:
        df_master_to_scrape = df_master[df_master["Scrapped_Flipkart"] != "Yes"].head(TEST_N).copy()
        log(f"🧪 TEST MODE: Scraping {len(df_master_to_scrape)} products")
    elif error_mode == "y":
        log("")
        log("=" * 60)
        log("ERROR LIST MODE - Select error file")
        log("=" * 60)
        
        try:
            root = tk.Tk()
            root.withdraw()
            error_file = filedialog.askopenfilename(
                title="Select Error List File",
                filetypes=[("Excel files", "*.xlsx *.xls"), ("CSV files", "*.csv"), ("Text files", "*.txt")]
            )
            root.destroy()
            
            if not error_file:
                log("❌ No error file selected, exiting...")
                return
            
            log(f"📂 Selected error file: {error_file}")
            
            # Load error models based on file type
            error_models = []
            if error_file.endswith(('.xlsx', '.xls')):
                # Try to read Excel file
                try:
                    df_error = pd.read_excel(error_file, sheet_name="Error Models")
                    if "Make Model" in df_error.columns:
                        error_models = df_error["Make Model"].dropna().astype(str).tolist()
                    elif "Make-Model" in df_error.columns:
                        error_models = df_error["Make-Model"].dropna().astype(str).tolist()
                    else:
                        log("⚠️ Error file must contain 'Make Model' or 'Make-Model' column")
                        # Try first column as fallback
                        error_models = df_error.iloc[:, 0].dropna().astype(str).tolist()
                except Exception as e:
                    log(f"❌ Error reading Excel file: {e}")
                    return
            elif error_file.endswith('.csv'):
                # Read CSV file
                try:
                    df_error = pd.read_csv(error_file)
                    if "Make Model" in df_error.columns:
                        error_models = df_error["Make Model"].dropna().astype(str).tolist()
                    elif "Make-Model" in df_error.columns:
                        error_models = df_error["Make-Model"].dropna().astype(str).tolist()
                    else:
                        # Try first column as fallback
                        error_models = df_error.iloc[:, 0].dropna().astype(str).tolist()
                except Exception as e:
                    log(f"❌ Error reading CSV file: {e}")
                    return
            elif error_file.endswith('.txt'):
                # Read text file (one model per line)
                try:
                    with open(error_file, 'r', encoding='utf-8') as f:
                        error_models = [line.strip() for line in f if line.strip()]
                except Exception as e:
                    log(f"❌ Error reading text file: {e}")
                    return
            
            if not error_models:
                log("❌ No error models found in file")
                return
            
            log(f"✓ Loaded {len(error_models)} error models from file")
            
            # Normalize error models for matching
            def normalize_for_matching(s):
                """Remove all non-alphanumeric characters and lowercase for fuzzy matching"""
                return re.sub(r'[^a-z0-9]', '', str(s).lower())
            
            error_models_normalized = [normalize_for_matching(m) for m in error_models]
            
            # Create normalized column in master for matching
            df_master["Make-Model-Normalized"] = df_master["Make-Model"].apply(normalize_for_matching)
            
            # Fuzzy match: check if any error model is contained in master or vice versa
            def matches_any_error(master_normalized):
                if not master_normalized:
                    return False
                for error_norm in error_models_normalized:
                    if not error_norm:
                        continue
                    # Match if error is in master OR master is in error
                    if error_norm in master_normalized or master_normalized in error_norm:
                        return True
                return False
            
            mask = df_master["Make-Model-Normalized"].apply(matches_any_error)
            df_master_to_scrape = df_master[mask].copy()
            
            log(f"✓ Filtered to {len(df_master_to_scrape)} models matching the error list")
            
            if len(df_master_to_scrape) == 0:
                log("⚠️ No matches found between error list and master file")
                log("   Make sure the model names are similar in both files")
                return
            
        except Exception as e:
            log(f"❌ Error in error list mode: {e}")
            traceback.print_exc()
            return
    else:
        # Normal mode: scrape all non-scraped models
        df_master_to_scrape = df_master[df_master["Scrapped_Flipkart"] != "Yes"].copy()
        log(f"✓ Total models to scrape: {len(df_master_to_scrape)}")
    
    if len(df_master_to_scrape) == 0:
        log("")
        log("=" * 60)
        log("✅ All models already scraped! Nothing to do.")
        log("=" * 60)
        return
    
    log("")
    log("=" * 60)
    log(f"READY TO SCRAPE {len(df_master_to_scrape)} MODELS")
    log("=" * 60)
    log("")
    
    # Show summary of what will be scraped
    log("📋 Scraping Summary:")
    log(f"   • Total models to scrape: {len(df_master_to_scrape)}")
    log(f"   • Mode: {'FRESH START' if mode_choice == '1' else 'RESUME'}")
    log(f"   • Error list mode: {'YES' if error_mode == 'y' else 'NO'}")
    log(f"   • Max products per model: {MAX_PRODUCTS_PER_MODEL}")
    log(f"   • Browser refresh interval: every {REFRESH_EVERY} searches")
    log(f"   • Auto-save interval: every {SAVE_EVERY} products")
    log("")
    
    # Give user a chance to cancel
    proceed = input("Press ENTER to start scraping (or 'q' to quit): ").strip().lower()
    if proceed == 'q':
        log("❌ Scraping cancelled by user")
        return
    
    log("")
    log("=" * 60)
    log("🚀 STARTING SCRAPER...")
    log("=" * 60)
    log("")
    
    # Initialize browser and result storage
    driver = init_driver()
    wait = WebDriverWait(driver, 15)
    out_rows = []
    
    # Register Ctrl+S hotkey for manual saving
    try:
        keyboard.add_hotkey('ctrl+s', lambda: manual_save(out_rows, file_path, df_master))
        log("💡 Press Ctrl+S anytime to manually save progress")
    except Exception as e:
        log(f"⚠️ Could not register Ctrl+S hotkey: {e}")
        log("   Manual save with Ctrl+S will not be available")
    
    def open_flipkart_homepage():
        """Navigate to Flipkart homepage and close any popups"""
        safe_get("https://www.flipkart.com", driver)
        try:
            # Close login popup if it appears
            popup_close_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'✕')]"))
            )
            popup_close_btn.click()
        except:
            pass
    
    # Initial homepage load
    open_flipkart_homepage()
    
    try:
        # Main scraping loop
        total_to_scrape = len(df_master_to_scrape)
        completed = 0
        
        for idx, row in df_master_to_scrape.iterrows():
            try:
                make_model = str(row["Make-Model"]).strip()
                completed += 1
                progress_pct = (completed / total_to_scrape) * 100
                log(f"--- [{idx}] ({completed}/{total_to_scrape} - {progress_pct:.1f}%) Searching: {make_model}")
                
                # Periodic browser refresh to avoid detection
                if idx > 0 and idx % REFRESH_EVERY == 0:
                    log("🔄 Refreshing homepage to avoid detection...")
                    open_flipkart_homepage()
                
                # Close any popups that might have appeared
                try:
                    popup_close_btn = WebDriverWait(driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'✕')]"))
                    )
                    popup_close_btn.click()
                except:
                    pass
                
                # Locate search box
                search_box = None
                for _ in range(2):
                    try:
                        search_box = driver.find_element(By.XPATH, "//input[contains(@placeholder,'Search')]")
                        if search_box:
                            break
                    except:
                        pass
                    time.sleep(0.5)
                
                if not search_box:
                    search_box = find_search_box(driver, wait)
                
                if not search_box:
                    log(f"❌ Could not locate search box for {make_model}, skipping...")
                    out_rows.append([make_model, 0, 0, 0, "URL not available", "Not found", ""])
                    df_master.loc[row.name, "Scrapped_Flipkart"] = "Yes"
                    continue
                
                # Scroll to search box and clear it
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", search_box)
                except:
                    pass
                
                try:
                    search_box.clear()
                except:
                    pass
                
                try:
                    search_box.send_keys(Keys.CONTROL, 'a')
                    search_box.send_keys(Keys.DELETE)
                except:
                    pass
                
                # Perform search
                time.sleep(random.uniform(0.2, 0.6))
                search_box.send_keys(make_model)
                search_box.send_keys(Keys.RETURN)
                time.sleep(random.uniform(2.0, 4.0))
                
                # Extract product URLs from search results
                product_urls = []
                anchors = extract_product_cards_from_search(driver, make_model)
                
                if not anchors:
                    # No matching products found on search page
                    log(f"  ⚠️ No matching products found for '{make_model}'")
                    
                    # Try searching again with "mobile" keyword appended
                    log(f"  → Retrying search with 'mobile' keyword...")
                    try:
                        search_box = driver.find_element(By.XPATH, "//input[contains(@placeholder,'Search')]")
                        if search_box:
                            try:
                                search_box.clear()
                            except:
                                pass
                            try:
                                search_box.send_keys(Keys.CONTROL, 'a')
                                search_box.send_keys(Keys.DELETE)
                            except:
                                pass
                            time.sleep(random.uniform(0.2, 0.6))
                            search_box.send_keys(f"{make_model} mobile")
                            search_box.send_keys(Keys.RETURN)
                            time.sleep(random.uniform(2.0, 4.0))
                            
                            # Try extracting again
                            anchors = extract_product_cards_from_search(driver, make_model)
                    except:
                        pass
                
                if not anchors:
                    log(f"  ❌ Still no matching products after retry")
                    log(f"     Flipkart may not have this product or showed wrong category")
                    log(f"     Saving as 'Not found' and continuing...")
                    out_rows.append([make_model, 0, 0, 0, "URL not available", "No matching results", ""])
                    df_master.loc[row.name, "Scrapped_Flipkart"] = "Yes"
                    continue
                
                for a in anchors:
                    href = a.get_attribute("href")
                    if href and href.startswith("http"):
                        product_urls.append(href)
                
                # Remove duplicates and limit to MAX_PRODUCTS_PER_MODEL
                seen = set()
                product_urls = [u for u in product_urls if not (u in seen or seen.add(u))]
                product_urls = product_urls[:MAX_PRODUCTS_PER_MODEL]
                search_urls = product_urls.copy()
                
                # MODIFIED: Store selling prices and MRPs separately for each variant
                variant_selling_prices = []
                variant_mrps = []
                variant_data = []  # Store all variant info for reference
                
                # Detect dynamic classes for this search page
                class_tokens_page = collect_candidate_classes(driver)
                heur = find_classes_by_pattern(class_tokens_page)
                heur_title = heur.get("title", [])[:4]
                heur_price = heur.get("price", [])[:4]
                
                # Visit each product page
                for product_url in product_urls:
                    if not safe_get(product_url, driver):
                        continue
                    
                    # CRITICAL: Verify this is actually a mobile phone, not a laptop or other product
                    if not is_mobile_phone_product(driver):
                        log(f"  ✗ SKIPPED: Not a mobile phone (wrong category)")
                        continue
                    
                    # Check product availability
                    availability = "Available"
                    try:
                        av_elem = driver.find_elements(By.XPATH, 
                            "//div[contains(text(),'Only') or contains(text(),'Unavailable') or contains(text(),'Out of stock')]")
                        if av_elem:
                            availability = av_elem[0].text.strip()
                    except:
                        pass
                    
                    # Extract variant links (colors, storage options, etc.)
                    variant_links = extract_variant_links(driver)
                    if not variant_links:
                        variant_links = [product_url]
                    
                    # Check each variant
                    for v_url in variant_links:
                        if not safe_get(v_url, driver):
                            continue
                        
                        # Update heuristics for this specific page
                        page_classes = collect_candidate_classes(driver)
                        heur_page = find_classes_by_pattern(page_classes)
                        heur_title_page = heur_page.get("title", [])[:5]
                        heur_price_page = heur_page.get("price", [])[:5]
                        
                        # Extract product details
                        title = extract_title_from_product_page(driver, heur_title_page or heur_title)
                        if not title:
                            title = make_model  # Fallback to search term
                        
                        log(f"  → Checking product: {title}")
                        
                        # Filter out accessories
                        if any(k in title.lower() for k in EXCLUDE_KEYWORDS):
                            log(f"  ✗ SKIPPED (accessory): {title}")
                            continue
                        
                        # Verify title matches search query (STRICT PREFIX MATCHING)
                        if not model_matches_title(make_model, title):
                            log(f"  ✗ SKIPPED (different variant): {title}")
                            log(f"     Searched for: '{make_model}' but found: '{title}'")
                            continue
                        
                        log(f"  ✓ MATCH confirmed: {title}")
                        
                        # Extract pricing - MODIFIED to separate selling price and MRP
                        selling_price, mrp = extract_price_and_mrp_from_product_page(
                            driver, heur_price_page or heur_price
                        )
                        
                        if not selling_price:
                            log(f"Price not found on page; taking screenshot for debugging.")
                            save_screenshot(driver, name_prefix=f"no_price_{idx}")
                        
                        # CRITICAL: Store selling prices and MRPs in separate lists
                        if selling_price > 0:
                            variant_selling_prices.append(selling_price)
                        
                        # If MRP not found, use selling price as fallback
                        if mrp > 0:
                            variant_mrps.append(mrp)
                        elif selling_price > 0:
                            variant_mrps.append(selling_price)
                        
                        # Store variant info for reference
                        variant_data.append({
                            "title": title,
                            "selling_price": selling_price,
                            "mrp": mrp,
                            "url": v_url,
                            "availability": availability
                        })
                
                # MODIFIED: Aggregate results using separate lists
                # Lowest_Price = minimum selling price
                # Highest_Price = maximum selling price
                # MRP = maximum MRP value
                
                # Log summary of what was found
                log(f"  → Summary: Found {len(variant_data)} matching variants")
                
                # Filter out outliers in selling prices before aggregation
                if variant_selling_prices:
                    # Remove duplicates
                    unique_selling = sorted(list(set(variant_selling_prices)))
                    
                    # If we have a wide variance, filter outliers
                    if len(unique_selling) > 1 and max(unique_selling) > 3 * min(unique_selling):
                        # Keep prices that are at least 50% of the maximum
                        threshold = max(unique_selling) * 0.5
                        filtered_selling = [p for p in unique_selling if p >= threshold]
                        if filtered_selling:
                            log(f"  ⚠️ Model-level: Filtered price outliers {unique_selling} → {filtered_selling}")
                            variant_selling_prices = filtered_selling
                    
                    lowest_price = min(variant_selling_prices)
                    highest_price = max(variant_selling_prices)
                else:
                    lowest_price = 0
                    highest_price = 0
                
                if variant_mrps:
                    # Remove duplicates
                    unique_mrps = sorted(list(set(variant_mrps)))
                    
                    # If we have wide variance in MRPs, filter outliers
                    if len(unique_mrps) > 1 and max(unique_mrps) > 3 * min(unique_mrps):
                        threshold = max(unique_mrps) * 0.5
                        filtered_mrps = [p for p in unique_mrps if p >= threshold]
                        if filtered_mrps:
                            log(f"  ⚠️ Model-level: Filtered MRP outliers {unique_mrps} → {filtered_mrps}")
                            variant_mrps = filtered_mrps
                    
                    mrp_final = max(variant_mrps)
                else:
                    mrp_final = 0
                
                # Get first variant's URL and availability for reference
                if variant_data:
                    url_final = variant_data[0]["url"]
                    availability_final = variant_data[0]["availability"]
                else:
                    url_final = "URL not available"
                    availability_final = "Not found"
                
                # Save result for this product
                out_rows.append([
                    make_model, lowest_price, highest_price, mrp_final, 
                    url_final, availability_final, ", ".join(search_urls)
                ])
                df_master.loc[row.name, "Scrapped_Flipkart"] = "Yes"
                
                # Log the aggregated results for verification
                log(f"✓ {make_model}: Low={lowest_price}, High={highest_price}, MRP={mrp_final}")
                
                # Periodic save and browser restart
                if idx > 0 and idx % SAVE_EVERY == 0:
                    log("💾 Periodic save and browser restart...")
                    save_progress(out_rows, file_path, df_master)
                    try:
                        driver.quit()
                    except:
                        pass
                    time.sleep(random.uniform(3, 6))
                    driver = init_driver()
                    wait = WebDriverWait(driver, 15)
                    open_flipkart_homepage()
            
            except Exception as e:
                log("❌ Exception in loop — logging error and continuing")
                traceback.print_exc()
                try:
                    save_screenshot(driver, name_prefix=f"exception_{idx}")
                except:
                    pass
                out_rows.append([
                    str(row.get("Make-Model", "")), 0, 0, 0, 
                    "URL not available", "Error", str(e)
                ])
                df_master.loc[row.name, "Scrapped_Flipkart"] = "Yes"
                time.sleep(random.uniform(1.0, 3.0))
                continue
    
    finally:
        # Cleanup keyboard hotkeys
        try:
            keyboard.unhook_all_hotkeys()
        except:
            pass
        
        # Cleanup and final save
        try:
            driver.quit()
        except:
            pass
        save_progress(out_rows, file_path, df_master)
        
        # Show completion summary
        log("")
        log("=" * 60)
        log("✅ SCRAPING COMPLETE!")
        log("=" * 60)
        log(f"📊 Summary:")
        log(f"   • Total models processed: {len(out_rows)}")
        log(f"   • Successful: {sum(1 for row in out_rows if row[1] > 0)}")
        log(f"   • Failed/Not found: {sum(1 for row in out_rows if row[1] == 0)}")
        log(f"   • All results saved to: {file_path}")
        log("=" * 60)

if __name__ == "__main__":
    main()
