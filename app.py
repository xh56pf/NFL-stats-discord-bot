import streamlit as st
import duckdb
import pandas as pd
import altair as alt

st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] {
        width: 200px !important; # adjust to your desired width
        min-width: 150px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.set_page_config(
    page_title="NFL WR Target Share Analysis",
    page_icon="🏈",
    layout="wide"
)

st.title("🏈 Team WR Target Share Distribution")

@st.cache_data
def load_data():
    con = duckdb.connect("nfl_stats.duckdb", read_only=True)
    
    query = """
        SELECT  
            s.player_id,
            s.player_display_name AS player_name,
            s.team,
            s.season,
            ROUND(AVG(s.target_share), 3) AS avg_target_share,
            r.headshot_url
        FROM weekly_stats s
        LEFT JOIN rosters r 
            ON s.player_id = r.gsis_id
        WHERE s.position = 'WR' 
          AND s.target_share IS NOT NULL 
          AND s.season = 2026
        GROUP BY 
            s.player_id, 
            s.player_display_name, 
            s.team, 
            s.season, 
            r.headshot_url
        ORDER BY 
            s.team, 
            avg_target_share DESC;
    """
    df = con.execute(query).df()
    con.close()
    return df

try:
    df = load_data()
except Exception as e:
    st.error(f"Failed to read from DuckDB: {e}")
    st.stop()

if df.empty:
    st.warning("No target share data found.")
    st.stop()

# Convert share to numeric just in case
df["avg_target_share"] = pd.to_numeric(df["avg_target_share"], errors="coerce")

# 1. Sidebar Team Filter
st.sidebar.header("Filter Options")
unique_teams = sorted(df["team"].dropna().unique().tolist())
selected_team = st.sidebar.selectbox("Select Team:", unique_teams)

# 2. Filter down to selected team WRs
filtered_df = df[df["team"] == selected_team].copy()
filtered_df = filtered_df.sort_values(by="avg_target_share", ascending=False)

# WR1 Highlight Metric
if not filtered_df.empty:
    top_wr = filtered_df.iloc[0]
    st.metric(
        label=f"{selected_team} WR1: {top_wr['player_name']}",
        value=f"{top_wr['avg_target_share']:.1%}"
    )

# 3. Altair Visual with Headshots
chart = (
    alt.Chart(filtered_df)
    .mark_image(width=96, height=96)
    .encode(
        x=alt.X(
            "player_name:N",
            sort="-y",
            title="Wide Receiver",
            axis=alt.Axis(labelAngle=-40)
        ),
        y=alt.Y(
            "avg_target_share:Q",
            title="Average Target Share",
            axis=alt.Axis(format=".1%"),
            scale=alt.Scale(zero=False)
        ),
        url="headshot_url:N",
        tooltip=[
            alt.Tooltip("player_name:N", title="Player"),
            alt.Tooltip("team:N", title="Team"),
            alt.Tooltip("avg_target_share:Q", title="Target Share", format=".1%"),
        ]
    )
    .properties(height=520)
)

st.altair_chart(chart, use_container_width=True)

# Stacked horizontal bar chart for team target share breakdown

bar = (
    alt.Chart(filtered_df)
    .mark_bar(size=40)  # Explicit bar thickness ensures it renders
    .encode(
        y=alt.Y("team:N", title="Team", axis=alt.Axis(labels=True)),
        x=alt.X(
            "avg_target_share:Q",
            title="Total WR Target Share",
            axis=alt.Axis(format="%"),
            stack="zero",
        ),
        color=alt.Color(
            "player_name:N",
            title="Receiver",
            legend=alt.Legend(orient="bottom"),
        ),
        order=alt.Order("avg_target_share:Q", sort="descending"),
        tooltip=[
            alt.Tooltip("player_name:N", title="Player"),
            alt.Tooltip("avg_target_share:Q", title="Target Share", format=".1%"),
        ],
    )
    .properties(height=100)
)



# 4. (Optional) Direct text labels inside the bar slices
labels = (
    alt.Chart(filtered_df)
    .mark_text(align="center", baseline="middle", color="white", fontSize=11)
    .encode(
        y=alt.Y("team:N"),
        x=alt.X("avg_target_share:Q", title="Total WR Target Share", axis=alt.Axis(format="%", offset=12, labelPadding=10, titlePadding=12), stack="zero"),
        color=alt.Color(
            "player_name:N",
            title="Receiver",
            legend=alt.Legend(orient="bottom", columns=4)
        ),
        text=alt.Text("avg_target_share:Q", format=".1%"),
        order=alt.Order("avg_target_share:Q", sort="descending"),
        detail="player_name:N",
    )
)

team_chart = (bar + labels).properties(height=140)

st.altair_chart(team_chart, use_container_width=True)

# 4. Raw Data Table view
with st.expander("View Team WR Stats Table"):
    st.dataframe(
        filtered_df[["player_name", "team", "avg_target_share"]].reset_index(drop=True),
        use_container_width=True
    )


