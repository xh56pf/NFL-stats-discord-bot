# General Notes, Thoughts, and Lessons Learned


## 09/24/26: Bot has no memory, trying to implement most efficient way to do this

### Problem:
During some testing regarding question related to analyzing the draft position of Jaylen Waddle, I realized the bot has no built in memory. This means any references I make to where I drafted Waddle in previous conversations is not saved and I will be forced to repeat these details anytime I want to perform analysis in the future (super annoying). This appears to be because of the stateless nature of Gemini API calls. 
Gemini does have a built in option utilizing the Google GenAI SDK with 'chats.create' which will allow me to maintain ongoing conversation history, however this solution will require utilizing more and more tokens as time goes on since the entire history of messages will only increase with use. 
- One solution is to create a sliding window history that only keeps the last 2-3 messages, but this doesn't seem like a good solution as things can randomly occur with time such as a player injury in a few weeks. 
- Another solution is to create a local database that stores these kinds of details in the form of 'user notes'. This seems like a much more viable solution as duckdb tables are small and can be indexed quickly. 
Will look into this further 

## 09/23/26

### Problem: 
While Hank2.0 is great queries related to offensive players it falls short on anything related to defense, especially related to defensive performance vs a specific player. For example, if I want to query which defense gives up the most points in fantasy to Tight Ends, the result is N/A. 

### Solution: 
The plan is the implement play by play data from NFLReadR as well as player roster data. The play by play data is a breakdown of each play from each game in the season and contains much more situational data compared to the cumulative stats collected in the weekly stats table (consisting of the player's combined stats at the end of the week). 

## 09/22/26: The Birth of Hank 2.0 

The official name of my discord bot is Hank 2.0 in honor of my friend and Fantasy Football co-manager Hank 1.0. Hank 2.0 is designed to be everything Hank 1.0, knowledgeable and analytical regarding the game of football and player statistics. 