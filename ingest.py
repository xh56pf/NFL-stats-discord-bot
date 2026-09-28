import duckdb
import nflreadpy as nfl

def refresh_nfl_duckdb(season: int = 2026):
    print(f"[INGEST] Pulling {season} weekly player stats from nflreadpy...")
    # ---------------------------------------------------------
    # 1. Weekly Stats (Existing offensive aggregations)
    # ---------------------------------------------------------
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
    # ---------------------------------------------------------
    # 2. Rosters (Position classifications and identifiers)
    # ---------------------------------------------------------
    print(f"[INGEST] Fetching rosters for {season}...")
    roster_df = nfl.load_rosters([season])
    con.register("incoming_rosters", roster_df)
    con.execute("""
        CREATE OR REPLACE TABLE rosters AS
        SELECT 
            season,
            team,
            position,
            depth_chart_position,
            jersey_number,
            status,
            full_name,
            gsis_id,
            headshot_url
        FROM incoming_rosters
        WHERE gsis_id IS NOT NULL;
    """)
    roster_count = con.execute("SELECT count(*) FROM rosters").fetchone()[0]
    print(f"[INGEST] Saved rosters ({roster_count} rows).")

    # ---------------------------------------------------------
    # 3. Play-by-Play (Granular play events, down/distance & defense)
    # ---------------------------------------------------------
    print(f"[INGEST] Fetching play-by-play for {season}...")
    pbp_df = nfl.load_pbp([season])
    con.register("incoming_pbp", pbp_df)
    con.execute("""
        CREATE OR REPLACE TABLE play_by_play AS
        SELECT 
            play_id,
            game_id,
            season,
            week,
            posteam,
            defteam,
            yardline_100,
            quarter_seconds_remaining,
            qtr,
            down,
            ydstogo,
            play_type,
            yards_gained,
            shotgun,
            no_huddle,
            qb_dropback,
            pass,
            rush,
            pass_length,
            pass_location,
            air_yards,
            yards_after_catch,
            epa,
            wpa,
            passer_player_id,
            passer_player_name,
            receiver_player_id,
            receiver_player_name,
            rusher_player_id,
            rusher_player_name,
            qb_kneel,
            qb_spike,
            "desc" AS play_description
        FROM incoming_pbp;
    """)
    pbp_count = con.execute("SELECT count(*) FROM play_by_play").fetchone()[0]
    print(f"[INGEST] Saved play_by_play ({pbp_count} rows).")

    # ---------------------------------------------------------
    # 4. Schedules (Full season schedule & remaining matchups)
    # ---------------------------------------------------------
    print(f"[INGEST] Fetching schedule for {season}...")
    sched_df = nfl.load_schedules([season])
    con.register("incoming_schedules", sched_df)
    con.execute("""
        CREATE OR REPLACE TABLE schedules AS
        SELECT 
            game_id,
            season,
            game_type,
            week,
            gameday,
            weekday,
            gametime,
            away_team,
            home_team,
            away_score,
            home_score,
            result,
            spread_line,
            total_line
        FROM incoming_schedules;
    """)
    sched_count = con.execute("SELECT count(*) FROM schedules").fetchone()[0]
    print(f"[INGEST] Saved schedules ({sched_count} rows).")

    con.close()
    print("[INGEST] All 3 tables successfully ingested into nfl_stats.duckdb.")

if __name__ == "__main__":
    refresh_nfl_duckdb()