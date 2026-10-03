# Relu Hiring Challenge: Data Extraction Engineer

Two scrapers plus a small Flask app that shows their results.

## Files
| File | What it is |
|---|---|
| `relu_task 1.py` | Task 1: Disney Cruise Line scraper |
| `results.csv`, `answers.json` | Task 1 output |
| `disney_cruise_raw.json`, `disney_cruise_raw.csv` | Task 1 data before cleaning |
| `relu_task 2.py` | Task 2: Ingredients Network scraper |
| `task2/results.csv`, `task2/answers.json` | Task 2 output |
| `task2/companies_raw.csv` | Task 2 data before cleaning |
| `task2/products.csv` | Products from the same search |
| `app.py`, `templates/`, `static/` | Web app |
| `Relu_Challenge_Submission.docx` | Submission write-up |

## Running the scrapers
```bash
pip install -r requirements-scraper.txt
python "relu_task 1.py"
python "relu_task 2.py"
```
Both use Chrome through Selenium; Selenium downloads Chrome if it isn't installed. Task 1 needs a visible window because the Disney site blocks headless browsers, so it won't run on Colab or Replit. Run it on your own machine.

## Web app
```bash
pip install -r requirements.txt
python app.py        # http://127.0.0.1:5000
```
- `/` shows Task 1 (Disney cruises)
- `/ingredients` shows Task 2 (Ingredients Network)

### Deploying on Replit
1. Create a new Python Repl and upload these files and folders: `app.py`, `templates/`, `static/`, `results.csv`, `answers.json`, `task2/` (at least `results.csv`, `products.csv`, `answers.json`), `requirements.txt` and `.replit`.
2. Press **Run**. It installs Flask and gunicorn and starts the app on port 5000.
3. Open **Deploy** and choose Autoscale. The build and run commands are already in `.replit`.

You don't need to upload the scrapers to Replit; the app only reads the CSV and JSON files.
