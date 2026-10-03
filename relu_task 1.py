"""
Relu hiring challenge - task 1: Disney Cruise Line scraper.

Opens disneycruise.disney.go.com/en-in, clicks "View Dates", scrolls until
all result pages are loaded and saves every cruise card to results.csv.

The cards don't show the ship or sailing ids, so I also listen to the
"available-products" API calls the page makes while scrolling and match
each card to its API entry.

Needs a visible Chrome window - the site blocks headless browsers.
"""

import csv
import json
import os
import re
import time
from collections import Counter

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


START_URL = "https://disneycruise.disney.go.com/en-in/"

OUTPUT_CSV = "results.csv"
TEMP_JSON = "disney_cruise_raw.json"
TEMP_CSV = "disney_cruise_raw.csv"
ANSWERS_JSON = "answers.json"

# the brief asks for at least 35 pages
TARGET_PAGES = 35

WAIT_TIME = 30
SCROLL_PAUSE = 1.0
MAX_STABLE_SCROLLS = 8   # scrolls with nothing new before giving up
MAX_SCROLLS = 400

CARD_SELECTOR = "dcl-product-card.dcl-infinite-scroll-child"

HOLIDAY_THEMES = [
    "Halloween on the High Seas",
    "Very Merrytime",
]

# shown as badges above the title, the title itself doesn't include them
CARD_TAGS = HOLIDAY_THEMES + [
    "Marvel Days at Sea",
    "Pixar Days at Sea",
    "2 Stops at Castaway Cay",
    "One-Way Cruise",
]

FINAL_COLUMNS = [
    "Page",
    "Card No",
    "Cruise Name",
    "Cruise Tags",
    "Offer",
    "Ship",
    "Duration",
    "Departing From",
    "Ending In",
    "Sailing To",
    "Destination Region",
    "Holiday Theme",
    "Price From (INR)",
    "Price From (USD)",
    "Guests",
    "Number of Dates",
    "Product ID",
    "Sailing IDs",
]


API_HOOK_JS = r"""
(function () {
    if (window.__dclHooked) return;
    window.__dclHooked = true;
    window.__dclPages = {};
    window.__dclMeta = {};

    const uniq = (arr) => Array.from(new Set(arr.filter((x) => x !== undefined && x !== null && x !== '')));

    function slimProduct(p) {
        const its = p.itineraries || [];
        const sailings = [].concat(...its.map((i) => i.sailings || []));
        const prices = its
            .map((i) => i.minimumPriceSummary && i.minimumPriceSummary.total)
            .filter((x) => typeof x === 'number')
            .sort((a, b) => a - b);
        return {
            productId: p.productId,
            productName: (p.productDisplayName || p.productName || '').trim(),
            ships: uniq(sailings.map((s) => s.ship && s.ship.name)),
            sailingIds: uniq(sailings.map((s) => s.sailingId)),
            nights: uniq(sailings.map((s) => s.numberOfNights)),
            regions: uniq(sailings.map((s) => s.destination)),
            portsOfCall: uniq([].concat(...its.map((i) => i.portsOfCall || []))),
            numberOfSailings: its.reduce((a, i) => a + (i.numberOfSailings || 0), 0),
            minPriceUSD: prices.length ? prices[0] : null,
        };
    }

    function record(url, bodyText, requestBody) {
        if (!url || String(url).indexOf('available-products') === -1) return;
        try {
            const data = typeof bodyText === 'string' ? JSON.parse(bodyText) : bodyText;
            const req = requestBody ? JSON.parse(requestBody) : {};
            const page = req.page || Object.keys(window.__dclPages).length + 1;
            window.__dclMeta = {
                totalPages: data.totalPages,
                totalAvailableCruises: data.totalAvailableCruises,
            };
            window.__dclPages[page] = (data.products || []).map(slimProduct);
        } catch (e) {}
    }

    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
        this.__dclUrl = url;
        return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (body) {
        this.addEventListener('load', () => {
            try {
                const res = (this.responseType === '' || this.responseType === 'text')
                    ? this.responseText : this.response;
                record(this.__dclUrl, res, typeof body === 'string' ? body : null);
            } catch (e) {}
        });
        return origSend.apply(this, arguments);
    };

    const origFetch = window.fetch;
    window.fetch = function (input, init) {
        return origFetch.apply(this, arguments).then((resp) => {
            try {
                const url = typeof input === 'string' ? input : input.url;
                if (url && url.indexOf('available-products') !== -1) {
                    resp.clone().text().then((t) => record(url, t, init && init.body));
                }
            } catch (e) {}
            return resp;
        });
    };
})();
"""


CARD_EXTRACT_JS = r"""
const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
return Array.from(document.querySelectorAll(arguments[0])).map((card, i) => {
    const text = (sel) => {
        const el = card.querySelector(sel);
        return el ? clean(el.innerText || el.textContent) : '';
    };
    return {
        dom_index: i,
        cruise_name: text('h2.product-card-content__name'),
        sailing_to: Array.from(card.querySelectorAll('dcl-port-list li'))
            .map((li) => clean(li.innerText || li.textContent)).filter(Boolean),
        price_text: text('.wrapper-price__pricing'),
        price_currency: text('.wrapper-price__currency'),
        guests: text('.total-guests-number__guests-label'),
        dates_button: text('.product-card-footer-wrapper__btn'),
        raw_card_text: (card.innerText || '').trim(),
    };
});
"""


def create_driver():

    options = Options()

    options.browser_version = "stable"

    # headless gets "Access Denied" from Akamai
    if os.environ.get("HEADLESS") == "1":
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=en-IN")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)

    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": API_HOOK_JS},
    )

    return driver


driver = create_driver()


def normalize_text(text):

    if not text:
        return ""

    return " ".join(str(text).split()).strip()


def print_step(title):

    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def get_product_cards():

    return driver.find_elements(By.CSS_SELECTOR, CARD_SELECTOR)


def get_api_state():

    return driver.execute_script(
        "return {pages: window.__dclPages || {}, meta: window.__dclMeta || {}};"
    )


def open_disney():

    print_step("STEP 1 - OPENING DISNEY CRUISE")

    driver.get(START_URL)

    WebDriverWait(driver, WAIT_TIME, poll_frequency=0.2).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    if "Access Denied" in driver.title:
        raise Exception(
            "Blocked by the site's bot protection (Access Denied). "
            "Run with a visible browser window."
        )

    print("Title:", driver.title)
    print("URL:", driver.current_url)

    accept_consent()


def accept_consent():

    selectors = [
        (By.ID, "onetrust-accept-btn-handler"),
        (By.XPATH, "//button[normalize-space(.)='Accept All' or normalize-space(.)='Accept' "
                   "or normalize-space(.)='I Agree' or normalize-space(.)='Agree']"),
    ]

    for by, selector in selectors:

        try:

            button = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable((by, selector))
            )
            driver.execute_script("arguments[0].click();", button)
            print("Consent banner accepted.")
            return

        except Exception:
            continue

    print("No consent banner shown.")


def click_view_dates():

    print_step("STEP 2 - CLICKING VIEW DATES")

    wait = WebDriverWait(driver, WAIT_TIME, poll_frequency=0.2)

    selectors = [
        (By.CSS_SELECTOR, "button.view-cruises-button"),
        (By.XPATH, "//button[contains(normalize-space(.), 'View Dates')]"),
        (By.XPATH, "//a[contains(normalize-space(.), 'View Dates')]"),
    ]

    view_dates = None

    for by, selector in selectors:

        try:
            view_dates = wait.until(EC.element_to_be_clickable((by, selector)))
            break

        except Exception:
            continue

    if view_dates is None:
        raise Exception("View Dates button could not be found.")

    print("View Dates found.")

    # default filters (2 adults, any date/destination/port) = every cruise
    print("Filters: default search (2 adults, all destinations / dates / ships)")

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", view_dates
    )
    time.sleep(0.5)
    driver.execute_script("arguments[0].click();", view_dates)

    print_step("STEP 3 - WAITING FOR CRUISE RESULTS")

    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, CARD_SELECTOR)))

    wait.until(lambda d: len(get_api_state()["pages"]) >= 1)

    state = get_api_state()

    print("Cruise results ready.")
    print("Current URL:", driver.current_url)
    print(f"Product cards currently visible: {len(get_product_cards())}")
    print(f"Result pages reported by site: {state['meta'].get('totalPages')}")
    print(f"Total sailings reported by site: {state['meta'].get('totalAvailableCruises')}")


def load_all_pages():

    print_step("STEP 4 - SCROLLING TO LOAD ALL RESULT PAGES")

    stable_scrolls = 0
    previous = (0, 0)

    for scroll_number in range(1, MAX_SCROLLS + 1):

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(SCROLL_PAUSE)

        state = get_api_state()
        pages_loaded = len(state["pages"])
        total_pages = state["meta"].get("totalPages") or TARGET_PAGES
        card_count = len(get_product_cards())
        products_loaded = sum(len(p) for p in state["pages"].values())

        current = (pages_loaded, card_count)

        if current != previous:
            print(
                f"Scroll {scroll_number}: pages {pages_loaded}/{total_pages}, "
                f"cards {card_count}"
            )
            stable_scrolls = 0
            previous = current
        else:
            stable_scrolls += 1

        if pages_loaded >= total_pages and card_count >= products_loaded:
            print("All result pages loaded.")
            break

        if stable_scrolls >= MAX_STABLE_SCROLLS:

            # scroll up a bit and back down, sometimes needed to trigger the next page
            driver.execute_script("window.scrollBy(0, -1200);")
            time.sleep(1)
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(3)

            if len(get_api_state()["pages"]) == pages_loaded:
                print("No more results are loading.")
                break

            stable_scrolls = 0

    state = get_api_state()
    pages_loaded = len(state["pages"])

    print(f"Result pages loaded: {pages_loaded}")
    print(f"Cruise cards loaded: {len(get_product_cards())}")

    if pages_loaded < TARGET_PAGES:
        print(
            f"WARNING: only {pages_loaded} pages loaded "
            f"(target {TARGET_PAGES})."
        )

    return state


def flatten_api_pages(state):

    products = []

    for page in sorted(state["pages"], key=lambda p: int(p)):
        for product in state["pages"][page]:
            product = dict(product)
            product["page"] = int(page)
            products.append(product)

    return products


def match_cards_to_products(cards, products):

    # cards come in the same order as the API pages, fall back to name matching if not
    names_match = len(cards) == len(products) and all(
        normalize_text(c["cruise_name"]).lower()
        == normalize_text(p["productName"]).lower()
        for c, p in zip(cards, products)
    )

    if names_match:
        print("Cards matched to API products by position.")
        return list(zip(cards, products))

    print("Card order differs from API order - matching by name.")

    unused = list(products)
    pairs = []

    for card in cards:

        name = normalize_text(card["cruise_name"]).lower()
        match = next(
            (p for p in unused if normalize_text(p["productName"]).lower() == name),
            None,
        )

        if match is not None:
            unused.remove(match)

        pairs.append((card, match or {}))

    return pairs


def parse_ports_from_name(cruise_name):

    # "4-Night ... from Fort Lauderdale ending in San Juan" -> ("Fort Lauderdale", "San Juan")
    match = re.search(r"\bfrom\s+(.+)$", cruise_name, flags=re.IGNORECASE)

    if not match:
        return "", ""

    after_from = match.group(1)

    parts = re.split(r"\s+ending(?:\s+in)?\s+", after_from, maxsplit=1, flags=re.IGNORECASE)

    departing = re.split(r"\s+with\s+", parts[0], flags=re.IGNORECASE)[0]
    ending = re.split(r"\s+with\s+", parts[1], flags=re.IGNORECASE)[0] if len(parts) > 1 else ""

    return normalize_text(departing), normalize_text(ending)


def extract_duration(cruise_name, api_nights):

    match = re.search(r"(\d+)\s*-?\s*Night", cruise_name, flags=re.IGNORECASE)

    if match:
        nights = int(match.group(1))
    elif api_nights:
        nights = int(api_nights[0])
    else:
        return ""

    return f"{nights} Nights"


def extract_tags_and_offer(raw_card_text, cruise_name):

    # text above the title = tags + offer badge, sometimes glued together
    # ("One-Way CruiseGuaranteed with Restrictions"), so cut out known tags first
    header = raw_card_text.split(cruise_name)[0] if cruise_name else ""

    header = re.sub(r"Disney Cruise Line will select.*?Learn More", " ", header, flags=re.S)

    tags = []

    for tag in CARD_TAGS:
        if tag.lower() in header.lower():
            tags.append(tag)
            header = re.sub(re.escape(tag), " ", header, flags=re.IGNORECASE)

    offer = normalize_text(header)

    return tags, offer


def extract_holiday_theme(tags, api_product_name):

    for theme in HOLIDAY_THEMES:
        if theme in tags or theme.lower() in api_product_name.lower():
            return theme

    return ""


def extract_number_of_dates(dates_button, api_sailings):

    match = re.search(r"Show\s+(\d+)\s+Dates?", dates_button, flags=re.IGNORECASE)

    if match:
        return int(match.group(1))

    return api_sailings or ""


def build_record(card, product, card_number):

    cruise_name = normalize_text(card["cruise_name"]) or product.get("productName", "")

    departing, ending = parse_ports_from_name(cruise_name)

    tags, offer = extract_tags_and_offer(card["raw_card_text"], card["cruise_name"])

    price_usd = product.get("minPriceUSD")

    return {
        "Page": product.get("page", ""),
        "Card No": card_number,
        "Cruise Name": cruise_name,
        "Cruise Tags": " | ".join(tags),
        "Offer": offer,
        "Ship": " | ".join(product.get("ships", [])),
        "Duration": extract_duration(cruise_name, product.get("nights", [])),
        "Departing From": departing,
        "Ending In": ending,
        "Sailing To": " | ".join(card["sailing_to"]),
        "Destination Region": " | ".join(r.title() for r in product.get("regions", [])),
        "Holiday Theme": extract_holiday_theme(tags, product.get("productName", "")),
        "Price From (INR)": normalize_text(card["price_text"]),
        "Price From (USD)": f"${price_usd:,.2f}" if isinstance(price_usd, (int, float)) else "",
        "Guests": card["guests"],
        "Number of Dates": extract_number_of_dates(
            card["dates_button"], product.get("numberOfSailings")
        ),
        "Product ID": product.get("productId", ""),
        "Sailing IDs": " | ".join(product.get("sailingIds", [])),
        "_ports_of_call_api": " | ".join(product.get("portsOfCall", [])),
        "_raw_card_text": card["raw_card_text"],
    }


def scrape_all_cards(state):

    print_step("STEP 4 - EXTRACTING CARD DATA")

    cards = driver.execute_script(CARD_EXTRACT_JS, CARD_SELECTOR)
    products = flatten_api_pages(state)

    print(f"Cards on page: {len(cards)}")
    print(f"Products from {len(state['pages'])} API pages: {len(products)}")

    pairs = match_cards_to_products(cards, products)

    records = [
        build_record(card, product, number)
        for number, (card, product) in enumerate(pairs, start=1)
    ]

    print(f"Records extracted: {len(records)}")

    return records


def save_raw(records):

    with open(TEMP_JSON, "w", encoding="utf-8") as file:
        json.dump(records, file, indent=2, ensure_ascii=False)

    fieldnames = list(records[0].keys()) if records else FINAL_COLUMNS

    with open(TEMP_CSV, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"Raw data saved to {TEMP_JSON} and {TEMP_CSV}")


def clean_records(records):

    print_step("STEP 5 - DATA CLEANING")

    cleaned = []
    dropped_no_location = 0

    for record in records:

        row = {
            key: normalize_text(value) if isinstance(value, str) else value
            for key, value in record.items()
        }

        if not row["Departing From"] or not row["Destination Region"]:
            dropped_no_location += 1
            continue

        if not row["Sailing To"]:
            # e.g. 3-Night Cruise from Singapore has no ports of call
            row["Sailing To"] = row["_ports_of_call_api"] or "Days at Sea (no ports of call)"

        if not row["Ending In"]:
            row["Ending In"] = row["Departing From"]

        if not row["Offer"]:
            row["Offer"] = "No Special Offer"

        if not row["Cruise Tags"]:
            row["Cruise Tags"] = "No Tags"

        if not row["Holiday Theme"]:
            row["Holiday Theme"] = "Not a Holiday Cruise"

        if any(row[col] in ("", None) for col in FINAL_COLUMNS):
            print(f"Dropped incomplete record: {row['Cruise Name']}")
            continue

        cleaned.append(row)

    print(f"Records before cleaning: {len(records)}")
    print(f"Dropped (missing location): {dropped_no_location}")

    cleaned = remove_duplicates(cleaned)

    for number, row in enumerate(cleaned, start=1):
        row["Card No"] = number

    print(f"Final clean records: {len(cleaned)}")

    return cleaned


def remove_duplicates(records):

    # product id / name alone isn't unique - the same route shows up once per ship,
    # and every card link is just "#"
    unique_records = []
    seen = set()

    for record in records:

        key = (
            record["Product ID"],
            record["Ship"],
            record["Sailing IDs"],
            record["Cruise Name"],
            record["Price From (INR)"],
        )

        if key in seen:
            continue

        seen.add(key)
        unique_records.append(record)

    print(f"Records before deduplication: {len(records)}")
    print(f"Records after deduplication: {len(unique_records)}")

    return unique_records


def save_csv(records):

    print_step("STEP 6 - SAVING FINAL CSV")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as file:

        writer = csv.DictWriter(file, fieldnames=FINAL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    print(f"CSV created: {OUTPUT_CSV}")
    print(f"Total records: {len(records)}")


def answer_questions(records, state):

    print_step("ANSWERS")

    def has(row, column, word):
        return word.lower() in str(row[column]).lower()

    # the site's destination filter calls it "Pacific Coast Cruises"
    pacific = [
        r for r in records
        if has(r, "Cruise Name", "Pacific")
        or has(r, "Destination Region", "Pacific")
        or has(r, "Sailing To", "Pacific")
    ]

    holiday = [r for r in records if r["Holiday Theme"] != "Not a Holiday Cruise"]
    holiday_by_theme = Counter(r["Holiday Theme"] for r in holiday)

    more_than_two_dates = [r for r in records if int(r["Number of Dates"]) > 2]

    miami = [r for r in records if has(r, "Departing From", "Miami")]
    london = [r for r in records if has(r, "Departing From", "London")]

    total_sailings = sum(int(r["Number of Dates"]) for r in records)

    answers = {
        "pacific_cruises": len(pacific),
        "total_cruises": len(records),
        "total_sailings": total_sailings,
        "site_reported_sailings": state["meta"].get("totalAvailableCruises"),
        "holiday_cruises": len(holiday),
        "holiday_by_theme": dict(holiday_by_theme),
        "cruises_with_more_than_2_dates": len(more_than_two_dates),
        "miami_departures": len(miami),
        "london_departures": len(london),
        "departure_ports": dict(Counter(r["Departing From"] for r in records).most_common()),
        "pages_loaded": len(state["pages"]),
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    print(f"(i)   Cruises with Pacific as destination : {answers['pacific_cruises']}")
    print(f"(ii)  Total cruises (cards)               : {answers['total_cruises']}"
          f"  [{total_sailings} individual sailing dates]")
    print(f"(iii) Holiday cruises                     : {answers['holiday_cruises']}"
          f"  {dict(holiday_by_theme)}")
    print(f"(iv)  Cruises with more than 2 dates      : {answers['cruises_with_more_than_2_dates']}")
    print(f"(v)   Departing from Miami                : {answers['miami_departures']}")
    print(f"      Departing from London               : {answers['london_departures']}")
    print("      Departure ports on the site         : "
          + ", ".join(f"{k} ({v})" for k, v in answers["departure_ports"].items()))

    with open(ANSWERS_JSON, "w", encoding="utf-8") as file:
        json.dump(answers, file, indent=2, ensure_ascii=False)

    print(f"\nAnswers saved to {ANSWERS_JSON}")

    return answers


def main():

    open_disney()

    click_view_dates()

    state = load_all_pages()

    records = scrape_all_cards(state)

    save_raw(records)

    final_records = clean_records(records)

    save_csv(final_records)

    answer_questions(final_records, state)


if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print_step("SCRAPER ERROR")
        print(repr(e))

        try:
            driver.save_screenshot("disney_error.png")
            print("Screenshot saved: disney_error.png")
        except Exception:
            pass

        try:
            with open("disney_error.html", "w", encoding="utf-8") as file:
                file.write(driver.page_source)
            print("HTML saved: disney_error.html")
        except Exception:
            pass

    finally:

        driver.quit()
        print("\nBrowser closed.")
