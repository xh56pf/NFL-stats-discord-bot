import argparse
import os 
import re
import duckdb
import requests
from pathlib import Path
import nflreadpy as nfl
from yfpy.query import YahooFantasySportsQuery

# ---------------------------------------------------------
# Yahoo League Configuration (Official OAuth API)
# ---------------------------------------------------------
YAHOO_LEAGUE_ID = os.getenv("YAHOO_LEAGUE_ID")
YAHOO_CLIENT_ID = os.getenv("YAHOO_CLIENT_ID")
YAHOO_CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET")



ENV_PATH = Path(".").resolve()

def get_yahoo_query() -> YahooFantasySportsQuery:
    
    
    return YahooFantasySportsQuery(
        league_id=str(YAHOO_LEAGUE_ID),
        game_code="nfl",
        yahoo_consumer_key=YAHOO_CLIENT_ID,
        yahoo_consumer_secret=YAHOO_CLIENT_SECRET,
        env_file_location=ENV_PATH,
        save_token_data_to_env_file=True,   # <--- THIS SAVES THE TOKEN TO YOUR .env
        env_var_fallback=True
    )


def sync_yahoo_free_agents(con: duckdb.DuckDBPyConnection, max_players: int = 100):
    """
    Fetches available offensive players (Free Agents and Waivers) directly via the Yahoo API
    and saves them to DuckDB.
    """
    if not YAHOO_LEAGUE_ID or not YAHOO_CLIENT_ID or not YAHOO_CLIENT_SECRET:
        print("[INGEST WARN] Yahoo credentials missing in .env. Skipping sync.")
        return

    print(f"[INGEST] Fetching available players via Yahoo API for League {YAHOO_LEAGUE_ID}...")

    try:
        yhandler = get_yahoo_query()
        league_key = yhandler.get_league_key()  # e.g., "449.l.123456"

        free_agents = []

        # Yahoo API paginates in batches of 25
        for offset in range(0, max_players, 25):
            # Endpoint for available offensive players sorted by actual rank
            url = f"league/{league_key}/players;status=A;position=O;sort=AR;start={offset};count=25"

            # yhandler.query handles the OAuth request and JSON extraction
            response_data = yhandler.query(url, ["fantasy_content", "league", "players"])
            
            if not response_data:
                break

            players_batch = response_data if isinstance(response_data, list) else [response_data]

            for item in players_batch:
                # yfpy returns Player model objects or dictionaries
                p = item if isinstance(item, dict) else (getattr(item, "_extracted_data", None) or item.__dict__)

                # Extract player attributes safely
                name_info = p.get("name", {})
                if isinstance(name_info, dict):
                    full_name = name_info.get("full")
                else:
                    full_name = getattr(name_info, "full", None) or p.get("full_name") or str(name_info)

                team = p.get("editorial_team_abbr") or p.get("editorial_team_key")
                pos = p.get("display_position") or p.get("primary_position")
                status = p.get("status") or "Available"
                player_id = str(p.get("player_id", ""))

                if full_name:
                    free_agents.append({
                        "yahoo_player_id": player_id,
                        "player_name": full_name.strip(),
                        "team": str(team).upper() if team else None,
                        "position": str(pos).upper() if pos else None,
                        "status": status
                    })

        if free_agents:
            con.execute("CREATE OR REPLACE TABLE yahoo_free_agents AS SELECT * FROM free_agents")
            fa_count = con.execute("SELECT count(*) FROM yahoo_free_agents").fetchone()[0]
            print(f"[INGEST] Successfully saved yahoo_free_agents ({fa_count} rows in DuckDB).")
        else:
            print("[INGEST WARN] Yahoo API returned 0 available players.")

    except Exception as e:
        print(f"[INGEST ERROR] Failed to sync Yahoo players via API: {e}")

# DB_PATH will use /app/nfl_stats.duckdb in Docker if set, or local nfl_stats.duckdb
DB_PATH = os.getenv("DUCKDB_PATH", "nfl_stats.duckdb")

def refresh_nfl_duckdb(season: int = 2026, skip_yahoo: bool = False):
    print(f"[INGEST] Connecting to {DB_PATH}...")
    con = duckdb.connect(DB_PATH)

    try:
        # ---------------------------------------------------------
        # 1. Weekly Stats (Existing offensive aggregations)
        # ---------------------------------------------------------
        print(f"[INGEST] Pulling {season} weekly player stats from nflreadpy...")
        df = nfl.load_player_stats([season], summary_level="week")
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
                headshot_url, 
                years_exp
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

        # ---------------------------------------------------------
        # 5. Yahoo Free Agents (Only run if not skipped)
        # ---------------------------------------------------------
        if not skip_yahoo:
            try:
                sync_yahoo_free_agents(con)
            except Exception as e:
                print(f"[INGEST WARN] Yahoo sync failed or was bypassed: {e}")
        else:
            print("[INGEST] Skipping Yahoo API/scrape sync (--skip-yahoo enabled).")

        print("[INGEST] All requested tables successfully ingested into DuckDB.")

    finally:
        con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest NFL & Fantasy Data into DuckDB")
    parser.add_argument("--season", type=int, default=2026, help="NFL season year")
    parser.add_argument(
        "--skip-yahoo",
        action="store_true",
        help="Skip Yahoo Fantasy API calls and sync nflreadpy only",
    )
    args = parser.parse_args()

    refresh_nfl_duckdb(season=args.season, skip_yahoo=args.skip_yahoo)