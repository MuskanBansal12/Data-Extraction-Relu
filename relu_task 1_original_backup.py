import json
import csv
import re
import time

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# ============================================================
# CONFIGURATION
# ============================================================

START_URL = "https://disneycruise.disney.go.com/en-in/"

OUTPUT_CSV = "disney_cruise_data.csv"
TEMP_JSON = "disney_cruise_raw.json"

# Assignment requirement
TARGET_PAGES = 35

WAIT_TIME = 15

# Small pause between scrolls
SCROLL_PAUSE = 0.5

# How many times the page can remain unchanged
# before we assume there are no more results
MAX_STABLE_SCROLLS = 4


# ============================================================
# DRIVER SETUP
# ============================================================

options = Options()

# Keep browser visible while debugging
# Uncomment this later if you want headless mode
# options.add_argument("--headless=new")

options.add_argument("--disable-gpu")
options.add_argument("--window-size=1920,1080")
options.add_argument("--disable-notifications")
options.add_argument("--disable-popup-blocking")

service = Service(ChromeDriverManager().install())

driver = webdriver.Chrome(
    service=service,
    options=options
)


# ============================================================
# STEP 1
# OPEN DISNEY CRUISE
# ============================================================

def open_disney():

    print("\n" + "=" * 60)
    print("STEP 1 - OPENING DISNEY CRUISE")
    print("=" * 60)

    driver.get(START_URL)

    WebDriverWait(
        driver,
        WAIT_TIME,
        poll_frequency=0.2
    ).until(
        EC.presence_of_element_located(
            (By.TAG_NAME, "body")
        )
    )

    print("Title:", driver.title)
    print("URL:", driver.current_url)


# ============================================================
# STEP 2
# CLICK VIEW DATES
# ============================================================

def click_view_dates():

    print("\n" + "=" * 60)
    print("STEP 2 - CLICKING VIEW DATES")
    print("=" * 60)

    wait = WebDriverWait(
        driver,
        WAIT_TIME,
        poll_frequency=0.2
    )

    # Different possible versions of the View Dates button
    selectors = [
        (
            By.XPATH,
            "//a[contains(normalize-space(.), 'View Dates')]"
        ),
        (
            By.XPATH,
            "//button[contains(normalize-space(.), 'View Dates')]"
        ),
        (
            By.XPATH,
            "//*[contains(normalize-space(.), 'View Dates')]"
        )
    ]

    view_dates = None

    for by, selector in selectors:

        try:

            view_dates = wait.until(
                EC.element_to_be_clickable(
                    (by, selector)
                )
            )

            if view_dates:
                break

        except Exception:
            continue

    if view_dates is None:
        raise Exception("View Dates button could not be found.")

    print("View Dates found.")

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        view_dates
    )

    time.sleep(0.5)

    driver.execute_script(
        "arguments[0].click();",
        view_dates
    )

    print("Waiting for cruise results...")

    # IMPORTANT:
    # Actual cruise cards are custom elements:
    #
    # dcl-product-card.dcl-infinite-scroll-child

    wait.until(
        EC.presence_of_element_located(
            (
                By.CSS_SELECTOR,
                "dcl-product-card.dcl-infinite-scroll-child"
            )
        )
    )

    print("Cruise results ready.")

    cards = driver.find_elements(
        By.CSS_SELECTOR,
        "dcl-product-card.dcl-infinite-scroll-child"
    )

    print(
        f"Product cards currently visible: {len(cards)}"
    )

    print("Current URL:", driver.current_url)


# ============================================================
# HELPER
# NORMALIZE TEXT
# ============================================================

def normalize_text(text):

    if not text:
        return ""

    return " ".join(text.split()).strip()


# ============================================================
# GET CURRENT PRODUCT CARDS
# ============================================================

def get_product_cards():

    return driver.find_elements(
        By.CSS_SELECTOR,
        "dcl-product-card.dcl-infinite-scroll-child"
    )


# ============================================================
# CREATE CARD SIGNATURE
# ============================================================

def get_card_signature(card):

    """
    Creates a unique-ish signature for a cruise card.

    We use the complete card text rather than only:
        destination
        duration
        price

    because several different cards can have similar values.
    """

    try:

        text = normalize_text(card.text)

        # Try to get a useful link as well
        links = card.find_elements(
            By.TAG_NAME,
            "a"
        )

        hrefs = []

        for link in links:

            try:

                href = link.get_attribute("href")

                if href:
                    hrefs.append(href.strip())

            except Exception:
                pass

        return (
            text,
            tuple(sorted(set(hrefs)))
        )

    except Exception:

        return ("", ())


# ============================================================
# GET UNIQUE CARDS CURRENTLY LOADED
# ============================================================

def get_unique_loaded_cards():

    cards = get_product_cards()

    unique_cards = []

    seen = set()

    for card in cards:

        signature = get_card_signature(card)

        if not signature[0]:
            continue

        if signature not in seen:

            seen.add(signature)
            unique_cards.append(card)

    return unique_cards


# ============================================================
# SCROLL AND LOAD MORE CARDS
# ============================================================

def load_more_cards():

    print("\nLoading dynamically rendered cruise cards...")

    stable_scrolls = 0

    previous_count = len(
        get_product_cards()
    )

    scroll_number = 0

    while True:

        scroll_number += 1

        print(
            f"Scroll {scroll_number}: "
            f"{previous_count} cards loaded"
        )

        # Scroll near bottom
        driver.execute_script(
            """
            window.scrollTo({
                top: document.body.scrollHeight,
                behavior: 'smooth'
            });
            """
        )

        time.sleep(SCROLL_PAUSE)

        # Wait briefly for Angular/JS to render new cards
        start_time = time.time()

        while time.time() - start_time < 5:

            current_count = len(
                get_product_cards()
            )

            if current_count > previous_count:
                break

            time.sleep(0.2)

        current_count = len(
            get_product_cards()
        )

        if current_count > previous_count:

            print(
                f"New cards loaded: "
                f"{previous_count} -> {current_count}"
            )

            previous_count = current_count
            stable_scrolls = 0

        else:

            stable_scrolls += 1

            print(
                f"No new cards loaded "
                f"({stable_scrolls}/{MAX_STABLE_SCROLLS})"
            )

        # Check whether we reached the bottom
        at_bottom = driver.execute_script(
            """
            return (
                window.innerHeight + window.scrollY
                >= document.body.scrollHeight - 100
            );
            """
        )

        if not at_bottom:

            stable_scrolls = 0
            continue

        if stable_scrolls >= MAX_STABLE_SCROLLS:

            print(
                "Reached bottom of currently "
                "loaded results."
            )

            break

        # Give infinite scroll another chance
        time.sleep(1)

    print(
        "Total product cards loaded:",
        len(get_product_cards())
    )


# ============================================================
# TEXT EXTRACTION HELPERS
# ============================================================

def get_element_text(parent, selector):

    try:

        element = parent.find_element(
            By.CSS_SELECTOR,
            selector
        )

        return normalize_text(
            element.text
        )

    except Exception:

        return ""


# ============================================================
# EXTRACT SHIP
# ============================================================

def extract_ship(card):

    text = normalize_text(card.text)

    known_ships = [
        "Disney Adventure",
        "Disney Wish",
        "Disney Treasure",
        "Disney Destiny",
        "Disney Fantasy",
        "Disney Dream",
        "Disney Wonder",
        "Disney Magic"
    ]

    for ship in known_ships:

        if ship.lower() in text.lower():
            return ship

    return ""


# ============================================================
# EXTRACT DURATION
# ============================================================

def extract_duration(card):

    text = normalize_text(card.text)

    patterns = [
        r"(\d+)-Night",
        r"(\d+)\s*Night",
        r"(\d+)-night",
        r"(\d+)\s*night"
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text
        )

        if match:

            return f"{match.group(1)} nights"

    return ""


# ============================================================
# EXTRACT DATES
# ============================================================

def extract_dates(card):

    text = normalize_text(card.text)

    # Common formats
    date_patterns = [
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}-\d{1,2}-\d{4}\b",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b"
    ]

    dates = []

    for pattern in date_patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        dates.extend(matches)

    # Remove duplicates while preserving order
    unique_dates = []

    for date in dates:

        if date not in unique_dates:
            unique_dates.append(date)

    if len(unique_dates) >= 2:

        return (
            unique_dates[0],
            unique_dates[1]
        )

    elif len(unique_dates) == 1:

        return (
            unique_dates[0],
            ""
        )

    return ("", "")


# ============================================================
# EXTRACT PRICE
# ============================================================

def extract_price(card):

    text = normalize_text(card.text)

    # Example:
    # 1,142 USD
    # 1,533 USD

    patterns = [
        r"\$\s*([\d,]+)",
        r"([\d,]+)\s*USD"
    ]

    for pattern in patterns:

        matches = re.findall(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if matches:

            # First matching price
            return matches[0].replace(",", "")

    return ""


# ============================================================
# EXTRACT LOCATIONS
# ============================================================

def extract_locations(card):

    text = normalize_text(card.text)

    departure_port = ""
    destination = ""

    # --------------------------------------------------------
    # Look for:
    #
    # 3-Night Cruise from Singapore
    # --------------------------------------------------------

    match = re.search(
        r"Cruise\s+from\s+([A-Za-zÀ-ÿ .'-]+)",
        text,
        flags=re.IGNORECASE
    )

    if match:

        departure_port = (
            match.group(1)
            .strip()
        )

        # Remove accidental trailing words
        departure_port = re.split(
            r"\b(?:What's|Price|Sailing|Rate|Show)\b",
            departure_port,
            flags=re.IGNORECASE
        )[0].strip()

    # --------------------------------------------------------
    # Look for:
    #
    # Sailing to
    # Langkawi, Malaysia
    # --------------------------------------------------------

    match = re.search(
        r"Sailing to\s+(.+?)(?=\s+Price from|\s+\$|\s+Rate Details|\s+Show \d+ Dates|$)",
        text,
        flags=re.IGNORECASE
    )

    if match:

        destination = (
            match.group(1)
            .strip()
        )

    return (
        departure_port,
        destination
    )


# ============================================================
# EXTRACT CRUISE NAME
# ============================================================

def extract_cruise_name(card):

    text = normalize_text(card.text)

    # Try heading elements first
    heading_selectors = [
        "h1",
        "h2",
        "h3",
        "h4",
        "[role='heading']"
    ]

    for selector in heading_selectors:

        try:

            elements = card.find_elements(
                By.CSS_SELECTOR,
                selector
            )

            for element in elements:

                heading = normalize_text(
                    element.text
                )

                if heading:
                    return heading

        except Exception:
            pass

    # Fallback:
    # Find a line containing Cruise
    lines = [
        line.strip()
        for line in card.text.splitlines()
        if line.strip()
    ]

    for line in lines:

        if "Cruise from" in line:

            return line

        if "Cruise" in line:

            return line

    return ""


# ============================================================
# EXTRACT DETAILS URL
# ============================================================

def extract_details_url(card):

    try:

        links = card.find_elements(
            By.TAG_NAME,
            "a"
        )

        for link in links:

            href = link.get_attribute(
                "href"
            )

            if href:

                href = href.strip()

                # Ignore javascript links
                if not href.startswith(
                    "javascript:"
                ):
                    return href

    except Exception:
        pass

    return ""


# ============================================================
# EXTRACT CARD
# ============================================================

def extract_cruise(
    card,
    page_number,
    card_number
):

    raw_card_text = card.text.strip()

    normalized_card_text = normalize_text(
        raw_card_text
    )

    cruise_name = extract_cruise_name(
        card
    )

    ship = extract_ship(
        card
    )

    duration = extract_duration(
        card
    )

    departure_date, return_date = (
        extract_dates(card)
    )

    price = extract_price(
        card
    )

    departure_port, destination = (
        extract_locations(card)
    )

    details_url = extract_details_url(
        card
    )

    # Try to find "Show X Dates"
    show_dates = ""

    match = re.search(
        r"Show\s+(\d+)\s+Dates",
        normalized_card_text,
        flags=re.IGNORECASE
    )

    if match:

        show_dates = match.group(1)

    record = {

        "page_number": page_number,

        "card_number": card_number,

        "cruise_name": cruise_name,

        "ship": ship,

        "departure_port": departure_port,

        "destination": destination,

        "duration": duration,

        "departure_date": departure_date,

        "return_date": return_date,

        "price": price,

        "number_of_dates": show_dates,

        "details_url": details_url,

        "raw_card_text": raw_card_text
    }

    return record


# ============================================================
# VALIDATE RECORD
# ============================================================

def validate_record(record):

    # Raw content must exist
    if not record.get(
        "raw_card_text",
        ""
    ).strip():

        return False

    # A location must exist
    location_exists = (
        bool(
            record.get(
                "departure_port",
                ""
            ).strip()
        )
        or
        bool(
            record.get(
                "destination",
                ""
            ).strip()
        )
    )

    if not location_exists:

        return False

    return True


# ============================================================
# SCRAPE CURRENT LOADED RESULTS
# ============================================================

def scrape_current_page(
    page_number
):

    print("\n" + "=" * 60)
    print(
        f"SCRAPING RESULT BATCH {page_number}"
    )
    print("=" * 60)

    # Load more cards
    load_more_cards()

    cards = get_unique_loaded_cards()

    print(
        f"Actual cruise product cards found: "
        f"{len(cards)}"
    )

    records = []

    for index, card in enumerate(
        cards,
        start=1
    ):

        try:

            record = extract_cruise(
                card,
                page_number,
                index
            )

            if validate_record(record):

                records.append(
                    record
                )

        except Exception as e:

            print(
                f"Error extracting card "
                f"{index}: {e}"
            )

    print(
        f"Valid records extracted: "
        f"{len(records)}"
    )

    return records


# ============================================================
# SAVE TEMPORARY JSON
# ============================================================

def save_json(records):

    with open(
        TEMP_JSON,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            records,
            file,
            indent=4,
            ensure_ascii=False
        )

    print(
        f"Progress saved to {TEMP_JSON}"
    )


# ============================================================
# REMOVE DUPLICATES
# ============================================================

def remove_duplicates(records):

    print(
        "\nRemoving duplicate records..."
    )

    unique_records = []

    seen = set()

    for record in records:

        details_url = (
            record.get(
                "details_url",
                ""
            ).strip()
        )

        # ----------------------------------------------------
        # If URL exists, use it
        # ----------------------------------------------------

        if details_url:

            key = (
                "url",
                details_url
            )

        else:

            # ------------------------------------------------
            # Otherwise use COMPLETE CARD INFORMATION
            # ------------------------------------------------

            key = (
                "content",

                record.get(
                    "cruise_name",
                    ""
                ).strip(),

                record.get(
                    "ship",
                    ""
                ).strip(),

                record.get(
                    "departure_port",
                    ""
                ).strip(),

                record.get(
                    "destination",
                    ""
                ).strip(),

                record.get(
                    "duration",
                    ""
                ).strip(),

                record.get(
                    "departure_date",
                    ""
                ).strip(),

                record.get(
                    "return_date",
                    ""
                ).strip(),

                record.get(
                    "price",
                    ""
                ).strip(),

                record.get(
                    "number_of_dates",
                    ""
                ).strip(),

                # Important:
                # Full raw content makes the identity
                # much less likely to collide.
                normalize_text(
                    record.get(
                        "raw_card_text",
                        ""
                    )
                )
            )

        if key not in seen:

            seen.add(key)

            unique_records.append(
                record
            )

    print(
        f"Records before deduplication: "
        f"{len(records)}"
    )

    print(
        f"Records after deduplication: "
        f"{len(unique_records)}"
    )

    return unique_records


# ============================================================
# CLEAN TEXT
# ============================================================

def clean_text(value):

    if value is None:

        return ""

    return normalize_text(
        str(value)
    )


# ============================================================
# CLEAN ALL RECORDS
# ============================================================

def clean_records(records):

    cleaned = []

    for record in records:

        new_record = {}

        for key, value in record.items():

            new_record[key] = clean_text(
                value
            )

        # Make sure raw content exists
        if not new_record[
            "raw_card_text"
        ]:

            continue

        # Make sure location exists
        if not (
            new_record[
                "departure_port"
            ]
            or
            new_record[
                "destination"
            ]
        ):

            continue

        cleaned.append(
            new_record
        )

    # Remove duplicates
    cleaned = remove_duplicates(
        cleaned
    )

    return cleaned


# ============================================================
# SAVE FINAL CSV
# ============================================================

def save_csv(records):

    fieldnames = [

        "page_number",

        "card_number",

        "cruise_name",

        "ship",

        "departure_port",

        "destination",

        "duration",

        "departure_date",

        "return_date",

        "price",

        "number_of_dates",

        "details_url",

        "raw_card_text"
    ]

    with open(
        OUTPUT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        for record in records:

            writer.writerow(
                {
                    field: record.get(
                        field,
                        ""
                    )
                    for field in fieldnames
                }
            )

    print(
        f"\nCSV created: {OUTPUT_CSV}"
    )

    print(
        f"Total records: {len(records)}"
    )


# ============================================================
# CARD PREVIEW
# ============================================================

def print_card_preview(records):

    print("\n" + "=" * 60)
    print("CARD PREVIEW")
    print("=" * 60)

    for index, record in enumerate(
        records,
        start=1
    ):

        print(
            "\n" + "-" * 60
        )

        print(
            f"Card: {index}"
        )

        print(
            record.get(
                "raw_card_text",
                ""
            )
        )


# ============================================================
# SCRAPE ALL RESULT BATCHES
# ============================================================

def scrape_all_pages():

    all_records = []

    processed_signatures = set()

    for page_number in range(
        1,
        TARGET_PAGES + 1
    ):

        print(
            "\n" + "#" * 70
        )

        print(
            f"RESULT BATCH {page_number} "
            f"/ {TARGET_PAGES}"
        )

        print(
            "#" * 70
        )

        # ----------------------------------------------------
        # Get currently loaded cards
        # ----------------------------------------------------

        cards = get_unique_loaded_cards()

        print(
            f"Cards currently loaded: "
            f"{len(cards)}"
        )

        # ----------------------------------------------------
        # Create signatures for currently
        # loaded cards
        # ----------------------------------------------------

        current_signatures = []

        for card in cards:

            signature = get_card_signature(
                card
            )

            if signature[0]:

                current_signatures.append(
                    signature
                )

        # ----------------------------------------------------
        # Only scrape cards that we haven't
        # already processed
        # ----------------------------------------------------

        new_cards = []

        for card in cards:

            signature = get_card_signature(
                card
            )

            if signature not in processed_signatures:

                new_cards.append(
                    card
                )

                processed_signatures.add(
                    signature
                )

        print(
            f"New cards in this batch: "
            f"{len(new_cards)}"
        )

        # ----------------------------------------------------
        # Extract new cards
        # ----------------------------------------------------

        batch_records = []

        for index, card in enumerate(
            new_cards,
            start=1
        ):

            try:

                record = extract_cruise(
                    card,
                    page_number,
                    index
                )

                if validate_record(record):

                    batch_records.append(
                        record
                    )

            except Exception as e:

                print(
                    f"Error extracting card: "
                    f"{e}"
                )

        all_records.extend(
            batch_records
        )

        print(
            f"Total records collected: "
            f"{len(all_records)}"
        )

        # ----------------------------------------------------
        # Save progress
        # ----------------------------------------------------

        save_json(
            all_records
        )

        # ----------------------------------------------------
        # If no new cards were found,
        # try scrolling again
        # ----------------------------------------------------

        if len(new_cards) == 0:

            print(
                "No new cards found in "
                "this batch."
            )

        # ----------------------------------------------------
        # Scroll to bottom to trigger
        # additional infinite-scroll loading
        # ----------------------------------------------------

        old_count = len(
            get_product_cards()
        )

        driver.execute_script(
            """
            window.scrollTo({
                top: document.body.scrollHeight,
                behavior: 'smooth'
            });
            """
        )

        time.sleep(1)

        # Wait for possible new cards
        start = time.time()

        while time.time() - start < 8:

            new_count = len(
                get_product_cards()
            )

            if new_count > old_count:

                print(
                    f"More cards loaded: "
                    f"{old_count} -> {new_count}"
                )

                break

            time.sleep(0.2)

        # ----------------------------------------------------
        # Check whether new cards appeared
        # ----------------------------------------------------

        final_count = len(
            get_product_cards()
        )

        if final_count <= old_count:

            print(
                "\nNo additional cards "
                "were loaded."
            )

            # Do NOT immediately stop.
            # Scroll a little upward and back
            # down to trigger lazy loading.

            driver.execute_script(
                """
                window.scrollBy(0, -800);
                """
            )

            time.sleep(0.5)

            driver.execute_script(
                """
                window.scrollTo({
                    top: document.body.scrollHeight,
                    behavior: 'smooth'
                });
                """
            )

            time.sleep(1)

            retry_count = len(
                get_product_cards()
            )

            if retry_count <= old_count:

                print(
                    "No additional cards "
                    "after retry."
                )

                # At this point there really may
                # be no more cards.
                #
                # Stop instead of creating fake
                # pages.

                break

    return all_records


# ============================================================
# MAIN
# ============================================================

def main():

    open_disney()

    click_view_dates()

    records = scrape_all_pages()

    print_card_preview(
        records
    )

    print(
        "\n" + "=" * 60
    )

    print(
        "FINAL DATA CLEANING"
    )

    print(
        "=" * 60
    )

    final_records = clean_records(
        records
    )

    print(
        f"Final clean records: "
        f"{len(final_records)}"
    )

    save_csv(
        final_records
    )


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "\n" + "=" * 60
        )

        print(
            "SCRAPER ERROR"
        )

        print(
            "=" * 60
        )

        print(
            repr(e)
        )

        # Save screenshot for debugging
        try:

            driver.save_screenshot(
                "disney_error.png"
            )

            print(
                "Screenshot saved: "
                "disney_error.png"
            )

        except Exception:
            pass

        # Save current HTML
        try:

            with open(
                "disney_error.html",
                "w",
                encoding="utf-8"
            ) as file:

                file.write(
                    driver.page_source
                )

            print(
                "HTML saved: "
                "disney_error.html"
            )

        except Exception:
            pass

    finally:

        driver.quit()

        print(
            "\nBrowser closed."
        )