import os
import json 
import re   
import io
import traceback
import asyncio
import duckdb
import discord
from discord import app_commands
from discord.ext import commands, tasks
from google import genai
from google.genai import types

# Import the ingestion function from ingest.py
try:
    import ingest
except ImportError:
    ingest = None

# Import the ingestion function from sleeper.py 
# (used for tracking injuries and up to the minute player statuses)
try: 
    import sleeper_sync
    print(f"[DEBUG] Sleeper module path: {sleeper_sync.__file__}")
    print(f"[DEBUG] Sleeper attributes: {dir(sleeper_sync)}")
except ImportError as e: 
    print(f"[ERROR] Failed to load sleeper module: {e}", flush=True)
    sleeper_sync = None

# Import dynamic rules file

RULES_FILE = "dynamic_rules.json"

# Queries file for saving user-defined queries to be used with API 
QUERIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "queries.json")

# ---------------------------------------------------------
# Environment & Client Initialization
# ---------------------------------------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_PATH = os.getenv("DUCKDB_PATH", "nfl_stats.duckdb")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

ai_client = genai.Client(api_key=GEMINI_API_KEY)

# ---------------------------------------------------------
# Dynamic Rules and Heuristic Utilities
# ---------------------------------------------------------

# Tracks the last query execution details per Discord channel
LAST_QUERY_CONTEXT = {}

def load_dynamic_rules() -> list[str]:
    """Reads learned heuristics from dynamic_rules.json."""
    if not os.path.exists(RULES_FILE):
        with open(RULES_FILE, "w") as f:
            json.dump({"rules": []}, f)
        return []
    try:
        with open(RULES_FILE, "r") as f:
            data = json.load(f)
            return data.get("rules", [])
    except Exception as e:
        print(f"[WARN] Failed to load dynamic rules: {e}", flush=True)
        return []

def save_dynamic_rule(new_rule: str):
    """Appends a newly synthesized rule to dynamic_rules.json."""
    rules = load_dynamic_rules()
    if new_rule not in rules:
        rules.append(new_rule)
        with open(RULES_FILE, "w") as f:
            json.dump({"rules": rules}, f, indent=2)

def build_system_instruction() -> str:
    learned_rules = load_dynamic_rules()
    rules_block = "\n".join([f"- {r}" for r in learned_rules]) if learned_rules else "- None yet."

    return (
        "You are Hank 2.0, an expert NFL statistics and fantasy football analyst with direct access "
        "to a local DuckDB database containing six tables: `weekly_stats`, `rosters`, `play_by_play`, `schedules`, `sleeper_trending`, and `player_injury_status`.\n\n"
        "Table Selection & Routing Guidelines:\n"
        "1. `weekly_stats`: Player season/weekly production, leaderboards, and fantasy totals (use `team` for the player's franchise, `player_id` for GSIS ID).\n"
        "2. `rosters`: Active rosters, depth charts, and player metadata mapped via `gsis_id` (serves as the primary `Dim_Player` dimension table).\n"
        "3. `play_by_play`: Situational event metrics, down-and-distance splits, EPA, and defensive points conceded (grouped by `defteam`). Contains role-specific IDs and names.\n"
        "4. `schedules`: Future matchups, remaining games, and schedule calendars.\n"
        "5. `sleeper_trending`: Real-time fantasy wire add volume and trending spikes across all Sleeper leagues.\n"
        "6. `player_injury_status`: Real-time active status, injury tags (Out, IR, Questionable), and body parts.\n\n"
        "Star Schema & Player ID Query Rules (CRITICAL FOR POWER BI & BOT PREVIEWS):\n"
        "- Dual-Field Selection Standard:\n"
        "  * Whenever writing queries that aggregate player-level stats across ANY table, ALWAYS select BOTH the player's canonical identifier (aliased as `player_id`) AND the readable display name (aliased as `player_name`).\n"
        "  * This provides clean human readability in Discord preview embeds while enabling seamless 1:* relationships with `rosters.gsis_id` in Power BI.\n\n"
        "- Table-Specific ID Column Mapping:\n"
        "  1. In `play_by_play` (Role-Specific):\n"
        "     * Passing/Receiving: `receiver_player_id AS player_id, receiver_player_name AS player_name`\n"
        "     * Rushing: `rusher_player_id AS player_id, rusher_player_name AS player_name`\n"
        "     * Passing (QB): `passer_player_id AS player_id, passer_player_name AS player_name`\n"
        "     * Always filter: `WHERE <role>_player_id IS NOT NULL`\n"
        "  2. In `weekly_stats` (Direct Player Level):\n"
        "     * Already indexed by player: select `player_id, player_display_name AS player_name` (or `player_name`).\n"
        "     * Always include both `player_id` and the name in `SELECT` and `GROUP BY`.\n\n"
        "- Cross-Table GSIS Keys:\n"
        "  * Core tables join directly: `rosters.gsis_id = weekly_stats.player_id`.\n"
        "Fantasy Waiver & Injury Analysis Rules:\n"
        "- Trending Targets & Handcuffs:\n"
        "  * Use `sleeper_trending` to identify surging wire targets and breakout backups based on `add_count`.\n"
        "- Return-to-Play & IR Cleared:\n"
        "  * To find players returning from injury, look for `status = 'Active'` and `injury_status IS NULL` in `player_injury_status`.\n"
        "- Cross-Source ID Resolution & Name Matching:\n"
        "  * When linking external tables (`sleeper_trending`, `player_injury_status`) to core tables (`rosters`, `weekly_stats`), join via `gsis_id`/`player_id` whenever available.\n"
        "  * If falling back to string joins, always normalize case and whitespace: `LOWER(TRIM(col))` or `ILIKE`.\n\n"
        "Rest-of-Season (ROS) & Matchup Analysis Rules:\n"
        "- Linking Players to Schedules:\n"
        "  * Individual players belong to a team via `rosters.team` (e.g., 'DAL', 'DET').\n"
        "  * To find upcoming games for players, join `rosters` to `schedules` matching either home or away:\n"
        "    JOIN schedules s ON (r.team = s.home_team OR r.team = s.away_team)\n"
        "- Determining the Opponent:\n"
        "  * For each scheduled matchup, identify the opponent using:\n"
        "    CASE WHEN s.home_team = r.team THEN s.away_team ELSE s.home_team END AS opponent_team\n"
        "- Filter Future Weeks:\n"
        "  * Always restrict schedule lookups to remaining games: `WHERE s.week > [current_completed_week] AND s.season = 2026`.\n"
        "- Strength of Schedule (SoS) / Matchup Difficulty:\n"
        "  1. Defensive Baseline: From `play_by_play` joined with `rosters`, compute average yards or fantasy points conceded by `defteam` against specific position groups (e.g., `r.position = 'WR'`).\n"
        "  2. Future Matchup Join: Join the player's upcoming `opponent_team` from `schedules` to the defensive baseline.\n"
        "  3. Ranking: Players/teams facing opponents with the highest average yards/points allowed have the 'easiest' remaining schedule.\n\n"
        "Relational Joins by Position in `play_by_play`:\n"
        "- Passing/Receiving: `JOIN rosters r ON p.receiver_player_id = r.gsis_id WHERE r.position = '<TARGET_POSITION>'`\n"
        "- Rushing: `JOIN rosters r ON p.rusher_player_id = r.gsis_id WHERE r.position = '<TARGET_POSITION>'`\n\n"
        "### Dynamic Heuristics & Execution Rules (MANDATORY TO FOLLOW):\n"
        f"{rules_block}\n\n"
        "Operational Directives:\n"
        "- If a tool call returns zero rows or an error, explain what happened instead of returning an empty response.\n"
        "- Format responses cleanly in Markdown with bold numbers, rankings, or tables."
        "- Visual Presentation & Discord Formatting:\n"
        "  * NEVER use `<br>` tags or HTML inside tables. Discord Markdown does not render HTML.\n"
        "  * In tabular outputs, EVERY player must have their OWN separate row. Do not group multiple players into one multi-line table row.\n"
        "  * If presenting in chat, format with proper Markdown tables, code blocks (` ``` `), or bullet points.\n"
    )

# ---------------------------------------------------------
# Database Tool for Gemini
# ---------------------------------------------------------
def run_sql_query(sql_query: str) -> list[dict]:
    """Execute a read-only DuckDB SQL query against NFL statistics.

    Available Tables:
    1. weekly_stats:
       - player_id, player_display_name, position, team, season, week
       - targets, receptions, receiving_yards, receiving_tds, target_share, air_yards_share
       - carries, rushing_yards, rushing_tds, fantasy_points, fantasy_points_ppr
       - completions, attempts, passing_yards, passing_tds, interceptions

    2. rosters:
       - season, team, position, depth_chart_position, jersey_number, status, full_name, gsis_id

    3. play_by_play:
       - play_id, game_id, season, week, posteam, defteam, yardline_100, down, ydstogo, play_type
       - yards_gained, shotgun, no_huddle, qb_dropback, pass, rush, air_yards, yards_after_catch
       - epa, wpa, passer_player_id, passer_player_name, receiver_player_id, receiver_player_name
       - rusher_player_id, rusher_player_name, qb_kneel, qb_spike, play_description
    
    4. schedule:
        - game_id, season, game_type, week, gameday, weekday, gametime
        -  away_team, home_team, away_score, home_score, result, spread_line, total_line
    
    5. sleeper_trending:
       - player_id, player_name, team, position, add_count, lookback_hours, pulled_at

    6. player_injury_status:
       - player_id, player_name, team, position, status, injury_status, injury_body_part
    """

    clean_query = sql_query.strip()
    print(f"\n[SQL GENERATED BY GEMINI]:\n{clean_query}\n", flush=True)
    if not clean_query.lower().startswith("select"):
        return [{"error": "Only SELECT queries are permitted."}]

    try:
        # Open in read-only mode so concurrent bot commands do not lock the DB
        con = duckdb.connect(DB_PATH, read_only=True)
        cursor = con.execute(clean_query)
        columns = [desc[0] for desc in cursor.description]
        raw_rows = cursor.fetchall()
        con.close()

        # Map SQL NULL directly to Python None instead of pandas float NaN
        results = [dict(zip(columns, row)) for row in raw_rows]

        # cache query 
        if CURRENT_ACTIVE_CHANNEL and CURRENT_ACTIVE_CHANNEL in LAST_QUERY_CONTEXT:
            LAST_QUERY_CONTEXT[CURRENT_ACTIVE_CHANNEL]["sql"] = clean_query
            LAST_QUERY_CONTEXT[CURRENT_ACTIVE_CHANNEL]["row_count"] = len(results)

        print(f"[SQL RESULTS COUNT]: {len(results)} rows returned", flush=True)

        if results:
            print(f"[SQL SAMPLE ROW]: {results[0]}", flush=True)
        return results[:50]  # Limit records to protect prompt context

    except Exception as e:
        print(f"[SQL ERROR]: {e}", flush=True)
        return [{"error": f"SQL execution error: {str(e)}"}]


# ---------------------------------------------------------
# Gemini Orchestration
# ---------------------------------------------------------
def ask_gemini_with_duckdb(channel_id: int, user_prompt: str) -> str:

    
   # ---------------------------------------------------------
   # Old system instructions, leaving here for reference and comparison with 
   # new dynamic method 
   # ---------------------------------------------------------
    """Synchronous worker that feeds the prompt and DuckDB tool to Gemini."""
    system_instruction=(
        "You are Hank 2.0, an expert NFL statistics and fantasy football analyst with direct access "
        "to a local DuckDB database containing three tables: `weekly_stats`, `rosters`, and `play_by_play`.\n\n"
        "Table Selection & Routing Guidelines:\n"
        "1. `weekly_stats`: Use for player season/weekly production, leaderboards, and fantasy totals (PPR/standard).\n"
        "2. `rosters`: Use for checking depth charts, active roster status, and looking up player `gsis_id`.\n"
        "3. `play_by_play`: Use for down-and-distance splits, EPA/efficiency, formations (shotgun), and ANY defensive matchups or performance against positions.\n\n"
        "4. `schedules`: Future matchups, remaining games, and schedule calendars.\n\n"
        "Rest-of-Season (ROS) & Matchup Analysis Rules:\n"
        "- Linking Players to Schedules:\n"
        "  * Individual players belong to a team via `rosters.team` (e.g., 'DAL', 'DET').\n"
        "  * To find upcoming games for players, join `rosters` to `schedules` matching either home or away:\n"
        "    JOIN schedules s ON (r.team = s.home_team OR r.team = s.away_team)\n"
        "- Determining the Opponent:\n"
        "  * For each scheduled matchup, identify the opponent using:\n"
        "    CASE WHEN s.home_team = r.team THEN s.away_team ELSE s.home_team END AS opponent_team\n"
        "- Filter Future Weeks:\n"
        "  * Always restrict schedule lookups to remaining games: `WHERE s.week > [current_completed_week] AND s.season = 2026`.\n"
        "- Strength of Schedule (SoS) / Matchup Difficulty:\n"
        "  1. Defensive Baseline: From `play_by_play` joined with `rosters`, compute average yards or fantasy points conceded by `defteam` against specific position groups (e.g., `r.position = 'WR'`).\n"
        "  2. Future Matchup Join: Join the player's upcoming `opponent_team` from `schedules` to the defensive baseline.\n"
        "  3. Ranking: Players/teams facing opponents with the highest average yards/points allowed have the 'easiest' remaining schedule.\n\n"
        "Relational Joins & SQL Rules:\n"
        "- `play_by_play` stores player IDs, not position abbreviations. To filter or aggregate plays by position (e.g., TE, WR, RB), join with `rosters` on `gsis_id`:\n"
        "    * Passing/Receiving: `JOIN rosters r ON p.receiver_player_id = r.gsis_id WHERE r.position = 'TE'`\n"
        "    * Rushing: `JOIN rosters r ON p.rusher_player_id = r.gsis_id WHERE r.position = 'RB'`\n"
        "- Defensive points/yardage conceded by position must aggregate `play_by_play.yards_gained` grouped by `defteam`.\n"
        "- Always filter out non-plays when calculating efficiency or EPA: `WHERE qb_kneel = 0 AND qb_spike = 0`.\n"
        "- Use `ILIKE '%Name%'` for player or team matching.\n"
        "- Format the final output clearly in Markdown using bold numbers, bullet points, or tables."   
    )
    

    # Track the active channel so run_sql_query knows which channel key to populate
    global CURRENT_ACTIVE_CHANNEL
    CURRENT_ACTIVE_CHANNEL = channel_id
    
    config= types.GenerateContentConfig(
        system_instruction=build_system_instruction(),
        tools=[run_sql_query],
        temperature=0.0
    )

    chat = ai_client.chats.create(
        model="gemini-3.5-flash-lite",
        config=config
    )

    # Automatic Function Calling (AFC) runs run_sql_query and returns the final synthesized answer
    response = chat.send_message(user_prompt)
    
    # Check if the model requested a function call
    if response.function_calls:
        for function_call in response.function_calls:
            name = function_call.name
            args = function_call.args
            
            if name == "run_sql_query":
                # Execute DuckDB query locally
                sql = args.get("sql_query")
                tool_result = run_sql_query(sql)
                
                # Send the database rows back to Gemini
                second_response = chat.send_message(
                    types.Part.from_function_response(
                        name=name,
                        response={"result": tool_result}
                    )
                )
                if second_response.text:
                    return second_response.text
    if response.text:
        return response.text

    # Candidate text fallback if response.text evaluates to None
    try:
        parts = response.candidates[0].content.parts
        text_parts = [p.text for p in parts if getattr(p, "text", None)]
        if text_parts:
            return "\n".join(text_parts)
    except Exception:
        pass

    return "No statistical summary could be generated."


# ---------------------------------------------------------
# Background Ingestion Task
# ---------------------------------------------------------
@tasks.loop(hours=24)
async def auto_refresh_db():
    """Runs once every 24 hours to check for and pull fresh nflreadpy data."""
    if ingest is None:
        print("[WARN] ingest.py not found; skipping scheduled DB refresh.", flush=True)
        return

    print("[INGEST-TASK] Running background database refresh...", flush=True)
    try:
        # Offload network download and DuckDB write to a separate thread
        await bot.loop.run_in_executor(None, ingest.refresh_nfl_duckdb)
        print("[INGEST-TASK] Background refresh finished successfully.", flush=True)
    except Exception as e:
        print(f"[INGEST-TASK ERROR] Failed to refresh database: {e}", flush=True)


# ---------------------------------------------------------
# Sleeper Ingestion Task
# ---------------------------------------------------------
@tasks.loop(minutes=20)
async def auto_refresh_sleeper():
    """Polls Sleeper for trending waiver adds and injury updates."""
    if sleeper_sync is None:
        return

    print("[SLEEPER-TASK] Refreshing trending wire data & injury status...", flush=True)
    try:
        await sleeper_sync.sync_sleeper_trending_to_duckdb(lookback_hours=4, limit=50)
        await sleeper_sync.sync_player_status_to_duckdb()
        print("[SLEEPER-TASK] Sleeper sync finished successfully.", flush=True)
    except Exception as e:
        print(f"[SLEEPER-TASK ERROR] Failed to sync Sleeper data: {e}", flush=True)

@auto_refresh_sleeper.before_loop
async def before_sleeper_loop():
    await bot.wait_until_ready()

# ---------------------------------------------------------
# Discord Command Handlers
# ---------------------------------------------------------
@bot.command(name="Hank2.0")
async def nfl_command(ctx: commands.Context, *, question: str):
    async with ctx.typing():
        try:
            # 1. Initialize channel context entry
            LAST_QUERY_CONTEXT[ctx.channel.id] = {
                "prompt": question,
                "sql": None,
                "row_count": 0
            }
            # Run the query in executor to prevent blocking the async event loop, include ctx.channel.id
            answer = await bot.loop.run_in_executor(
                None, 
                ask_gemini_with_duckdb, 
                ctx.channel.id,
                question
                )

            if not answer:
                answer = "No response generated."

            if len(answer) > 1950:
                file = discord.File(io.StringIO(answer), filename="nfl_stats.md")
                await ctx.reply("Response was too large for chat. Attached as file:", file=file)
            else:
                await ctx.reply(answer)
        except Exception as e:
            print("=== ERROR IN NFL COMMAND ===", flush=True)
            traceback.print_exc()
            print("============================", flush=True)
            await ctx.reply(f"**Error:** `{type(e).__name__}: {str(e)}`")


@bot.command(name="nflsync")
@commands.has_permissions(administrator=True)
async def manual_sync(ctx: commands.Context):
    """Admin command to force a manual database refresh from Discord."""
    if ingest is None:
        await ctx.reply("`ingest.py` module is not available in the bot container.")
        return

    await ctx.reply("Starting manual database sync with `nflreadpy`...")
    try:
        await bot.loop.run_in_executor(None, ingest.refresh_nfl_duckdb)
        await ctx.reply("✅ `nfl_stats.duckdb` successfully updated!")
    except Exception as e:
        await ctx.reply(f"❌ Sync failed: `{e}`")


# Commands for DES engine to explain and correct SQL statements 
@bot.command(name="explain")
async def explain_last_query(ctx: commands.Context):
    """Displays the SQL query and details from the last analysis."""
    context = LAST_QUERY_CONTEXT.get(ctx.channel.id)
    if not context or not context.get("sql"):
        await ctx.reply("No previous query execution found in this channel.")
        return

    async with ctx.typing():
        # Prompt Gemini to explain its own SQL architecture
        explanation_prompt = (
            f"A user asked this NFL analytics prompt: '{context['prompt']}'\n\n"
            f"The database executed this DuckDB SQL query:\n"
            f"```sql\n{context['sql']}\n```\n"
            "Provide a concise, step-by-step walkthrough of the relational logic:\n"
            "1. Explain each CTE or table join (why those tables and columns were matched).\n"
            "2. Explain key filters (weeks, game types, positions, play conditions).\n"
            "3. Explain the final aggregation or calculation used to reach the answer.\n"
            "Keep the explanation clear, technical, and formatted in Markdown bullet points."
        )

        try:
            # Quick one-shot generation without tools
            response = ai_client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=explanation_prompt
            )
            step_by_step = response.text.strip()
        except Exception as e:
            step_by_step = f"Could not generate natural language explanation: {e}"

    output = (
        f"**Original Prompt:** `{context['prompt']}`\n\n"
        f"**Generated SQL Query:**\n```sql\n{context['sql']}\n```\n"
        f"**Rows Returned:** {context['row_count']}"
        f"### Step-by Step Logic Breakdown:\n{step_by_step}"
    )
    if len(output) > 1950:
        file = discord.File(io.StringIO(output), filename="query_explanation.md")
        await ctx.reply("Query details attached:", file=file)
    else:
        await ctx.reply(output)


@bot.command(name="correct")
async def correct_last_query(ctx: commands.Context, *, user_critique: str):
    """Extracts a permanent SQL heuristic from your feedback and persists it."""
    context = LAST_QUERY_CONTEXT.get(ctx.channel.id)
    if not context:
        await ctx.reply("No previous query to correct. Ask an analytical question first!")
        return

    async with ctx.typing():
        # Meta-prompt to distill the user's critique into a strict rule
        meta_prompt = (
            f"You previously generated this SQL query for the prompt: '{context['prompt']}':\n"
            f"```sql\n{context['sql']}\n```\n"
            f"The user provided this feedback/correction:\n"
            f"'{user_critique}'\n\n"
            "Extract a concise, single-sentence operational rule for future SQL generation. "
            "Return ONLY the rule as raw text with no quotes, preamble, or markdown formatting."
        )

        response = ai_client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=meta_prompt
        )
        new_rule = response.text.strip()
        save_dynamic_rule(new_rule)

        await ctx.reply(
            f"✅ **Rule Learned & Saved to `dynamic_rules.json`:**\n"
            f"> `{new_rule}`\n\n"
            "This rule will be applied to all future queries automatically."
        )

### Command for saving query to queries.json for later retrieval
@bot.command(name="savequery")
@commands.has_permissions(administrator=True)
async def save_query(ctx: commands.Context, endpoint_name: str):
    endpoint_name = endpoint_name.lower()

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", endpoint_name):
        await ctx.reply("Use a name starting with a letter; letters, numbers, and underscores only.")
        return

    context = LAST_QUERY_CONTEXT.get(ctx.channel.id, {})
    sql = context.get("sql")
    if not sql:
        await ctx.reply("No successfully executed query found in this channel. Ask Hank a question first.")
        return

    saved_queries = {}
    if os.path.exists(QUERIES_FILE) and os.path.getsize(QUERIES_FILE) > 0:
        try:
            with open(QUERIES_FILE, "r", encoding="utf-8") as file:
                saved_queries = json.load(file)
        except json.JSONDecodeError:
            await ctx.reply("queries.json is not valid JSON; fix it before saving a query.")
            return

        if not isinstance(saved_queries, dict):
            await ctx.reply("queries.json must contain a JSON object mapping endpoint names to SQL.")
            return

    saved_queries[endpoint_name] = sql

    with open(QUERIES_FILE, "w", encoding="utf-8") as file:
        json.dump(saved_queries, file, indent=2)

    await ctx.reply(f"Saved. API URL: `/api/named/{endpoint_name}`")

### Pulls existing queries from queries.json file for review 
@bot.command(name="listqueries")
@commands.has_permissions(administrator=True)
async def list_saved_queries(ctx: commands.Context):
    if not os.path.exists(QUERIES_FILE) or os.path.getsize(QUERIES_FILE) == 0:
        await ctx.reply("No saved queries found.")
        return

    try:
        with open(QUERIES_FILE, "r", encoding="utf-8") as file:
            saved_queries = json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        await ctx.reply(f"Could not read queries.json: `{exc}`")
        return

    if not isinstance(saved_queries, dict) or not saved_queries:
        await ctx.reply("No saved queries found.")
        return

    entries = [
        f"{name}\nGET /api/named/{name}\n{sql}"
        for name, sql in saved_queries.items()
    ]
    output = "\n\n".join(entries)

    if len(output) > 1800:
        attachment = discord.File(
            io.StringIO(output),
            filename="saved_queries.txt",
        )
        await ctx.reply("Saved queries attached:", file=attachment)
    else:
        await ctx.reply(f"```text\n{output}\n```")

@bot.event
async def on_ready():
    print(f"[READY] Logged in as {bot.user.name} (ID: {bot.user.id})", flush=True)

    # 1. Check if core database exists; if not, trigger an initial ingestion
    if not os.path.exists(DB_PATH) and ingest is not None:
        print(f"[INIT] '{DB_PATH}' not found. Performing initial ingestion...", flush=True)
        await bot.loop.run_in_executor(None, ingest.refresh_nfl_duckdb)

    # 2. Trigger an immediate initial Sleeper sync so tables are available right away
    if sleeper_sync is not None:
        print("[INIT] Performing initial Sleeper wire & injury sync...", flush=True)
        try:
            await sleeper_sync.sync_sleeper_trending_to_duckdb(lookback_hours=4, limit=50)
            await sleeper_sync.sync_player_status_to_duckdb()
            print("[INIT] Initial Sleeper sync complete.", flush=True)
        except Exception as e:
            print(f"[INIT ERROR] Failed initial Sleeper sync: {e}", flush=True)

    # 3. Start background loops
    if not auto_refresh_db.is_running():
        auto_refresh_db.start()

    if sleeper_sync is not None and not auto_refresh_sleeper.is_running():
        auto_refresh_sleeper.start()


# ---------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------
if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise ValueError("Missing DISCORD_TOKEN environment variable.")
    if not GEMINI_API_KEY:
        raise ValueError("Missing GEMINI_API_KEY environment variable.")
    bot.run(DISCORD_TOKEN)