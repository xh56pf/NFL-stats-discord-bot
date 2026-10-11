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

# Robust CSS to completely hide GitHub icon, Streamlit toolbar, and cloud badges
st.markdown(
    """
    <style>
    /* 1. Hide the entire top-right toolbar & header actions */
    [data-testid="stToolbar"],
    [data-testid="stHeader"] {
        display: none !important;
        visibility: hidden !important;
    }

    /* 2. Target specific GitHub/Cloud badges and icons */
    #GithubIcon,
    a[href*="github.com"],
    .viewerBadge_container__1QSob,
    .styles_viewerBadge__1yB5_,
    div[class*="viewerBadge"],
    div[class*="stDeployButton"] {
        display: none !important;
        visibility: hidden !important;
    }

    /* 3. Keep sidebar custom width */
    section[data-testid="stSidebar"] {
        width: 200px !important;
        min-width: 150px !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🏈 Team WR Target Share Distribution")
st.markdown(
    """
    **Interactive Guide:** Click any player's headshot to reveal their **Targets vs. Receptions** conversion rate. 
    """
)

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
            SUM(s.targets) AS total_targets,
            SUM(s.receptions) AS total_receptions,
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

# 1. Selectbox Team Filter
unique_teams = sorted(df["team"].dropna().unique().tolist())

# Find the index for 'CAR' if it exists, otherwise default to 0
default_team = "CAR"
default_index = unique_teams.index(default_team) if default_team in unique_teams else 0

selected_team = st.selectbox("Select Team:", unique_teams, index=default_index, key="team_filter")

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

# Selection point for clicking a headshot
player_selector = alt.selection_point(
    name="player_click",
    fields=["player_name"],
    clear="dblclick"  # Double-clicking anywhere clears selection and resets the view
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
            alt.Tooltip("total_targets:Q", title="Targets"),
            alt.Tooltip("total_receptions:Q", title="Catches"),
        ]
    )
    .add_params(player_selector)
    .properties(height=520)
)

# Capture chart click events
chart_event = st.altair_chart(
    chart,
    use_container_width=True,
    on_select="rerun",
    key="wr_headshot_chart"
)



# Extract selected player name from selection event
selection_data = chart_event.selection.get("player_click", [])
selected_player = selection_data[0].get("player_name") if selection_data else None

if selected_player not in filtered_df["player_name"].values:
    selected_player = None

if selected_player:
    # --- VIEW A: SELECTED PLAYER TARGETS VS CATCHES ---
    player_row = filtered_df[filtered_df["player_name"] == selected_player].iloc[0]
    
    targets = int(player_row.get("total_targets") or 0)
    catches = int(player_row.get("total_receptions") or 0)
    catch_rate = (catches / targets) if targets > 0 else 0.0

    st.subheader(f"{selected_player}: Targets vs. Receptions")
    st.markdown(f"#### Catch Rate: {catch_rate:.1%}")
    # Reshape into tidy rows for Altair
    stats_df = pd.DataFrame([
        {"Metric": "Targets", "Count": targets},
        {"Metric": "Receptions", "Count": catches}
    ])

    player_bars = (
        alt.Chart(stats_df)
        .mark_bar(size=30, cornerRadiusEnd=4, opacity=0.85)
        .encode(
            # Anchor both to a single virtual baseline so axis labels don't collide
            y=alt.Y(
                "baseline:N",
                axis=alt.Axis(labels=False, ticks=False, domain=False, title=None)
            ),
            # Targets shifted UP (-10px), Receptions shifted DOWN (+10px)
            # With bar size=28, this creates an exact 8px vertical overlap
            yOffset=alt.YOffset(
                "Metric:N",
                scale=alt.Scale(domain=["Targets", "Receptions"], range=[-40, 0])
            ),
            x=alt.X("Count:Q", title="Total Count", axis=alt.Axis(tickMinStep=1)),
            color=alt.Color(
                "Metric:N",
                scale=alt.Scale(domain=["Targets", "Receptions"], range=["#0073CF", "#FF5F1F"]),
                legend=alt.Legend(
                    orient="top",
                    title=None,
                    labelFontSize=12,
                    labelFontWeight="bold"
                )
            ),
            tooltip=[
                alt.Tooltip("Metric:N", title="Stat"),
                alt.Tooltip("Count:Q", title="Total")
            ]
        )
        .transform_calculate(baseline="'Volume'")
    )

    player_labels = (
        alt.Chart(stats_df)
        .mark_text(align="left", baseline="middle", dx=8, fontSize=12, fontWeight="bold", color="white")
        .encode(
            y=alt.Y("baseline:N"),
            yOffset=alt.YOffset(
                "Metric:N",
                scale=alt.Scale(domain=["Targets", "Receptions"], range=[-40, 0])
            ),
            x=alt.X("Count:Q"),
            text=alt.Text("label_display:N")
        )
        .transform_calculate(
            baseline="'Volume'",
            label_display="datum.Count + ' ' + datum.Metric"  # Displays e.g. "12 Targets", "8 Receptions"
        )
    )

    st.altair_chart(
        (player_bars + player_labels)
        .properties(height=130)
        .configure_view(strokeOpacity=0),
        use_container_width=True
    )
    st.caption("💡 *Double-click the scatterplot to return to the team target share breakdown.*")

else:

    # Stacked horizontal bar chart for team target share breakdown

    bar = (
        alt.Chart(filtered_df)
        .mark_bar(size=32)  # Explicit bar thickness ensures it renders
        .encode(
            y=alt.Y("team:N", title="Team", axis=alt.Axis(labels=True)),
            x=alt.X(
                "avg_target_share:Q",
                title="Total WR Target Share",
                axis=alt.Axis(format="%", offset=14, labelPadding=8, titlePadding=12),
                stack="zero",
            ),
            color=alt.Color(
                "player_name:N",
                title="Receiver",
                legend=alt.Legend(
                    orient="bottom", 
                    columns=3, 
                    labelLimit=0,
                    columnPadding=24,
                    rowPadding=8,
                    labelFontSize=12, 
                    symbolType="circle"),
            ),
            order=alt.Order("avg_target_share:Q", sort="descending"),
            tooltip=[
                alt.Tooltip("player_name:N", title="Player"),
                alt.Tooltip("avg_target_share:Q", title="Target Share", format=".1%"),
            ],
        )
    )




    # 4. Direct text labels inside the bar slices
    labels = (
        alt.Chart(filtered_df)
        .transform_filter(alt.datum.avg_target_share > 0.08)
        .mark_text(
            align="center",
            baseline="middle",
            color="white",
            fontSize=11,
            fontWeight="bold",
        )
        .encode(
            y=alt.Y("team:N"),
            x=alt.X("avg_target_share:Q", stack="zero"),
            text=alt.Text("avg_target_share:Q", format=".1%"),
            order=alt.Order("avg_target_share:Q", sort="descending"),
            detail="player_name:N",
            # Notice: NO color or legend here — avoids overriding the legend on the bar mark
        )
    )

    team_chart = ( 
        (bar + labels)
        .properties(
            height=180,
            padding={"bottom":30, "left": 10}
        )
        .configure_view(
            strokeOpacity=0
        )
    )

    st.altair_chart(team_chart, use_container_width=True)

# 4. Raw Data Table view
with st.expander("View Team WR Stats Table"):
    st.dataframe(
        filtered_df[["player_name", "team", "avg_target_share"]].reset_index(drop=True),
        use_container_width=True
    )


