import requests
import streamlit as st
from datetime import datetime
from openai import OpenAI

# ==============================
# KEYS
# ==============================
API_KEY = st.secrets["API_KEY"]
OPENAI_KEY = st.secrets["OPENAI_KEY"]
DISCORD_WEBHOOK = st.secrets["DISCORD_WEBHOOK"]

HEADERS = {"x-apisports-key": API_KEY}
client = OpenAI(api_key=OPENAI_KEY)

team_cache = {}

# ==============================
# STYLE
# ==============================
st.set_page_config(layout="wide")

st.markdown("""
<style>
.main-card {
    background: #111;
    padding: 20px;
    border-radius: 15px;
    margin-bottom: 15px;
}
.stat {font-size:18px;margin:5px 0;}
.green {color:#00ff88;}
.red {color:#ff4d4d;}
.big {font-size:26px;font-weight:bold;}
</style>
""", unsafe_allow_html=True)

# ==============================
# REQUEST
# ==============================
def safe_request(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        return r.json().get("response", [])
    except:
        return []

# ==============================
# TEAM SEARCH
# ==============================
def search_teams(q):
    data = safe_request(f"https://v3.football.api-sports.io/teams?search={q}")
    return [{
        "id": t["team"]["id"],
        "label": f"{t['team']['name']} ({t['team']['country']})"
    } for t in data] if data else []

# ==============================
# MATCHES
# ==============================
def get_matches(team_id):
    data = safe_request(f"https://v3.football.api-sports.io/fixtures?team={team_id}&next=10")
    matches = []

    for m in data or []:
        dt = datetime.fromisoformat(m["fixture"]["date"].replace("Z","+00:00"))
        matches.append({
            "home": m["teams"]["home"]["name"],
            "away": m["teams"]["away"]["name"],
            "home_id": m["teams"]["home"]["id"],
            "away_id": m["teams"]["away"]["id"],
            "label": f"{m['teams']['home']['name']} vs {m['teams']['away']['name']} | {dt.strftime('%d %b %H:%M')}"
        })

    return matches

# ==============================
# TEAM FORM
# ==============================
def get_form(team_id):

    if team_id in team_cache:
        return team_cache[team_id]

    data = safe_request(f"https://v3.football.api-sports.io/fixtures?team={team_id}&last=15")

    scored = conceded = over15 = games = 0

    for m in data:
        hg, ag = m["goals"]["home"], m["goals"]["away"]
        if hg is None or ag is None:
            continue

        if team_id == m["teams"]["home"]["id"]:
            scored += hg
            conceded += ag
        else:
            scored += ag
            conceded += hg

        if hg + ag >= 2:
            over15 += 1

        games += 1

    if games == 0:
        return None

    stats = {
        "scored": round(scored/games,2),
        "conceded": round(conceded/games,2),
        "over15": round((over15/games)*100,1)
    }

    team_cache[team_id] = stats
    return stats

# ==============================
# H2H AVG GOALS
# ==============================
def get_h2h(home_id, away_id):

    data = safe_request(f"https://v3.football.api-sports.io/fixtures/headtohead?h2h={home_id}-{away_id}&last=5")

    total_goals = 0
    games = 0

    for m in data:
        hg, ag = m["goals"]["home"], m["goals"]["away"]
        if hg is None or ag is None:
            continue

        total_goals += (hg + ag)
        games += 1

    if games == 0:
        return None

    return round(total_goals/games,2)

# ==============================
# SCORING MODEL
# ==============================
def score_match(h, a, odds, h2h):

    if odds > 2.2:
        return 0, 0

    score = 0

    if h["scored"] > 1.8:
        score += 4
    if a["scored"] > 1.8:
        score += 4

    if h["over15"] > 80:
        score += 3
    if a["over15"] > 75:
        score += 2

    if h2h is not None:
        if h2h >= 3.2:
            score += 4
        elif h2h >= 2.6:
            score += 3
        elif h2h >= 2.2:
            score += 2

    xg = ((h["scored"] + a["conceded"]) / 2) + ((a["scored"] + h["conceded"]) / 2)
    if xg > 2.5:
        score += 3

    if h["conceded"] < 0.7:
        score -= 2
    if a["conceded"] < 0.7:
        score -= 2

    if odds < 1.6:
        score += 4
    elif odds <= 1.8:
        score += 3

    return score, round(xg,2)

# ==============================
# CONFIDENCE
# ==============================
def get_conf(score):
    if score >= 14:
        return 80
    elif score >= 11:
        return 70
    elif score >= 8:
        return 60
    else:
        return 50

# ==============================
# AI FINAL (FIXED OUTPUT)
# ==============================
def ai_analysis(match, h, a, xg, odds, conf, h2h):

    h2h_text = f"{h2h}" if h2h is not None else "No data"

    prompt = f"""
Match: {match['home']} vs {match['away']}

Home scored {h['scored']} conceded {h['conceded']}
Away scored {a['scored']} conceded {a['conceded']}

Over1.5 rates: {h['over15']}% vs {a['over15']}%
H2H avg goals: {h2h_text}

xG: {xg}
Odds: {odds}

Base confidence: {conf}

Rules:
- Ideal odds: 1.55–1.95
- Odds >2.2 = NO PLAY
- Attack > defense
- Ignore H2H if no data
- Max confidence = 80%

Return EXACTLY this format:

Confidence: X%
Verdict: PLAY or NO PLAY
Reason: short explanation
"""

    res = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role":"user","content":prompt}]
    )

    return res.choices[0].message.content

# ==============================
# SAFE VERDICT PARSER 🔥
# ==============================
def extract_verdict(ai_text):
    text = ai_text.upper()

    if "NO PLAY" in text:
        return "NO PLAY"
    elif "PLAY" in text:
        return "PLAY"
    else:
        return "NO PLAY"  # fallback safety

# ==============================
# DISCORD
# ==============================
def send_to_discord(text):
    requests.post(DISCORD_WEBHOOK, json={"content": text})

# ==============================
# UI
# ==============================
st.title("⚽ OVER 1.5 ELITE BOT V12")

q = st.text_input("🔍 Zoek team")
teams = search_teams(q)

team = st.selectbox("Team", teams, format_func=lambda x:x["label"]) if teams else None

match = None
if team:
    matches = get_matches(team["id"])
    match = st.selectbox("Match", matches, format_func=lambda x:x["label"]) if matches else None

odds = st.number_input("📊 Over 2.5 Odds", 1.0, 5.0, 1.70)

if st.button("Analyse Match"):

    h = get_form(match["home_id"])
    a = get_form(match["away_id"])
    h2h = get_h2h(match["home_id"], match["away_id"])

    score, xg = score_match(h,a,odds,h2h)
    conf = get_conf(score)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 🏠 Home")
        st.write(h)

    with col2:
        st.markdown("### ✈️ Away")
        st.write(a)

    if h2h is not None:
        st.markdown(f"### 🔁 H2H Avg Goals: {h2h}")
    else:
        st.markdown("### 🔁 H2H Avg Goals: No data")

    st.markdown("## 🤖 AI FINAL DECISION")
    ai_result = ai_analysis(match, h, a, xg, odds, conf, h2h)
    st.write(ai_result)

    verdict = extract_verdict(ai_result)

    if verdict == "PLAY":
        st.markdown("<div class='green big'>PLAY</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='red big'>NO PLAY</div>", unsafe_allow_html=True)

    if st.button("📤 Send to Discord"):
        send_to_discord(ai_result)
        st.success("Sent!")
