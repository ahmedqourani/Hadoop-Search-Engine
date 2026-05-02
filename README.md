# Hadoop-Search-Engine

Tiny search engine demo:

- `build_sqlite.py` converts Hadoop/MapReduce output (`part-*`) into a SQLite inverted index (`index.sqlite`).
- `main.py` serves a small UI (`templates/index.html`) and a JSON API (`/api/search`) that queries that index.

## Requirements

- Python 3.10+
- Packages: `fastapi`, `jinja2`, `uvicorn` (SQLite support comes from the Python stdlib: `sqlite3`)

Install:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install fastapi jinja2 "uvicorn[standard]"
```

## Create the DB (`index.sqlite`)

`main.py` expects an `index.sqlite` file in the repo root (same folder as `main.py`).

Input format per line:

```
<term>\t<file_key>:<count>[, <file_key>:<count>...]
```

Build from the included sample file (`part-00000`):

```bash
python build_sqlite.py --input part-00000 --output index.sqlite
```

Build from multiple Hadoop output files:

```bash
python build_sqlite.py --input "part-*" --output index.sqlite
```

## Run

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

