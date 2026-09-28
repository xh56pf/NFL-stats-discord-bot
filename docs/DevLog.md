# General Notes, Thoughts, and Lessons Learned

## 9/28/26

Took the weekend off, ready to get into the dynamic heuristic engine, persistent long-term memory, and in memory visualization tools. Progress and objectives for these new features can be found at git project board page here: https://github.com/users/xh56pf/projects/1

The purpose of the dynamic heuristic engine (DHE) is so the bot can learn based on conversations with user and self correct as needed based on the interaction. For example, if the bot returns a result that is completely wrong, the user can request the bot to return the SQL it generated and justify each statement. The user in theory will then be able to correct any errors in the sql statements and explain their reasoning. This reasoning will then be written into a 'dynamic_rules.json' file by the bot itself to be referenced for future queries of the same or similar nature. 

Since the bot operates using stateless conversation with Gemini, token usage can grow exponentially with each query as previous conversations continue to be passed along. What could start as a 25 token prompt could result in a 1000+ token prompt as Gemini passes previous information such as the user playing in a PPR format scoring league, previously selected untradeable players on roster, favorite team, etc. For this reason, a persistent long-term memory must be implemented to hold this kind of information and will be stored in a relational database following an entry attribute value model (EAV). This is also known as an open schema similar to how NoSQL tables are formatted with key value pairs stored as dictionaries. One column will store the "note_type" and another will store the "note_value". 
For example: 

|user_id|player_name|note_type|note_value|created_at|
|:---|:---|:---|:---|:---|
|12345|NULL|scoring_format|PPR|2026-09-28 12:00:00|
|12345|NULL|favorite_team|MIA|2026-09-28 12:01:00|
|12345|Puka Nacua|keeper_target|Round 5|2026-09-28 12:05:00|
|12345|Bryce Young|trade_status|Untouchable|2026-09-28 12:10:00|

Purpose of this is to cut down on token utilization when processing queries. When facts are taught to the bot, such as "My league is a PPR (Points per Reception) Scoring format", the bot will know to automatically inject this information from the persistent long term memory table into future queries, completely bypassing the LLM even having to process it into the query. 

One issue I am noticing with this however is how the bot chooses what information to include with the query? I get including the fact table if I am asking about which wide receiver to start, but what if I am just asking something simple like which kicker to start? Kicker scoring has nothing to do with the PPR format rules, thus it is unnecessary to include any info about PPR at all. Seems like there is a risk for something called 'context-contamination' unless some steps are implemented to mitigate. Will explore this further. 

## 9/26/26

## 9/25/26: Added Roster and Play by Play tables    

Updated ingest.py to also extract Roster, Play by Play, and schedules tables from NFLReadR. Also updated run_sql query function in bot.py for associated new tables as well as system instructions. Bot now able to derive defensive performance calculations for queries such as "Which defense has given up the most targets to opposing wide receivers so far this season". Additionally, bot is able to make predictions of future performance based on schedule (i.e. "Which WR has the easiest strength of schedule moving forward based on defensive matchups") Set the instructions for which table to use as reference in system_instruction. Rosters table linked to play by play table using player gs ID and provides player's attributes such as position. This is still surprisingly fast, duckdb is pretty cool. 

One issue I am running into however is asking more complex questions, specifically: "!Hank2.0 based on which team's defenses give up the most fantasy points to wide receivers, which wide receivers should I be trying to trade for or start based on the easy strength of schedule for them?". Seems like it should be an easy one to answer since the bot can already tell me which teams have been giving up the most points to WR, so just find me players on teams that will be playing these porous defenses and rank them in order of whichever player has the most games against them... or am I taking crazy pills? Will check the console log in docker desktop to see just what kind of monstrous SQL statement gemini is producing to find this information. 

This is a good start for generic questions but I would like to be able to update instructions on the fly as I discover issues with queries. Seems too cumbersome to go back and manually update the system_instruction each time for each edge case, would be so much cooler to be able to have a conversation with the bot and have it self correct based on what I am saying. Looked into moving some instructions to a json file that the bot can edit on it's own, seems like it is called a dynamic heuristics loader where the dynamic rules are stored within the json file and refined/modified using a bot command like !edit in discord. 

## 09/24/26: Bot has no memory, trying to implement most efficient way to do this

### Problem:
During some testing regarding question related to analyzing the draft position of Jaylen Waddle, I realized the bot has no built in memory. This means any references I make to where I drafted Waddle in previous conversations is not saved and I will be forced to repeat these details anytime I want to perform analysis in the future (super annoying). This appears to be because of the stateless nature of Gemini API calls. 
Gemini does have a built in option utilizing the Google GenAI SDK with 'chats.create' which will allow me to maintain ongoing conversation history, however this solution will require utilizing more and more tokens as time goes on since the entire history of messages will only increase with use. 
- One solution is to create a sliding window history that only keeps the last 2-3 messages, but this doesn't seem like a good solution as things can randomly occur with time such as a player injury in a few weeks. 
- Another solution is to create a local database that stores these kinds of details in the form of 'user notes'. This seems like a much more viable solution as duckdb tables are small and can be indexed quickly. 
Will look into this further 

## 09/23/26

### Problem: 
While Hank2.0 is great at queries related to offensive players it falls short on anything related to defense, especially related to defensive performance vs a specific player. For example, if I want to query which defense gives up the most points in fantasy to Tight Ends, the result is N/A. 

### Solution: 
The plan is the implement play by play data from NFLReadR as well as player roster data. The play by play data is a breakdown of each play from each game in the season and contains much more situational data compared to the cumulative stats collected in the weekly stats table (consisting of the player's combined stats at the end of the week). 

## 09/22/26
Need to figure out how to create visualizations of queries. One option is integrating MatPlotLib/Seaborn and outputting images directly to Discord via bot. Also looking into Plotly, seems interesting. 

## 09/21/26: The Birth of Hank 2.0 

The official name of my discord bot is Hank 2.0 in honor of my friend and Fantasy Football co-manager Hank 1.0. Hank 2.0 is designed to be everything Hank 1.0, knowledgeable and analytical regarding the game of football and player statistics. 