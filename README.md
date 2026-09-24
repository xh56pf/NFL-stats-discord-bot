# 🏈 Football Intelligence: Autonomous NFL Analytics & Assistant

An autonomous, NFL analytics engine connected directly to Discord. The system uses **Google Gemini** for reasoning and SQL synthesis, combined with an embedded **DuckDB** analytical engine for high-speed local queries on official `nflreadr` statistical data. A primary design objective of this system is to experiment with **extreme token efficiency** - minimizing prompt bloat and API overhead to operate sustainably within strict quota tiers and reduce environmental compute impact. 

This project began as a method of compiling derived statistics such as receiver target share without manually searching and performing the calculations via Yahoo Fantasy's web interface or spinning up a Jupyter Notebook and inputting python code. 

Weekly NFL player performance statistics are collected from NFLReadPy and stored in a local DuckDB. 

For an iterative breakdown of engineering hurdles, trade-offs, and design notes, see the [Devlog](docs/LEARNINGS.md).
---

## Architecture Overview

The system decouples batch ingestion from user-facing query interfaces to ensure high availability and prevent concurrency deadlocks:

```text
       [ Discord User ]
              │
              ▼
    [ Discord Gateway / bot.py ]
              │
              ▼
   [ Google Gemini (LLM) ] ── (Synthesizes SQL & Function Calls)
              │
              ▼
    [ DuckDB Query Tool ] ── (Executes in <15ms, Read-Only)
              │
      [ nfl_stats.duckdb ]
              ▲
              │ (Scheduled Batch ETL)
       [ ingest.py ] ◀── [ nflreadpy / NFL Official Feeds ]
```

## Key Engineering Decisions
- Analytical Speed via DuckDB: Early prototypes of this project intended to leverage the capabilities of prebuilt Sports Stat MCP's readily available on Github as the data sources. However these solutions proved to be cumbersome and increased processing time for queries due to network overhead and remote latency. By storing the data used for analytics in a local database and utilizing DuckDB in particular, OLAP queries can be executed in under 20 ms. 

- Concurrency: DuckDB enforces single-writer semantics. Batch data ingestion utilizing ingest.py runs independently from client facing tools in bot.py, ensuring end user queries are never blocked behind data download (Query tools operate exclusively with 'read_only=True'). 

- Future In-Memory Visualizations Implementation: As an added bonus, data charts can be potentially plotted headlessly using Python tools such as MatPlotLib and Seaborn and passed directly to Discord through memory buffers, avoiding disk I/O and temporary file clutter. 

## Database Schema

Currently this project is utilizing weekly updates of the Player Stats table from NFLReadR exclusively, but future iterations will include extended roster and play by play data. 

More information regarding the schema of this table can be found [here](https://nflreadr.nflverse.com/articles/dictionary_player_stats.html)

## Getting Started

Prerequisites
Python 3.11+ or Docker & Docker Compose

A Discord Bot Token

A Google Gemini API Key

1. Clone & Setup Environment
```bash
Bash
git clone [https://github.com/your-username/nfl-analytics-bot.git](https://github.com/your-username/nfl-analytics-bot.git)
cd nfl-analytics-bot
```

Create a .env file in the root directory:
```bash
Code snippet
DISCORD_BOT_TOKEN="your_discord_bot_token_here"
GEMINI_API_KEY="your_gemini_api_key_here"
```

2. Run via Docker (Recommended)
```bash
Bash
# Build and run the bot container
docker compose up -d --build
```

# Run the initial data ingestion
```bash
docker compose exec discord-bot python ingest.py
```

3. Local Installation (Alternative)
```bash
Bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Ingest data into DuckDB
python ingest.py

# Launch the Discord client
python bot.py
```

## Example Queries

- "How did Rashid Shaheed's target share compare to all Seahawks wide receivers in week 2?"

- "Give me a breakdown of which NFL teams have given up the most touchdowns in the past 2 weeks?"

- "Which running backs have experienced the greatest increase in carries between weeks 1 to 3?"


