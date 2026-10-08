import os 
import re
import duckdb
import requests
from bs4 import BeautifulSoup 
import nflreadpy as nfl

# ---------------------------------------------------------
# Yahoo League Configuration
# ---------------------------------------------------------
YAHOO_LEAGUE_ID = os.getenv("YAHOO_LEAGUE_ID")
YAHOO_COOKIE_STRING = os.getenv("YAHOO_COOKIE_STRING", "")

def sync_yahoo_free_agents(con: duckdb.DuckDBPyConnection, max_players: int = 100):
    """Scrapes available free agents from your private Yahoo league."""
    if not YAHOO_LEAGUE_ID or not YAHOO_COOKIE_STRING:
        print("[INGEST WARN] YAHOO_LEAGUE_ID or YAHOO_COOKIE_STRING missing. Skipping scrape.")
        return

    print(f"[INGEST] Scraping Yahoo free agents for League {YAHOO_LEAGUE_ID}...")
    
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": YAHOO_COOKIE_STRING,
    }

    free_agents = []

    try:
        for offset in range(0, max_players, 25):
            url = f"https://football.fantasysports.yahoo.com/f1/{YAHOO_LEAGUE_ID}/players?status=A&pos=O&sort=AR&count={offset}"
            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                print(f"[INGEST] Yahoo request returned status {resp.status_code}. Stopping pagination.")
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Find all rows within any table on the page
            rows = soup.select("table tbody tr")
            if not rows:
                rows = soup.find_all("tr")

            batch_count = 0
            for row in rows:
                # Target the player profile hyperlink
                name_link = row.select_one('a[href*="/nfl/players/"]')
                if not name_link:
                    continue

                player_name = name_link.text.strip()
                if not player_name:
                    continue

                # Locate player metadata (Team and Position)
                team, pos = None, None
                sub_meta = row.select_one(".F-sub, .ysf-player-name span, .detail-wrapper")
                
                if sub_meta:
                    raw_text = sub_meta.text.strip()
                    # Expecting format like "KC - RB" or "SF - WR,RB"
                    match = re.search(r"([A-Za-z]+)\s*-\s*([A-Za-z,]+)", raw_text)
                    if match:
                        team = match.group(1).strip()
                        pos = match.group(2).strip()
                    else:
                        parts = raw_text.split(" - ")
                        team = parts[0].strip() if len(parts) > 0 else None
                        pos = parts[1].strip() if len(parts) > 1 else None

                free_agents.append({
                    "player_name": player_name,
                    "team": team,
                    "position": pos,
                    "status": "Free Agent"
                })
                batch_count += 1

            if batch_count == 0:
                # If offset=0 returned no rows, we can stop early
                break

        if free_agents:
            con.execute("CREATE OR REPLACE TABLE yahoo_free_agents AS SELECT * FROM free_agents")
            fa_count = con.execute("SELECT count(*) FROM yahoo_free_agents").fetchone()[0]
            print(f"[INGEST] Saved yahoo_free_agents ({fa_count} rows).")
        else:
            print("[INGEST WARN] No Yahoo players found. Check YAHOO_LEAGUE_ID and YAHOO_COOKIE_STRING.")

    except Exception as e:
        print(f"[INGEST ERROR] Yahoo free agent scraper encountered an issue: {e}")

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

    # 5. Yahoo Free Agents (Scraped separately)
    sync_yahoo_free_agents(con)


    con.close()
    print("[INGEST] All tables successfully ingested into nfl_stats.duckdb.")

if __name__ == "__main__":
    refresh_nfl_duckdb()