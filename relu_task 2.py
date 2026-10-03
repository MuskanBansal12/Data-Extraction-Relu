"""
Relu hiring challenge - task 2: Ingredients Network scraper.

Opens ingredientsnetwork.com, clicks Search and collects every company from
the results, then visits each company page for the required fields (name,
description, sales markets, business activity, categories, events, address,
email, telephone, website). Products from the same search go to products.csv.

The results page builds its cards from one JSON index (search46json.jsp),
so instead of clicking "Show more results" a few hundred times I read that
index from inside the browser session. It has to be the browser session -
a cookie-less request to the same URL returns a different, bigger list
that the Search page never shows.
"""

import csv
import html
import json
import os
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE_URL = "https://www.ingredientsnetwork.com"
SITE_ID = 47

OUTPUT_DIR = "task2"
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "results.csv")
PRODUCTS_CSV = os.path.join(OUTPUT_DIR, "products.csv")
TEMP_CSV = os.path.join(OUTPUT_DIR, "companies_raw.csv")
CACHE_FILE = os.path.join(OUTPUT_DIR, "companies_cache.jsonl")
ANSWERS_JSON = os.path.join(OUTPUT_DIR, "answers.json")

WAIT_TIME = 30

# keep it gentle on the site
MAX_WORKERS = 6
REQUEST_DELAY = 0.15
MAX_RETRIES = 4

# SAMPLE=40 python "relu_task 2.py" for a quick test run
SAMPLE = int(os.environ.get("SAMPLE", "0"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0 Safari/537.36"
)

REQUIRED_COLUMNS = [
    "Company Name",
    "Company Description",
    "Sales Markets",
    "Primary Business Activity",
    "Categories",
    "Events",
    "Address",
    "Email",
    "Telephone",
    "Website",
]

# sidebar filter groups -> csv column
FACET_COLUMNS = {
    "Ingredients": "Ingredient Groups",
    "Finished Products": "Finished Product Groups",
    "Delivery Formats": "Delivery Formats",
    "Health & Wellness": "Health & Wellness",
    "Operations & Services": "Operations & Services",
    "Certifications": "Certifications",
}

FINAL_COLUMNS = (
    ["Company ID"]
    + REQUIRED_COLUMNS
    + ["Country"]
    + list(FACET_COLUMNS.values())
    + ["Profile URL"]
)


def normalize_text(text):

    if not text:
        return ""

    return " ".join(str(text).split()).strip()


def print_step(title):

    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def decode_cf_email(encoded):

    # cloudflare hides emails as hex xor'ed with the first byte
    try:
        key = int(encoded[:2], 16)
        return "".join(
            chr(int(encoded[i:i + 2], 16) ^ key)
            for i in range(2, len(encoded), 2)
        )
    except ValueError:
        return ""


def card_json_path(result):

    # same logic as the site's search js: 316951 -> /47/company/31/69/51/search316951_46.json
    record_id = str(result["id"]).rjust(6, "0")
    path = f"/{SITE_ID}/{result['type']}"

    while record_id:
        if len(record_id) > 6:
            path += "/" + record_id[:-4]
            record_id = record_id[-4:]
        else:
            path += "/" + record_id[:2]
            record_id = record_id[2:]

    path += f"/search{result['id']}"

    if result["type"] in ("company", "product") and result.get("eventid", -1) > 0:
        path += f"-{result['eventid']}"

    return path + "_46.json?v=21"


def create_driver():

    options = Options()
    options.browser_version = "stable"

    if os.environ.get("HEADLESS") == "1":
        options.add_argument("--headless=new")

    options.add_argument("--window-size=1600,1000")
    options.add_argument("--disable-notifications")

    return webdriver.Chrome(options=options)


driver = create_driver()


def open_site():

    print_step("STEP 1 - OPENING INGREDIENTS NETWORK")

    driver.get(BASE_URL + "/")

    WebDriverWait(driver, WAIT_TIME).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "form[action*='searchresults']"))
    )

    print("Title:", driver.title)

    accept_cookies()


def accept_cookies():

    # the consent banner is inside a shadow root
    time.sleep(2)

    clicked = driver.execute_script("""
        const hosts = [document, ...Array.from(document.querySelectorAll('*'))
            .filter(e => e.shadowRoot).map(e => e.shadowRoot)];
        for (const root of hosts) {
            const btn = Array.from(root.querySelectorAll('button'))
                .find(b => /accept all/i.test(b.textContent));
            if (btn) { btn.click(); return true; }
        }
        return false;
    """)

    print("Cookies accepted." if clicked else "No cookie banner shown.")


def click_search():

    print_step("STEP 2 - CLICKING SEARCH")

    button = WebDriverWait(driver, WAIT_TIME).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "form[action*='searchresults'] [type='submit']")
        )
    )

    driver.execute_script("arguments[0].click();", button)

    print_step("STEP 3 - WAITING FOR RESULT CARDS")

    WebDriverWait(driver, 60).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".docu-filter-results .result"))
    )

    cards = driver.find_elements(By.CSS_SELECTOR, ".docu-filter-results .result")
    print("Results page:", driver.current_url)
    print(f"Cards on first page: {len(cards)}")


def load_search_index():

    json_url = driver.find_element(
        By.CSS_SELECTOR, ".docu-filter-results"
    ).get_attribute("data-json-url") + "&types=all"

    driver.set_script_timeout(180)

    text = driver.execute_async_script(
        """
        const done = arguments[arguments.length - 1];
        fetch(arguments[0], {credentials: 'include'})
            .then(r => r.text()).then(done).catch(e => done('ERROR ' + e));
        """,
        json_url,
    )

    if text.startswith("ERROR"):
        raise Exception(f"Could not load search index: {text}")

    index = json.loads(text)

    print(f"Search index: {len(index['results'])} results")
    print("By type:", dict(Counter(r["type"] for r in index["results"])))

    return index


def build_facets(index):

    # every result has a filterVal bit string, bit N = matches filter N.
    # returns {facet title: [(value, name, level), ...]}
    facets = {}

    for facet in index["facets"]:

        title = html.unescape(facet["title"])
        entries = []

        for f in facet["filters"]:
            raw = f["name"]
            level = raw.count("&nbsp;") // 3
            entries.append((f["value"], html.unescape(raw.replace("&nbsp;", "")).strip(), level))

        facets[title] = entries

    return facets


def has_bit(result, value):

    return result["filterVal"][value] == "1"


def decode_facets(result, facets):

    decoded = {}

    for title, column in FACET_COLUMNS.items():

        names = [
            name for value, name, level in facets.get(title, [])
            # ingredients has 550+ sub filters, only keep the top level ones
            if has_bit(result, value) and (title != "Ingredients" or level == 0)
        ]

        decoded[column] = " | ".join(names)

    return decoded


BROWSER_COOKIES = []


def create_session():

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})

    for cookie in BROWSER_COOKIES:
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))

    return session


_local = threading.local()


def http_get(session_factory, url):

    if not hasattr(_local, "session"):
        _local.session = session_factory()

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            time.sleep(REQUEST_DELAY)
            response = _local.session.get(url, timeout=40)

            if response.status_code == 200:
                return response

            if response.status_code == 404:
                return None

        except requests.RequestException:
            pass

        time.sleep(2 * attempt)

    return None


def parse_company_page(page_html):

    soup = BeautifulSoup(page_html, "lxml")
    data = {}

    h1 = soup.find("h1")
    data["Company Name"] = normalize_text(h1.get_text()) if h1 else ""

    desc_heading = soup.find("h3", string=re.compile(r"Company description", re.I))
    description = ""
    if desc_heading:
        parts = []
        for sibling in desc_heading.find_next_siblings():
            if sibling.name in ("h2", "h3", "table"):
                break
            parts.append(sibling.get_text(" "))
        description = normalize_text(" ".join(parts))
    data["Company Description"] = description

    facts = {}
    table = soup.find("table", class_="quickfacts")
    if table:
        for row in table.find_all("tr"):
            th, td = row.find("th"), row.find("td")
            if th and td:
                facts[normalize_text(th.get_text()).rstrip(":").lower()] = td

    def fact(name):
        td = facts.get(name)
        return normalize_text(td.get_text(" ")) if td else ""

    data["Sales Markets"] = fact("sales markets")
    data["Primary Business Activity"] = fact("primary business activity")

    groups = []
    for block in soup.select(".company-categories .category"):
        main = block.find("a")
        subs = [normalize_text(a.get_text()) for a in block.select(".subcategories a")]
        if main:
            name = normalize_text(main.get_text())
            groups.append(f"{name}: {', '.join(subs)}" if subs else name)

    if not groups and "affiliated categories" in facts:
        groups = [normalize_text(a.get_text()) for a in facts["affiliated categories"].find_all("a", class_="subcategory")]

    data["Categories"] = " | ".join(groups)

    events = []
    for event in soup.select("div.event"):
        name = event.find("h3")
        if not name:
            continue
        pieces = [normalize_text(name.get_text())]
        for cls in ("event-dates", "event-stand"):
            el = event.find(class_=cls)
            if el:
                pieces.append(normalize_text(el.get_text()))
        events.append(" - ".join(pieces))
    data["Events"] = " | ".join(dict.fromkeys(events))

    info = soup.select_one(".company-information")
    address = email = phone = website = ""

    if info:
        addr = info.find("address")
        address = normalize_text(addr.get_text(" ")) if addr else ""

        cf = info.select_one("[data-cfemail]")
        if cf:
            email = decode_cf_email(cf["data-cfemail"])
        else:
            mail = info.select_one("a[href^='mailto:']")
            email = mail["href"][7:] if mail else ""

        tel = info.select_one("a[href^='tel:']")
        phone = normalize_text(tel.get_text()) if tel else ""

        web = info.select_one("a.webLink")
        website = web["href"].strip() if web and web.get("href") else ""

    # some pages only have these in the json-ld block
    ld = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in payload.get("@graph", [payload]) if isinstance(payload, dict) else []:
            if isinstance(node, dict) and node.get("@type") == "Organization":
                ld = node

    if not address and isinstance(ld.get("address"), dict):
        a = ld["address"]
        address = ", ".join(
            normalize_text(a.get(k)) for k in
            ("streetAddress", "postalCode", "addressLocality", "addressCountry") if a.get(k)
        )
    email = email or normalize_text(ld.get("email"))
    phone = phone or normalize_text(ld.get("telephone"))

    data["Address"] = address
    data["Email"] = email
    data["Telephone"] = phone
    data["Website"] = website

    return data


def scrape_company(result, facets, session_factory):

    record = {"Company ID": result["id"]}

    card_response = http_get(session_factory, BASE_URL + card_json_path(result))
    card = card_response.json().get("result", {}) if card_response else {}

    profile_url = card.get("companylink") or card.get("link") or ""
    record["Profile URL"] = profile_url
    record["Country"] = normalize_text(card.get("country"))

    page = {}
    if profile_url:
        page_response = http_get(session_factory, profile_url)
        if page_response is not None:
            page = parse_company_page(page_response.text)

    record["Company Name"] = page.get("Company Name") or normalize_text(card.get("title"))
    record["Company Description"] = page.get("Company Description") or normalize_text(
        re.sub(r"<!--.*?-->", "", card.get("fulldesc", ""))
    )
    record["Sales Markets"] = page.get("Sales Markets", "")
    record["Primary Business Activity"] = page.get("Primary Business Activity") or normalize_text(card.get("companyTypes"))
    record["Categories"] = page.get("Categories") or normalize_text(card.get("categories", "").replace("|", " | "))
    record["Events"] = page.get("Events", "")
    record["Address"] = page.get("Address", "")
    record["Email"] = page.get("Email", "")
    record["Telephone"] = page.get("Telephone") or normalize_text(card.get("phone"))
    record["Website"] = page.get("Website", "")

    record.update(decode_facets(result, facets))

    return record


def load_cache():

    cache = {}

    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as file:
            for line in file:
                try:
                    row = json.loads(line)
                    cache[row["Company ID"]] = row
                except (json.JSONDecodeError, KeyError):
                    continue

    return cache


def scrape_companies(index, facets):

    print_step("STEP 4 - EXTRACTING COMPANY DATA")

    companies = [r for r in index["results"] if r["type"] == "company"]

    if SAMPLE:
        companies = companies[:SAMPLE]

    # cache lets a crashed run continue where it stopped
    cache = load_cache()
    todo = [c for c in companies if c["id"] not in cache]

    print(f"Companies in index: {len(companies)}")
    print(f"Already cached: {len(companies) - len(todo)}, to fetch: {len(todo)}")

    lock = threading.Lock()
    done = 0
    started = time.time()

    with open(CACHE_FILE, "a", encoding="utf-8") as cache_file, \
            ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:

        futures = {pool.submit(scrape_company, c, facets, create_session): c for c in todo}

        for future in as_completed(futures):

            try:
                record = future.result()
            except Exception as e:
                print(f"Error on company {futures[future]['id']}: {e!r}")
                continue

            with lock:
                cache[record["Company ID"]] = record
                cache_file.write(json.dumps(record, ensure_ascii=False) + "\n")
                cache_file.flush()
                done += 1

                if done % 200 == 0 or done == len(todo):
                    rate = done / max(time.time() - started, 1)
                    print(f"  {done}/{len(todo)} companies ({rate:.1f}/s)")

    records = [cache[c["id"]] for c in companies if c["id"] in cache]

    print(f"Company records extracted: {len(records)}")

    return records


def scrape_products(index, facets):

    print_step("STEP 4b - EXTRACTING PRODUCT CARDS")

    products = [r for r in index["results"] if r["type"] == "product"]

    if SAMPLE:
        products = products[:SAMPLE]

    def fetch(result):
        response = http_get(create_session, BASE_URL + card_json_path(result))
        card = response.json().get("result", {}) if response else {}
        row = {
            "Product ID": result["id"],
            "Product Name": normalize_text(card.get("title")) or result["name"],
            "Supplier": normalize_text(card.get("supplier") or card.get("companyname")),
            "Description": normalize_text(re.sub(r"<!--.*?-->", "", card.get("fulldesc") or card.get("desc") or "")),
            "Country": normalize_text(card.get("country")),
            "Product URL": urljoin_site(card.get("url") or card.get("link") or ""),
        }
        row.update(decode_facets(result, facets))
        return row

    rows = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for n, row in enumerate(pool.map(fetch, products), start=1):
            rows.append(row)
            if n % 500 == 0:
                print(f"  {n}/{len(products)} products")

    seen, clean = set(), []
    for row in rows:
        key = row["Product ID"]
        if key in seen or not row["Product Name"]:
            continue
        seen.add(key)
        for k, v in row.items():
            if v == "":
                row[k] = "Not listed"
        clean.append(row)

    with open(PRODUCTS_CSV, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=list(clean[0].keys()))
        writer.writeheader()
        writer.writerows(clean)

    print(f"Products saved: {len(clean)} -> {PRODUCTS_CSV}")

    return clean


def urljoin_site(url):

    return url if url.startswith("http") or not url else BASE_URL + url


def save_raw(records):

    with open(TEMP_CSV, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FINAL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    print(f"Raw (pre-cleaning) data saved to {TEMP_CSV}")


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_records(records):

    print_step("STEP 5 - DATA CLEANING")

    cleaned = []
    missing = Counter()

    for record in records:

        row = {k: normalize_text(v) if isinstance(v, str) else v for k, v in record.items()}

        if row["Email"] and not EMAIL_RE.match(row["Email"]):
            row["Email"] = ""
        if row["Website"] and not row["Website"].lower().startswith("http"):
            row["Website"] = "https://" + row["Website"]

        # brief: required columns can't be empty, so incomplete companies are dropped
        empty = [col for col in REQUIRED_COLUMNS if not row.get(col)]
        if empty:
            missing.update(empty)
            continue

        for col in FACET_COLUMNS.values():
            if not row.get(col):
                row[col] = "None listed"
        if not row.get("Country"):
            row["Country"] = "Not listed"

        cleaned.append(row)

    print(f"Records before cleaning: {len(records)}")
    print(f"Dropped for a missing required field: {len(records) - len(cleaned)}")
    for col, n in missing.most_common():
        print(f"   missing {col}: {n}")

    cleaned = remove_duplicates(cleaned)

    print(f"Final clean records: {len(cleaned)}")

    return cleaned


def remove_duplicates(records):

    unique, seen_ids, seen_keys = [], set(), set()

    for record in records:

        key = (record["Company Name"].lower(), record["Website"].lower().rstrip("/"))

        if record["Company ID"] in seen_ids or key in seen_keys:
            continue

        seen_ids.add(record["Company ID"])
        seen_keys.add(key)
        unique.append(record)

    print(f"Records before deduplication: {len(records)}")
    print(f"Records after deduplication: {len(unique)}")

    return unique


def save_csv(records):

    print_step("STEP 6 - SAVING FINAL CSV")

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FINAL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)

    print(f"CSV created: {OUTPUT_CSV}")
    print(f"Total records: {len(records)}")


# counts work the same way as the site's filter sidebar
def answer_questions(index, facets, clean_records_list):

    print_step("ANSWERS")

    results = index["results"]

    def filter_value(facet, name):
        return next(v for v, n, _ in facets[facet] if n == name)

    def count(type_, values):
        return sum(
            1 for r in results
            if r["type"] == type_ and any(has_bit(r, v) for v in values)
        )

    ingredient_values = [v for v, _, _ in facets["Ingredients"]]
    finished_values = [v for v, _, _ in facets["Finished Products"]]

    herbs = filter_value("Ingredients", "Herbs, Spices")
    physical = filter_value("Delivery Formats", "Physical Formats")
    cognitive = filter_value("Health & Wellness", "Cognitive & Mental Health")

    clean_ids = {r["Company ID"] for r in clean_records_list}

    def count_clean(value):
        return sum(1 for r in results if r["type"] == "company" and r["id"] in clean_ids and has_bit(r, value))

    answers = {
        "total_ingredients": count("product", ingredient_values),
        "total_finished_products": count("product", finished_values),
        "companies_herbs_spices": count("company", [herbs]),
        "companies_physical_formats": count("company", [physical]),
        "companies_cognitive_mental_health": count("company", [cognitive]),
        "alternatives": {
            "all_products_listed": sum(r["type"] == "product" for r in results),
            "all_companies_listed": sum(r["type"] == "company" for r in results),
            "ingredient_categories_on_site": len(facets["Ingredients"]),
            "finished_product_categories_on_site": len(facets["Finished Products"]),
            "companies_tagged_any_ingredient": count("company", ingredient_values),
            "companies_tagged_any_finished_product": count("company", finished_values),
        },
        "in_clean_csv": {
            "companies": len(clean_records_list),
            "herbs_spices": count_clean(herbs),
            "physical_formats": count_clean(physical),
            "cognitive_mental_health": count_clean(cognitive),
        },
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    alt = answers["alternatives"]

    print(f"(i)   Total ingredients (ingredient products)      : {answers['total_ingredients']}")
    print(f"      [{alt['all_products_listed']} products listed in total, "
          f"{alt['ingredient_categories_on_site']} ingredient categories]")
    print(f"(ii)  Total finished products                      : {answers['total_finished_products']}")
    print(f"      [{alt['finished_product_categories_on_site']} finished-product categories]")
    print(f"(iii) Companies with Herbs, Spices                 : {answers['companies_herbs_spices']}")
    print(f"(iv)  Companies with Physical delivery formats     : {answers['companies_physical_formats']}")
    print(f"(v)   Companies in Cognitive & Mental Health       : {answers['companies_cognitive_mental_health']}")
    print(f"\nWithin the {len(clean_records_list)} complete records in results.csv: "
          f"herbs/spices {answers['in_clean_csv']['herbs_spices']}, "
          f"physical formats {answers['in_clean_csv']['physical_formats']}, "
          f"cognitive & mental health {answers['in_clean_csv']['cognitive_mental_health']}")

    with open(ANSWERS_JSON, "w", encoding="utf-8") as file:
        json.dump(answers, file, indent=2, ensure_ascii=False)

    print(f"\nAnswers saved to {ANSWERS_JSON}")

    return answers


def main():

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    open_site()

    click_search()

    index = load_search_index()

    facets = build_facets(index)

    # browser isn't needed for the rest, reuse its cookies with requests
    BROWSER_COOKIES.extend(driver.get_cookies())
    driver.quit()

    records = scrape_companies(index, facets)

    save_raw(records)

    final_records = clean_records(records)

    save_csv(final_records)

    scrape_products(index, facets)

    answer_questions(index, facets, final_records)


if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print_step("SCRAPER ERROR")
        print(repr(e))

        try:
            driver.save_screenshot("ingredients_error.png")
            with open("ingredients_error.html", "w", encoding="utf-8") as file:
                file.write(driver.page_source)
            print("Screenshot and HTML saved for debugging.")
        except Exception:
            pass

    finally:

        try:
            driver.quit()
        except Exception:
            pass

        print("\nDone.")
