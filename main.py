import sqlite3
from pathlib import Path
from functools import lru_cache
from fastapi import FastAPI, Request, Query
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles


APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "index.sqlite"
templates = Jinja2Templates(directory="templates")

MIN_TERM_LENGTH = 3

app = FastAPI(title="Simple Search Engine")
app.mount("/static", StaticFiles(directory="static"), name="static")


# Search + LRU Cache Strategy
@lru_cache(maxsize=128)
def search_index_with_pagination(tokens_tuple: tuple, limit: int, offset: int):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(tokens_tuple))

    # Query for total count
    count_query = f"""
        SELECT COUNT(*) FROM (
            SELECT postings.file_id
            FROM postings
            JOIN terms ON terms.id = postings.term_id
            WHERE terms.term IN ({placeholders})
            GROUP BY postings.file_id
            HAVING COUNT(DISTINCT postings.term_id) = ?
        )
    """
    cur.execute(count_query, (*tokens_tuple, len(tokens_tuple)))
    total_count = cur.fetchone()[0]

    # Search Query
    search_query = f"""
        SELECT COALESCE(files.decoded_key, files.raw_key) AS url, SUM(postings.count) as total
        FROM postings
        JOIN terms ON terms.id = postings.term_id
        JOIN files ON files.id = postings.file_id
        WHERE terms.term IN ({placeholders})
        GROUP BY postings.file_id
        HAVING COUNT(DISTINCT postings.term_id) = ?
        ORDER BY total DESC 
        LIMIT ? OFFSET ?
    """
    cur.execute(search_query, (*tokens_tuple, len(tokens_tuple), limit, offset))
    rows = cur.fetchall()
    
    conn.close()
    results = [{"url": r[0], "count": r[1]} for r in rows]
    return results, total_count

# Endpoint

@app.get("/")
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})

@app.get("/api/search")
def api_search(q: str = Query(...), page: int = Query(1, ge=1)):
    per_page = 10  
    offset = (page - 1) * per_page
    
    tokens = sorted(list(set(t for t in q.casefold().split() if len(t) >= MIN_TERM_LENGTH)))
    if not tokens:
        return {"results": [], "total_pages": 0}

    results, total_count = search_index_with_pagination(tuple(tokens), per_page, offset)
    
    import math
    total_pages = math.ceil(total_count / per_page)

    return {
        "word": q,
        "page": page,
        "total_pages": total_pages,
        "results": results
    }