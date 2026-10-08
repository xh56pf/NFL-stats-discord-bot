import os
import asyncio
from datetime import datetime, timezone
import httpx
import duckdb
import pandas as pd

DUCKDB_PATH = os.getenv("DUCKDB_PATH", "nfl_stats.duckdb")
SLEEPER_ALL_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
SLEEPER_TRENDING_URL = "https://api.sleeper.app/v1/players/nfl/trending/add"

# In-memory cache for Sleeper's large (~5MB) master metadata dictionary
_player_cache = {}


async def fetch_sleeper_player_map() -> dict:
    """Fetches and caches the master Sleeper player dictionary."""
    global _player_cache
    if _player_cache:
        return _player_cache

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(SLEEPER_ALL_PLAYERS_URL)
            if resp.status_code == 200:
                _player_cache = resp.json()
    except Exception as e:
        print(f"[SLEEPER ERROR] Failed to fetch player metadata map: {e}", flush=True)

    return _player_cache


async def sync_player_status_to_duckdb(db_path: str = DUCKDB_PATH):
    """Fetches full player status, checks for returns to play, and updates player_injury_status."""
    print("[SLEEPER] Fetching player injury & active status...", flush=True)
    players = await fetch_sleeper_player_map()
    if not players:
        print("[SLEEPER ERROR] Player map empty; skipping status sync.", flush=True)
        return

    records = []
    for pid, p in players.items():
        if p.get("position") in ["QB", "RB", "WR", "TE"] and p.get("team"):
            name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            records.append({
                "player_id": str(pid),
                "player_name": name,
                "team": p.get("team"),
                "position": p.get("position"),
                "status": p.get("status"),              # Active, Inactive, IR
                "injury_status": p.get("injury_status"), # Questionable, Out, IR, None
                "injury_body_part": p.get("injury_body_part")
            })

    df = pd.DataFrame(records)
    con = duckdb.connect(db_path)
    try:
        con.register("incoming_status", df)

        table_exists = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'player_injury_status'"
        ).fetchone()[0]

        if table_exists:
            returns = con.execute("""
                SELECT 
                    curr.player_name, 
                    curr.position, 
                    curr.team,
                    prev.injury_status AS old_status,
                    curr.injury_status AS new_status
                FROM incoming_status curr
                JOIN player_injury_status prev ON curr.player_id = prev.player_id
                WHERE prev.injury_status IN ('Out', 'IR', 'PUP')
                  AND (curr.injury_status IS NULL OR curr.injury_status = 'Questionable')
                  AND curr.status = 'Active';
            """).fetchdf()

            if not returns.empty:
                print(f"[STATUS] Detected {len(returns)} player(s) returning to play!", flush=True)

        con.execute("CREATE OR REPLACE TABLE player_injury_status AS SELECT * FROM incoming_status;")
        total_rows = con.execute("SELECT count(*) FROM player_injury_status").fetchone()[0]
        print(f"[SLEEPER] Updated player_injury_status ({total_rows} active players).", flush=True)
    finally:
        con.close()


async def sync_sleeper_trending_to_duckdb(
    lookback_hours: int = 4, 
    limit: int = 50, 
    db_path: str = DUCKDB_PATH
):
    """Fetches trending waiver wire adds from Sleeper and saves to sleeper_trending."""
    print(f"[SLEEPER] Fetching top {limit} trending adds (last {lookback_hours}h)...", flush=True)
    params = {"lookback_hours": lookback_hours, "limit": limit}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(SLEEPER_TRENDING_URL, params=params)
            if resp.status_code != 200:
                print(f"[SLEEPER ERROR] Trending endpoint returned HTTP {resp.status_code}", flush=True)
                return
            trending_raw = resp.json()
    except Exception as e:
        print(f"[SLEEPER ERROR] Failed to fetch trending data: {e}", flush=True)
        return

    player_map = await fetch_sleeper_player_map()
    now_utc = datetime.now(timezone.utc)
    records = []

    for item in trending_raw:
        pid = str(item.get("player_id"))
        count = int(item.get("count", 0))
        meta = player_map.get(pid, {})
        name = meta.get("full_name") or f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip()

        if name:
            records.append({
                "player_id": pid,
                "player_name": name,
                "team": meta.get("team"),
                "position": meta.get("position"),
                "add_count": count,
                "lookback_hours": lookback_hours,
                "pulled_at": now_utc
            })

    if not records:
        print("[SLEEPER] No trending records returned.", flush=True)
        return

    df_trending = pd.DataFrame(records)
    con = duckdb.connect(db_path)
    try:
        con.register("incoming_trending", df_trending)
        con.execute("CREATE OR REPLACE TABLE sleeper_trending AS SELECT * FROM incoming_trending;")
        total_rows = con.execute("SELECT count(*) FROM sleeper_trending").fetchone()[0]
        print(f"[SLEEPER] Updated sleeper_trending ({total_rows} players).", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    async def main():
        await sync_player_status_to_duckdb()
        await sync_sleeper_trending_to_duckdb()

    asyncio.run(main())