# 📱 Mobile Phone Price Master Builder

A personal portfolio project that collects mobile phone prices from **Amazon India** and **Flipkart**, retrieves launch dates from **GSMArena**, and consolidates everything into a structured price master using **Microsoft Excel**.

---

## 🗂 Table of Contents

1. [Project Overview](#project-overview)
2. [Objective](#objective)
3. [Data Sources](#data-sources)
4. [End-to-End Workflow](#end-to-end-workflow)
5. [Price Master Creation Logic](#price-master-creation-logic)
6. [Project Folder Structure](#project-folder-structure)
7. [Inputs & Outputs](#inputs--outputs)
8. [How to Run the Scripts](#how-to-run-the-scripts)
9. [Sample Output Explanation](#sample-output-explanation)
10. [Use Cases](#use-cases)
11. [Limitations](#limitations)
12. [Future Improvements](#future-improvements)
13. [Interview Discussion Points](#interview-discussion-points)

---

## Project Overview

This project is a **semi-automated data pipeline** I built to solve a very practical problem: keeping track of mobile phone prices across multiple e-commerce platforms at scale. Doing this manually for hundreds of models and their variants is tedious and error-prone. I built three Python scrapers that handle the heavy lifting — searching, matching, and extracting prices — and then I bring everything together in Excel using VLOOKUP and MIN/MAX formulas to produce a clean, validated price master.

The pipeline is **intentionally semi-automated**. The scraping is automated, but the final consolidation step happens in Excel. This was a deliberate choice — it lets me visually inspect the data, catch naming mismatches between platforms, and validate prices before committing them to the master file. Speed matters less here than accuracy.

---

## Objective

Mobile phone prices on Amazon and Flipkart:
- Vary by RAM and storage variant (e.g., 8GB+128GB vs 8GB+256GB)
- Change frequently
- Are not always listed consistently — the same model may appear under slightly different names on different platforms

The goal of this project is to build a **per-variant price master** that captures the lowest available price, the highest price (or price range), and the MRP across both platforms, along with the device's official India launch date — all tied together using a consistent model + variant key.

---

## Data Sources

| Source | What is Scraped | Script |
|---|---|---|
| **Amazon India** (amazon.in) | Selling price (low & high), MRP, product URL, availability | `amazon_price_scraper.py` |
| **Flipkart** (flipkart.com) | Selling price (low & high), MRP, product URL, availability | `flipkart_price_scraper.py` |
| **GSMArena** (gsmarena.com) | India launch date | `Launch_Date_scraper.py` |

---

## End-to-End Workflow

### Step 1 — Prepare the Input Master File

Before running any scraper, you need a Master Excel file with a `Master` sheet that contains at minimum a column named **`Make-Model`** (e.g., `Samsung Galaxy S24`, `Apple iPhone 15 Pro`). This list drives all three scrapers.

### Step 2 — Run the Amazon Scraper

The Amazon scraper reads the `Master` sheet, searches Amazon India for each model, and extracts prices. For each model it:

1. Searches Amazon with the query `{model name} mobile phone`
2. Checks up to 8 search results per model
3. Validates each result — confirms it is a mobile phone (via breadcrumb check), filters out accessories (cases, chargers, etc.), and matches the product title against the search query using fuzzy token-overlap logic (requires at least 70% of search tokens to match)
4. Detects and visits all color/storage variants of a matched product (up to 5 variants per product, using Amazon's twister/variation selectors)
5. Extracts the **selling price** and **MRP** (strikethrough price) from each variant separately, with sanity checks (price must be between ₹3,000 and ₹2,00,000; MRP must be greater than selling price and not more than 3x the selling price)
6. Aggregates across all matched products and variants: records the **minimum selling price**, **maximum selling price**, and the **highest MRP** found for that model
7. Marks the model as scraped in the `Master` sheet and saves results to the **`Amazon` sheet** of the same Excel file

Progress is saved automatically every 100 models, and you can trigger a manual save anytime using **Ctrl+S** without interrupting the scraping.

### Step 3 — Run the Flipkart Scraper

The Flipkart scraper follows the same structure as the Amazon scraper but handles Flipkart-specific page layouts. Key differences:

- Uses dynamic CSS class detection to handle Flipkart's frequently changing class names
- Has an additional **Error List mode** (Mode 3) — you can supply a separate list of models that failed in a previous run and retry only those
- Strikethrough MRP detection uses both known class patterns and inline style inspection
- Includes an outlier filter: if extracted selling prices vary wildly (max > 3× min), low-value outliers (likely "Save ₹X" text picked up accidentally) are discarded
- Results are written to the **`Flipkart` sheet** of the same Excel file

### Step 4 — Run the GSMArena Scraper

This scraper queries GSMArena's search for each model and navigates to the device's detail page to extract the launch/announced date. Key behaviors:

- Uses **strict prefix-based title matching** — `iPhone 13` will not match `iPhone 13 Pro` or `iPhone 13 Mini`, preventing cross-variant date contamination
- Extracts the date using a set of regex patterns ordered from most-specific (full date: `March 23, 2021`) to least-specific (year only: `2021`)
- Supports an optional **proxy list** for IP rotation (currently a placeholder)
- Includes a **ChromeDriver socket recovery** mechanism — if the WebDriver connection becomes poisoned/unresponsive, the browser process is automatically restarted rather than hanging indefinitely
- Writes results back directly to the **`Master` sheet**: `Launch_Date_India`, `Launch_Source`, `Launch_URL`, `Launch_Availability`

### Step 5 — Excel-Based Consolidation (Manual)

This is the step that produces the final price master. After all three scrapers have run:

1. Open the output Excel file (which now has `Master`, `Amazon`, and `Flipkart` sheets)
2. Open or create a separate **consolidation sheet** alongside the master model list
3. Use **`Make Model + RAM + Storage`** as the composite join key across all sheets
4. Use **VLOOKUP** formulas to pull:
   - Amazon minimum selling price (`Low_Price` from the Amazon sheet)
   - Amazon maximum selling price (`High_Price` from the Amazon sheet)
   - Flipkart minimum selling price (`Low_Price` from the Flipkart sheet)
   - Flipkart maximum selling price (`High_Price` from the Flipkart sheet)
   - Launch date (`Launch_Date_India` from the Master sheet)
5. Once Amazon and Flipkart prices are aligned side-by-side per variant, visually inspect for:
   - Model naming mismatches (e.g., "Galaxy S24 5G" on one platform vs "Galaxy S24" on another)
   - Missing variants that were not picked up by the scraper
   - Outlier prices that slipped through validation
6. Correct any mismatches manually before finalizing
7. Apply **`MIN()`** across both platforms' low prices to get the **cheapest available price**
8. Apply **`MAX()`** across both platforms' high prices to get the **highest price / full price range**
9. Save the final consolidated sheet as the official **price master Excel file**

This manual validation step is intentional. It exists because model names are not standardized across platforms, and automated joins alone are not reliable enough for production-quality data.

---

## Price Master Creation Logic

### Variant Handling

Each scraper is variant-aware. When a matching product is found on either platform, the scraper navigates to all color and storage variants (e.g., 8GB+128GB, 8GB+256GB, 12GB+256GB) and records prices from each. This means the `Low_Price` and `High_Price` in the scraper output already reflect the **full variant price spread** for that model on that platform.

### VLOOKUP-Based Merging in Excel

The consolidation key used in VLOOKUP is:

```
Mobile Model + RAM + Storage
```

For example: `Samsung Galaxy S24 8GB 256GB`

This key is constructed in the consolidation sheet and used to look up prices from the Amazon and Flipkart output sheets. Since scraper outputs store results by model name (not yet broken out by RAM/Storage), the master model list — which you maintain separately with explicit variant breakdowns — serves as the authoritative reference for matching.

### MIN/MAX Price Range Calculation

After VLOOKUPs are in place:

- **Final Low Price** = `MIN(Amazon_Low, Flipkart_Low)` — the cheapest the phone can be bought across both platforms
- **Final High Price** = `MAX(Amazon_High, Flipkart_High)` — the upper end of the price range, useful for understanding full price spread

These two values form the price range displayed in the final price master.

---

## Project Folder Structure

```
Mobile-Phone-Price-Master-Builder/
│
├── amazon_price_scraper.py         # Amazon India price scraper
├── flipkart_price_scraper.py       # Flipkart price scraper
├── Launch_Date_scraper.py          # GSMArena launch date scraper
│
├── requirements.txt                # Python dependencies
├── README.md                       # This file
│
├── chromedriver-win64/             # ChromeDriver binary (download separately)
│   └── chromedriver.exe
│
├── data/
│   └── master_input.xlsx           # Input: your master model list (user-provided)
│
├── debug_screenshots_amazon/       # Auto-created: Amazon error screenshots
├── debug_screenshots/              # Auto-created: Flipkart error screenshots
└── debug_screenshots_gsm/          # Auto-created: GSMArena error screenshots
```

> **Note:** ChromeDriver is not included in this repository. Download the version that matches your installed Chrome browser from [chromedriver.chromium.org](https://chromedriver.chromium.org/downloads) and update the `CHROMEDRIVER_PATH` constant in each script.

---

## Inputs & Outputs

### Input

| File | Sheet | Required Columns |
|---|---|---|
| `master_input.xlsx` | `Master` | `Make-Model` (e.g., `Samsung Galaxy S24`) |

### Scraper Outputs (written back to the same Excel file)

**Amazon sheet** (`Amazon`):

| Column | Description |
|---|---|
| `Model` | Mobile model name from the master list |
| `Low_Price` | Lowest selling price found across all matched products/variants |
| `High_Price` | Highest selling price found across all matched products/variants |
| `MRP` | Highest MRP (original price) found |
| `Product_URL` | URL of the first matched product |
| `Availability` | Stock status from the product page |
| `Search_URLs` | Amazon search URL(s) used for this model |

**Flipkart sheet** (`Flipkart`): Same column structure as above.

**Master sheet** (updated by the GSMArena scraper):

| Column | Description |
|---|---|
| `Launch_Date_India` | Extracted launch/announced date |
| `Launch_Source` | Source used (GSMArena) |
| `Launch_URL` | Direct URL of the GSMArena device page |
| `Launch_Availability` | Status: `Found`, `No exact date`, or `Not found` |
| `Launch_Date_Scrapped` | Tracking flag: `Yes` / `No` |

### Final Output

A manually consolidated Excel file (price master) with one row per model+variant, containing:
- Amazon low price, Amazon high price
- Flipkart low price, Flipkart high price
- Final MIN price (cheapest across platforms)
- Final MAX price (highest across platforms)
- Launch date

---

## How to Run the Scripts

### Prerequisites

```
Python 3.8+
Google Chrome browser (latest)
ChromeDriver (matching your Chrome version)
```

### Install Dependencies

```bash
pip install selenium pandas openpyxl keyboard
```

### Configure ChromeDriver Path

In each script, update this line to point to your ChromeDriver:

```python
CHROMEDRIVER_PATH = r"C:\path\to\chromedriver.exe"
```

### Run the Amazon Scraper

```bash
python amazon_price_scraper.py
```

When prompted:
1. A file dialog opens — select your master Excel file
2. Choose mode: `1` for Fresh Start (scrape all models), `2` to Resume from where you left off
3. The browser opens and begins scraping
4. Press `Ctrl+S` anytime to save progress without stopping

### Run the Flipkart Scraper

```bash
python flipkart_price_scraper.py
```

Same prompts as above, with an additional option to run from an **error list** (a separate file listing models that failed in a previous run).

### Run the GSMArena Scraper

```bash
python Launch_Date_scraper.py
```

When prompted:
1. Select your master Excel file
2. Choose Fresh Start or Resume
3. Optionally supply a separate error-list file to retry specific models only

### Configuration Options (in each script)

| Setting | Default | Description |
|---|---|---|
| `HEADLESS_MODE` | `False` | Run browser invisibly (True) or visibly (False) |
| `TEST_MODE` | `False` | Scrape only `TEST_N` models — useful for a quick test |
| `PRODUCTS_TO_CHECK` | `8` (Amazon) / `5` (Flipkart) | Max search results to visit per model |
| `REFRESH_EVERY` | `80` | Refresh browser homepage after N models (anti-detection) |
| `SAVE_EVERY` | `100` | Auto-save after processing N models |

---

## Sample Output Explanation

After running the Amazon scraper on a model like `Apple iPhone 15`:

```
Model           | Low_Price | High_Price | MRP    | Availability
Apple iPhone 15 | 69900     | 79900      | 79900  | In Stock
```

This means the scraper found iPhone 15 listed at prices ranging from ₹69,900 (e.g., 128GB variant) up to ₹79,900 (e.g., 256GB variant), with an MRP of ₹79,900. After Excel consolidation — comparing against Flipkart prices — the final master might show:

```
Model           | Variant       | Amazon Low | Flipkart Low | Final MIN | Final MAX
Apple iPhone 15 | 8GB + 128GB   | 69900      | 69999        | 69900     | 72000
Apple iPhone 15 | 8GB + 256GB   | 79900      | 79900        | 79900     | 82000
```

---

## Use Cases

- **Retail/distribution teams** tracking competitive pricing across platforms before placing bulk orders
- **Price trend analysis** — run the scrapers periodically and compare historical master files
- **Consumer research** — quickly identify the cheapest platform for a specific model and variant
- **Category management** — understand the full price spread across a brand's lineup

---

## Limitations

- **Platform ToS:** Web scraping may conflict with the terms of service of Amazon, Flipkart, and GSMArena. This project is for personal/educational use only.
- **Selector brittleness:** Both Amazon and Flipkart frequently change their page structure and CSS class names. The scrapers use multiple fallback selectors, but may break after major site redesigns.
- **Windows-only keyboard hook:** The `keyboard` library (used for Ctrl+S manual save) requires Windows. On Linux/Mac, this feature will not work.
- **ChromeDriver version dependency:** ChromeDriver must exactly match the installed Chrome version. Mismatches cause immediate failures.
- **No headless stability guarantee:** Running in headless mode (`HEADLESS_MODE = True`) can sometimes cause more CAPTCHAs and detection by anti-bot systems.
- **Manual Excel step required:** The pipeline is not fully automated end-to-end. The consolidation step requires manual work in Excel.
- **Model name normalization is imperfect:** Despite fuzzy matching, some models may not match correctly if names differ significantly between your master list and how they are listed on the platform.
- **Launch date coverage:** GSMArena is the sole source for launch dates. If a model is not listed on GSMArena or lacks an "announced" field, it will be marked `Not found`.

---

## Future Improvements

- **Automated consolidation:** Replace the manual Excel VLOOKUP step with a Python-based merge (using `pandas.merge`) to produce the final price master without manual intervention
- **Scheduled runs:** Wrap the pipeline in a task scheduler (Windows Task Scheduler or cron) to run weekly and maintain a price history
- **Database integration:** Store results in SQLite or PostgreSQL instead of Excel to support trend queries
- **Price history tracking:** Append results to a time-series table rather than overwriting, allowing you to chart price movements over time
- **Better CAPTCHA handling:** Integrate 2Captcha or a similar service to handle CAPTCHA interruptions automatically
- **Multi-threaded scraping:** Run multiple browser instances in parallel (with proper rate limiting) to reduce total scraping time
- **Improved variant splitting:** Automatically decompose scraped model names into separate Brand, RAM, and Storage columns during scraping — reducing reliance on manual Excel cleanup

---

## Interview Discussion Points

This project comes up frequently in interviews. Here is how I explain the key decisions:

**"Why use Selenium instead of requests + BeautifulSoup?"**
Amazon and Flipkart render much of their pricing data dynamically via JavaScript. Requests-based scrapers would get a shell page without prices. Selenium drives a real browser so the full DOM — including variant selectors and price elements — is available.

**"How did you handle model name mismatches between platforms?"**
Two levels of defense. At scrape time: fuzzy token-overlap matching (Amazon) and prefix-based strict matching (Flipkart/GSMArena) both reject results where the searched model and the found product title are too different. At consolidation time: the Excel validation step lets me manually catch anything that slipped through.

**"Why not automate the Excel consolidation step?"**
Data quality. Model names are not standardized — the same phone might be listed as `Samsung Galaxy S24` on Flipkart and `Galaxy S24 5G` on Amazon. A fully automated join would silently drop those or create duplicates. The manual review step is intentional quality control, not a gap in the project.

**"How do you prevent being blocked?"**
Random delays between requests, user-agent spoofing, disabling Chrome's automation flags, and periodic homepage refreshes to reset session state. The browser also restarts automatically after every 100 models to clear any fingerprinting state.

**"What does the price range in the output represent?"**
It reflects the full variant spread, not just one listing. For example, an iPhone 15 entry with Low=₹69,900 and High=₹79,900 means the scraper found that model at ₹69,900 for the 128GB variant and ₹79,900 for the 256GB variant — both on the same platform in the same scraping session.

---

## Tech Stack

- **Python 3.x**
- **Selenium** — browser automation
- **pandas + openpyxl** — data handling and Excel I/O
- **keyboard** — Ctrl+S hotkey for manual save
- **tkinter** — file selection dialog
- **Microsoft Excel** — final data consolidation and validation

---

*This is a personal portfolio project built for learning and practical data collection purposes. It is not affiliated with Amazon, Flipkart, or GSMArena.*
