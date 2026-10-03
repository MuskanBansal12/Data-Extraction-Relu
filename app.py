"""
Small Flask app to browse the scraped data.

/             task 1 - Disney cruises (results.csv)
/ingredients  task 2 - Ingredients Network (task2/results.csv)

python app.py, then open http://127.0.0.1:5000
"""

import csv
import json
import os
from collections import Counter

from flask import Flask, abort, jsonify, render_template, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_CSV = os.path.join(BASE_DIR, "results.csv")
ANSWERS_JSON = os.path.join(BASE_DIR, "answers.json")

app = Flask(__name__)


def load_cruises():

    with open(RESULTS_CSV, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    for row in rows:
        row["Number of Dates"] = int(row["Number of Dates"])
        row["Page"] = int(row["Page"])
        row["Card No"] = int(row["Card No"])

    return rows


def load_answers(cruises):

    if os.path.exists(ANSWERS_JSON):
        with open(ANSWERS_JSON, encoding="utf-8") as file:
            return json.load(file)

    # answers.json missing -> work them out from the csv
    return {
        "total_cruises": len(cruises),
        "total_sailings": sum(c["Number of Dates"] for c in cruises),
        "pacific_cruises": sum("pacific" in c["Cruise Name"].lower() for c in cruises),
        "holiday_cruises": sum(c["Holiday Theme"] != "Not a Holiday Cruise" for c in cruises),
        "holiday_by_theme": {},
        "cruises_with_more_than_2_dates": sum(c["Number of Dates"] > 2 for c in cruises),
        "miami_departures": sum("miami" in c["Departing From"].lower() for c in cruises),
        "london_departures": sum("london" in c["Departing From"].lower() for c in cruises),
        "pages_loaded": max((c["Page"] for c in cruises), default=0),
    }


CRUISES = load_cruises()
ANSWERS = load_answers(CRUISES)
PORT_COUNTS = Counter(c["Departing From"] for c in CRUISES).most_common()


# task 2

TASK2_DIR = os.path.join(BASE_DIR, "task2")
TASK2_FILES = {"results.csv", "products.csv"}


def load_task2():

    results_csv = os.path.join(TASK2_DIR, "results.csv")
    answers_json = os.path.join(TASK2_DIR, "answers.json")

    if not os.path.exists(results_csv):
        return [], {}

    with open(results_csv, encoding="utf-8-sig", newline="") as file:
        companies = list(csv.DictReader(file))

    answers = {}
    if os.path.exists(answers_json):
        with open(answers_json, encoding="utf-8") as file:
            answers = json.load(file)

    return companies, answers


COMPANIES, TASK2_ANSWERS = load_task2()
COUNTRY_COUNTS = Counter(c["Country"] for c in COMPANIES).most_common(15)


@app.route("/")
def index():
    return render_template(
        "index.html",
        cruises=CRUISES,
        answers=ANSWERS,
        port_counts=PORT_COUNTS,
    )


@app.route("/api/cruises")
def api_cruises():
    return jsonify(CRUISES)


@app.route("/api/answers")
def api_answers():
    return jsonify(ANSWERS)


@app.route("/download/results.csv")
def download_csv():
    return send_file(RESULTS_CSV, as_attachment=True, download_name="results.csv")


@app.route("/ingredients")
def ingredients():
    if not COMPANIES:
        abort(404, "Run relu_task 2.py first to create task2/results.csv")
    return render_template(
        "ingredients.html",
        companies=COMPANIES,
        answers=TASK2_ANSWERS,
        country_counts=COUNTRY_COUNTS,
    )


@app.route("/api/companies")
def api_companies():
    return jsonify(COMPANIES)


@app.route("/download/task2/<name>")
def download_task2(name):
    if name not in TASK2_FILES:
        abort(404)
    return send_file(os.path.join(TASK2_DIR, name), as_attachment=True, download_name=name)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
