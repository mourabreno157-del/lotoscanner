import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import Engine

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "8c7e8ababd0abb7d3a6e072396e8b8a6")
BIGBALLS_KEY = os.getenv("BIGBALLS_KEY", os.getenv("BIGBALLSDATA_API_KEY", "bbs_live_000006USGKRSswE4pkYckXyAPSfJAEZeggN6fVURbTi0GmQH"))
PORT = int(os.getenv("PORT", "8000"))

def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))

def search_local_team(name):
    from db import db
    term = name.strip().lower()

    with db.connect() as c:
        rows = c.execute("""
            SELECT id, name, api_id, provider, country, short_name
            FROM teams
            WHERE lower(name) = ?
               OR lower(short_name) = ?
            LIMIT 10
        """, (term, term)).fetchall()

    return [{
        "local_id": row["id"],
        "provider": row["provider"],
        "canonical_id": f"{row['provider']}:{row['api_id']}",
        "id": row["api_id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "country": row["country"]
    } for row in rows]


def save_team(team):
    from db import db

    with db.connect() as c:
        existing = c.execute("""
            SELECT id FROM teams
            WHERE provider = ? AND api_id = ?
            LIMIT 1
        """, (team.get("provider"), str(team.get("id")))).fetchone()

        if existing:
            return existing["id"]

        cur = c.execute("""
            INSERT INTO teams
            (provider, api_id, name, short_name, country)
            VALUES (?, ?, ?, ?, ?)
        """, (
            team.get("provider"),
            str(team.get("id")),
            team.get("name"),
            team.get("short_name"),
            team.get("country")
        ))
        c.commit()
        return cur.lastrowid


def search_local_competition(name):
    from db import db
    term = name.strip().lower()

    with db.connect() as c:
        rows = c.execute("""
            SELECT id, name, api_id, provider, country
            FROM competitions
            WHERE lower(name) = ?
            LIMIT 10
        """, (term,)).fetchall()

    return [{
        "local_id": row["id"],
        "provider": row["provider"],
        "canonical_id": f"{row['provider']}:{row['api_id']}",
        "id": row["api_id"],
        "name": row["name"],
        "country": row["country"]
    } for row in rows]


def save_competition(item):
    from db import db

    with db.connect() as c:
        existing = c.execute("""
            SELECT id FROM competitions
            WHERE provider = ? AND api_id = ?
            LIMIT 1
        """, (item.get("provider"), str(item.get("id")))).fetchone()

        if existing:
            return existing["id"]

        cur = c.execute("""
            INSERT INTO competitions
            (provider, api_id, name, country)
            VALUES (?, ?, ?, ?)
        """, (
            item.get("provider"),
            str(item.get("id")),
            item.get("name"),
            item.get("country")
        ))
        c.commit()
        return cur.lastrowid


def search_competition_api_football(name):
    if not API_FOOTBALL_KEY:
        return []

    query = urllib.parse.quote(name)
    data = get_json(
        f"https://v3.football.api-sports.io/leagues?search={query}",
        {"x-apisports-key": API_FOOTBALL_KEY}
    )

    result = []
    for item in data.get("response", []):
        league = item.get("league", {})
        country = item.get("country", {})
        result.append({
            "provider": "api-football",
            "id": league.get("id"),
            "name": league.get("name"),
            "country": country.get("name")
        })
    return result


def identify_competition(name):
    local = search_local_competition(name)
    if local:
        return local

    results = search_competition_api_football(name)
    for item in results:
        save_competition(item)
    return results


def search_api_football(name):
    if not API_FOOTBALL_KEY:
        return []
    query = urllib.parse.quote(name)
    data = get_json(
        f"https://v3.football.api-sports.io/teams?search={query}",
        {"x-apisports-key": API_FOOTBALL_KEY}
    )
    result = []
    for item in data.get("response", []):
        team = item.get("team", {})
        result.append({
            "provider": "api-football",
            "id": team.get("id"),
            "canonical_id": f"api-football:{team.get('id')}",
            "name": team.get("name"),
            "short_name": team.get("code"),
            "country": team.get("country"),
            "logo": team.get("logo"),
            "venue": item.get("venue", {})
        })
    return result

def team_history_api_football(team_id, venue):
    if not API_FOOTBALL_KEY:
        return []

    # Ask for a recent calendar window and keep only the requested venue.
    # The engine itself later uses the 10 most recent valid records.
    params = urllib.parse.urlencode({
        "team": team_id,
        "last": 30
    })
    data = get_json(
        f"https://v3.football.api-sports.io/fixtures?{params}",
        {"x-apisports-key": API_FOOTBALL_KEY}
    )

    records = []
    for item in data.get("response", []):
        fixture = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})

        home = teams.get("home", {})
        away = teams.get("away", {})
        home_id = home.get("id")
        away_id = away.get("id")

        if venue == "HOME" and home_id != int(team_id):
            continue
        if venue == "AWAY" and away_id != int(team_id):
            continue

        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if not isinstance(home_goals, (int, float)) or not isinstance(away_goals, (int, float)):
            continue

        if venue == "HOME":
            gf, ga = home_goals, away_goals
        else:
            gf, ga = away_goals, home_goals

        records.append({
            "source": "api-football",
            "source_fixture_id": fixture.get("id"),
            "match_date": fixture.get("date"),
            "venue": venue,
            "goals_scored": gf,
            "goals_conceded": ga,
            "home_team": home.get("name"),
            "away_team": away.get("name")
        })

    records.sort(key=lambda x: x.get("match_date") or "", reverse=True)
    return records[:10]


def team_history(team_id, venue):
    return team_history_api_football(team_id, venue)


def search_bigballs(name):
    if not BIGBALLS_KEY:
        return []
    try:
        data = get_json(
            "https://api.bigballsdata.com/v1/teams?sport=football",
            {"Authorization": f"Bearer {BIGBALLS_KEY}"}
        )
        rows = data.get("data", []) if isinstance(data, dict) else []
        term = name.strip().lower()
        result = []
        for team in rows:
            tname = ((team.get("name") or "") if isinstance(team, dict) else "")
            short = ((team.get("short_name") or "") if isinstance(team, dict) else "")
            if term in tname.lower() or term == short.lower():
                result.append({
                    "provider": "bigballs", "id": team.get("id"),
                    "canonical_id": f"bigballs:{team.get('id')}",
                    "name": tname, "short_name": short,
                    "country": team.get("country") or team.get("country_name")
                })
        return result[:10]
    except Exception:
        return []

def identify(name):
    local = search_local_team(name)
    if local:
        return local

    results = search_api_football(name)
    if results:
        for team in results:
            save_team(team)
        return results

    results = search_bigballs(name)
    if results:
        for team in results:
            save_team(team)
    return results


def save_history(records):
    import sqlite3
    import json as _json
    from db import db

    with db.connect() as c:
        for record in records:
            c.execute("""
                INSERT INTO raw_stats
                (source_fixture_id, canonical_id, venue, match_date, payload_json)
                VALUES (?, ?, ?, ?, ?)
            """, (
                record.get("source_fixture_id"),
                str(record.get("canonical_id")),
                record.get("venue"),
                str(record.get("match_date"))[:10],
                _json.dumps({
                    "goals_scored": record.get("goals_scored"),
                    "goals_conceded": record.get("goals_conceded")
                }, ensure_ascii=False)
            ))
        c.commit()

class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/identify":
            params = urllib.parse.parse_qs(parsed.query)
            name = params.get("name", [""])[0].strip()

            if len(name) < 3:
                self.send_json(400, {"error": "Informe pelo menos 3 caracteres."})
                return

            try:
                self.send_json(200, {
                    "name": name,
                    "results": identify(name)
                })
            except Exception as exc:
                self.send_json(502, {"error": "Falha na consulta.", "detail": str(exc)})
            return

        if parsed.path == "/api/analyze-grade":
            params = urllib.parse.parse_qs(parsed.query)
            raw = params.get("games", [""])[0]

            if not raw:
                self.send_json(400, {"error": "Informe os 14 jogos."})
                return

            try:
                games = json.loads(raw)
                if not isinstance(games, list) or len(games) != 14:
                    self.send_json(400, {"error": "A grade precisa ter 14 jogos."})
                    return

                engine = Engine()
                results = engine.grid(games)

                self.send_json(200, {
                    "results": results,
                    "optimization": {
                        "enabled": len(results) == 14 and all(
                            r.get("status") == "OK" for r in results
                        )
                    }
                })
            except Exception as exc:
                self.send_json(500, {
                    "error": "Falha na análise da grade.",
                    "detail": str(exc)
                })
            return

        if parsed.path == "/api/analyze":
            params = urllib.parse.parse_qs(parsed.query)
            home_id = params.get("home_id", [""])[0].strip()
            away_id = params.get("away_id", [""])[0].strip()
            date = params.get("date", [""])[0].strip()

            if not home_id or not away_id or not date:
                self.send_json(400, {
                    "error": "Informe home_id, away_id e date."
                })
                return

            try:
                engine = Engine()
                result = engine.game({
                    "date": date,
                    "home_canonical_id": home_id,
                    "away_canonical_id": away_id
                })
                self.send_json(200, result)
            except Exception as exc:
                self.send_json(500, {
                    "error": "Falha no motor matemático.",
                    "detail": str(exc)
                })
            return

        if parsed.path == "/api/history":
            params = urllib.parse.parse_qs(parsed.query)
            team_id = params.get("team_id", [""])[0].strip()
            venue = params.get("venue", [""])[0].strip().upper()

            if not team_id or venue not in ("HOME", "AWAY"):
                self.send_json(400, {
                    "error": "Informe team_id e venue HOME ou AWAY."
                })
                return

            try:
                provider = "api-football"
                numeric_id = team_id
                if ":" in team_id:
                    provider, numeric_id = team_id.split(":", 1)
                if provider != "api-football":
                    records = []
                else:
                    records = team_history(numeric_id, venue)

                for record in records:
                    record["canonical_id"] = f"api-football:{numeric_id}"

                if records:
                    save_history(records)

                self.send_json(200, {
                    "team_id": team_id,
                    "venue": venue,
                    "records": records
                })
            except Exception as exc:
                self.send_json(502, {
                    "error": "Falha ao coletar histórico.",
                    "detail": str(exc)
                })
            return

        if parsed.path == "/api/competition":
            params = urllib.parse.parse_qs(parsed.query)
            name = params.get("name", [""])[0].strip()

            if len(name) < 2:
                self.send_json(400, {"error": "Informe a competição."})
                return

            try:
                self.send_json(200, {
                    "name": name,
                    "results": identify_competition(name)
                })
            except Exception as exc:
                self.send_json(502, {
                    "error": "Falha na identificação da competição.",
                    "detail": str(exc)
                })
            return

        if parsed.path == "/api/status":
            self.send_json(200, {
                "api_football": bool(API_FOOTBALL_KEY),
                "bigballs": bool(BIGBALLS_KEY)
            })
            return

        if parsed.path == "/":
            try:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                index_path = os.path.join(base_dir, "index.html")

                with open(index_path, "rb") as f:
                    body = f.read()

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                self.send_json(500, {
                    "error": "Falha ao carregar a página.",
                    "detail": str(exc)
                })
            return

        self.send_json(404, {"error": "Rota não encontrada."})

if __name__ == "__main__":
    print("Servidor LOTOSCANNER em http://localhost:8000")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
