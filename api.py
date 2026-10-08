import os
import json
import duckdb
from typing import Optional
from fastapi import FastAPI, HTTPException, Query

DB_PATH = os.getenv("DUCKDB_PATH", "/app/nfl_stats.duckdb")
QUERIES_FILE = "/app/queries.json"
app = FastAPI(title="Hank 2.0 BI API")

def query_duckdb(query: str) -> list[dict]:
    """Helper to query DuckDB in read-only mode and return JSON-serializable records."""
    try:
        con = duckdb.connect(DB_PATH, read_only=True, config={"access_mode": "READ_ONLY"})
        cursor = con.execute(query)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        con.close()
        # Converts SQL NULLs to Python None (valid JSON null)
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trending")
def get_trending():
    """Returns Sleeper waiver-wire trending adds."""
    return query_duckdb("SELECT * FROM sleeper_trending ORDER BY add_count DESC;")

@app.get("/api/injuries")
def get_injuries():
    """Returns active player injury statuses."""
    return query_duckdb("SELECT * FROM player_injury_status WHERE injury_status IS NOT NULL;")

@app.get("/api/weekly_stats")
def get_weekly_stats():
    """Returns weekly player stats."""
    return query_duckdb("SELECT * FROM weekly_stats;")

@app.get("/api/rosters")
def get_rosters(team: Optional[str] = None):
    """
    Returns player rosters. 
    Optional filter: /api/rosters?team=KC
    """
    if team:
        sql = f"SELECT * FROM rosters WHERE team = '{team.upper()}';"
    else:
        sql = "SELECT * FROM rosters;"
    return query_duckdb(sql)

@app.get("/api/play_by_play/recent")
def get_recent_play_by_play(
    week: Optional[int] = None, 
    team: Optional[str] = None, 
    limit: int = Query(default=1000, le=5000)
):
    """
    Returns filtered raw plays (paged/limited) to prevent massive JSON payloads.
    Example: /api/play_by_play/recent?week=5&team=DET&limit=500
    """
    conditions = []
    if week:
        conditions.append(f"week = {week}")
    if team:
        conditions.append(f"(posteam = '{team.upper()}' OR defteam = '{team.upper()}')")
        
    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM play_by_play {where_clause} LIMIT {limit};"
    return query_duckdb(sql)


@app.get("/api/query")
def run_adhoc_query(sql: str = Query(..., description="The raw SQL query to run")):
    """Executes arbitrary SQL passed as a query string parameter."""
    return query_duckdb(sql)

@app.get("/api/debug/pbp_columns")
def get_pbp_columns():
    return query_duckdb("DESCRIBE play_by_play;")

### Dynamic Dispatch for Endpoint Queries 

@app.get("/api/named/{endpoint_name}")
def get_named_query(endpoint_name: str):
    """Executes a validated query registered in queries.json."""
    if not os.path.exists(QUERIES_FILE):
        raise HTTPException(status_code=404, detail="No queries registered yet.")
    
    with open(QUERIES_FILE, "r") as f:
        saved_queries = json.load(f)

    if endpoint_name not in saved_queries:
        raise HTTPException(status_code=404, detail=f"Query '{endpoint_name}' not found. Available: {list(saved_queries.keys())}")

    return query_duckdb(saved_queries[endpoint_name])