import duckdb
import nflreadpy as nfl

def refresh_nfl_duckdb(season: int = 2026):
    print(f"[INGEST] Pulling {season} weekly player stats from nflreadpy...")
    
    # 1. Fetch latest data
    df = nfl.load_player_stats([season], summary_level="week")
    
    # 2. Connect to DuckDB and update the table
    con = duckdb.connect("nfl_stats.duckdb")
    
    # Register DataFrame as a virtual view and overwrite table atomically
    con.register("incoming_stats", df)
    con.execute("""
        CREATE OR REPLACE TABLE weekly_stats AS 
        SELECT * FROM incoming_stats;
    """)
    
    row_count = con.execute("SELECT count(*) FROM weekly_stats").fetchone()[0]
    print(f"[INGEST] Successfully refreshed weekly_stats ({row_count} rows in DuckDB).")
    con.close()

if __name__ == "__main__":
    refresh_nfl_duckdb()