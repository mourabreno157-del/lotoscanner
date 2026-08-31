import json
import os
import urllib.parse
import urllib.request
import unicodedata
from difflib import SequenceMatcher
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine import Engine

API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
BIGBALLS_KEY = os.getenv("BIGBALLS_KEY", os.getenv("BIGBALLSDATA_API_KEY", ""))
PORT = int(os.getenv("PORT", "8000"))

def get_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))

def normalize_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", " e ")
    return " ".join("".join(ch if ch.isalnum() or ch.isspace() else " " for ch in text).split())


def similarity(query, candidate):
    q = normalize_text(query)
    c = normalize_text(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c or c in q:
        return 0.92
    return SequenceMatcher(None, q, c).ratio()


def rank_results(query, results, name_key="name", limit=10):
    return sorted(
        results,
        key=lambda item: similarity(
            query,
            item.get(name_key) or item.get("short_name") or ""
        ),
        reverse=True,
    )[:limit]


def search_local_team(name):
    from db import db
    term = normalize_text(name)

    with db.connect() as c:
        rows = c.execute("""
            SELECT id, name, api_id, provider, country, short_name
            FROM teams
        """).fetchall()

    candidates = []
    for row in rows:
        score = max(
            similarity(term, row["name"]),
            similarity(term, row["short_name"])
        )
        if score >= 0.45:
            candidates.append((score, row))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [{
        "local_id": row["id"],
        "provider": row["provider"],
        "canonical_id": f"{row['provider']}:{row['api_id']}",
        "id": row["api_id"],
        "name": row["name"],
        "short_name": row["short_name"],
        "country": row["country"]
    } for _, row in candidates[:10]]



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

    with db.connect() as c:
        rows = c.execute("""
            SELECT id, name, api_id, provider, country
            FROM competitions
        """).fetchall()

    candidates = []
    for row in rows:
        score = similarity(name, row["name"])
        if score >= 0.40:
            candidates.append((score, row))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return [{
        "local_id": row["id"],
        "provider": row["provider"],
        "canonical_id": f"{row['provider']}:{row['api_id']}",
        "id": row["api_id"],
        "name": row["name"],
        "country": row["country"]
    } for _, row in candidates[:10]]


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

    aliases = {
        "brasileirao": ["brasileirao", "brazil serie a", "serie a brazil", "campeonato brasileiro"],
        "brasileiro": ["brasileiro", "brazil serie a", "serie a brazil", "campeonato brasileiro"],
        "campeonato brasileiro": ["campeonato brasileiro", "brazil serie a", "serie a brazil"],
        "brasileirão série a": ["brasileirao serie a", "brazil serie a", "serie a brazil"],
        "la liga": ["la liga", "laliga", "primera division"],
    }
    normalized = normalize_text(name)
    queries = aliases.get(normalized, [name])
    if name not in queries:
        queries.append(name)

    found = {}
    for query_text in queries:
        try:
            data = get_json(
                "https://v3.football.api-sports.io/leagues?" +
                urllib.parse.urlencode({"search": query_text}),
                {"x-apisports-key": API_FOOTBALL_KEY}
            )
        except Exception:
            continue

        for item in data.get("response", []):
            league = item.get("league", {}) or {}
            country = item.get("country", {}) or {}
            league_id = league.get("id")
            if league_id is None:
                continue
            found[str(league_id)] = {
                "provider": "api-football",
                "id": league_id,
                "name": league.get("name"),
                "country": country.get("name")
            }

    results = list(found.values())
    return rank_results(name, results, "name", 10)


def search_bigballs_leagues(name):
    if not BIGBALLS_KEY:
        return []
    try:
        data = get_json(
            "https://api.bigballsdata.com/v1/leagues?" +
            urllib.parse.urlencode({"sport": "football"}),
            {"Authorization": f"Bearer {BIGBALLS_KEY}"}
        )
        rows = data.get("data", []) if isinstance(data, dict) else []
        results = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = similarity(name, row.get("name"))
            if score >= 0.40:
                results.append({
                    "provider": "bigballs",
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "country": row.get("country") or row.get("country_name"),
                    "slug": row.get("slug") or row.get("key") or row.get("id")
                })
        return sorted(results, key=lambda x: similarity(name, x["name"]), reverse=True)[:10]
    except Exception:
        return []


def identify_competition(name):
    local = search_local_competition(name)
    if local:
        return local

    results = search_competition_api_football(name)
    if results:
        for item in results:
            save_competition(item)
        return results

    results = search_bigballs_leagues(name)
    for item in results:
        save_competition(item)
    return results



def search_api_football(name):
    if not API_FOOTBALL_KEY:
        return []

    consultas = []
    nome = (name or "").strip()

    if nome:
        consultas.append(nome)

        partes = nome.split()
        if len(partes) > 1:
            consultas.extend(partes)

    consultas = list(dict.fromkeys(consultas))

    encontrados = []

    for consulta in consultas:
        try:
            query = urllib.parse.quote(consulta)

            data = get_json(
                f"https://v3.football.api-sports.io/teams?search={query}",
                {"x-apisports-key": API_FOOTBALL_KEY}
            )

            for item in data.get("response", []):
                team = item.get("team", {}) or {}

                if not team.get("id"):
                    continue

                encontrados.append({
                    "provider": "api-football",
                    "id": team.get("id"),
                    "canonical_id": f"api-football:{team.get('id')}",
                    "name": team.get("name"),
                    "short_name": team.get("code"),
                    "country": team.get("country"),
                    "logo": team.get("logo"),
                    "venue": item.get("venue", {})
                })

        except Exception:
            continue

    # Remove duplicados mantendo a primeira ocorrência
    unicos = {}
    for team in encontrados:
        unicos[team["id"]] = team

    encontrados = list(unicos.values())

    return rank_results(name, encontrados, "name", 10)



def team_history_api_football(team_id, venue, league_id=None, season=None, before=None):
    if not API_FOOTBALL_KEY:
        return []

    params = {
        "team": team_id,
        "last": 30,
    }
    if league_id:
        params["league"] = league_id
    if season:
        params["season"] = season

    data = get_json(
        "https://v3.football.api-sports.io/fixtures?" +
        urllib.parse.urlencode(params),
        {"x-apisports-key": API_FOOTBALL_KEY}
    )

    records = []
    target_id = int(team_id)

    for item in data.get("response", []):
        fixture = item.get("fixture", {}) or {}
        teams = item.get("teams", {}) or {}
        goals = item.get("goals", {}) or {}
        league = item.get("league", {}) or {}

        home = teams.get("home", {}) or {}
        away = teams.get("away", {}) or {}
        home_id = home.get("id")
        away_id = away.get("id")

        if venue == "HOME" and home_id != target_id:
            continue
        if venue == "AWAY" and away_id != target_id:
            continue

        match_date = str(fixture.get("date") or "")
        if before and match_date[:10] >= str(before)[:10]:
            continue

        home_goals = goals.get("home")
        away_goals = goals.get("away")

        if not isinstance(home_goals, (int, float)) or not isinstance(away_goals, (int, float)):
            continue

        fixture_id = fixture.get("id")
        competition_id = league.get("id")
        season_value = league.get("season")

        if home_id is not None:
            records.append({
                "source": "api-football",
                "source_fixture_id": fixture_id,
                "canonical_id": f"api-football:{home_id}",
                "match_date": match_date,
                "venue": "HOME",
                "goals_scored": home_goals,
                "goals_conceded": away_goals,
                "home_team": home.get("name"),
                "away_team": away.get("name"),
                "competition_provider": "api-football",
                "competition_id": competition_id,
                "season": season_value,
            })

        if away_id is not None:
            records.append({
                "source": "api-football",
                "source_fixture_id": fixture_id,
                "canonical_id": f"api-football:{away_id}",
                "match_date": match_date,
                "venue": "AWAY",
                "goals_scored": away_goals,
                "goals_conceded": home_goals,
                "home_team": home.get("name"),
                "away_team": away.get("name"),
                "competition_provider": "api-football",
                "competition_id": competition_id,
                "season": season_value,
            })

    records.sort(key=lambda x: x.get("match_date") or "", reverse=True)
    return records



def team_history_bigballs(team_id, venue, league_slug, before):
    if not BIGBALLS_KEY or not team_id or not league_slug:
        return []
    try:
        records = []
        for page in range(1, 11):
            data = get_json(
                "https://api.bigballsdata.com/v1/matches?" +
                urllib.parse.urlencode({
                    "sport": "football",
                    "league": league_slug,
                    "limit": 200,
                    "page": page,
                }),
                {"Authorization": f"Bearer {BIGBALLS_KEY}"}
            )
            rows = data.get("data", []) if isinstance(data, dict) else []
            if not rows:
                break
            for item in rows:
                home = item.get("home", {}) or {}
                away = item.get("away", {}) or {}
                if venue == "HOME" and str(home.get("id")) != str(team_id):
                    continue
                if venue == "AWAY" and str(away.get("id")) != str(team_id):
                    continue
                kickoff = str(item.get("kickoff_utc") or item.get("kickoff") or "")
                if not kickoff or kickoff[:10] >= str(before)[:10]:
                    continue
                score = item.get("score", {}) or {}
                hf = score.get("home")
                af = score.get("away")
                if not isinstance(hf, (int, float)) or not isinstance(af, (int, float)):
                    continue
                records.append({
                    "source_fixture_id": f"bigballs:{item.get('id')}",
                    "canonical_id": f"bigballs:{team_id}",
                    "match_date": kickoff,
                    "venue": venue,
                    "goals_scored": hf if venue == "HOME" else af,
                    "goals_conceded": af if venue == "HOME" else hf,
                    "home_team": home.get("name"),
                    "away_team": away.get("name"),
                    "competition_provider": "bigballs",
                    "competition_id": league_slug,
                    "season": item.get("season"),
                })
            if len(rows) < 200:
                break
        records.sort(key=lambda x: x.get("match_date") or "", reverse=True)
        return records
    except Exception:
        return []


def team_history(team_id, venue, league_id=None, season=None, before=None, provider="api-football", league_slug=None):
    if provider == "bigballs":
        return team_history_bigballs(team_id, venue, league_slug, before)
    return team_history_api_football(team_id, venue, league_id, season, before)



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
    # Primeiro tenta as APIs para obter uma identificação confiável.
    results = search_api_football(name)

    if results:
        for team in results:
            save_team(team)
        return results

    # Se a Football API não encontrar, tenta BigBalls.
    results = search_bigballs(name)

    if results:
        for team in results:
            save_team(team)
        return results

    # Só usa o banco local como último recurso.
    local = search_local_team(name)

    if local:
        return local

    return []



def save_history(records):
    import json as _json
    from db import db

    with db.connect() as c:
        for record in records:
            fixture_id = record.get("source_fixture_id")
            canonical_id = str(record.get("canonical_id") or "")
            venue = record.get("venue")
            match_date = str(record.get("match_date") or "")[:10]

            if not fixture_id or not canonical_id or not venue or not match_date:
                continue

            exists = c.execute(
                """
                SELECT 1
                FROM raw_stats
                WHERE source_fixture_id = ?
                  AND canonical_id = ?
                  AND venue = ?
                  AND match_date = ?
                LIMIT 1
                """,
                (fixture_id, canonical_id, venue, match_date)
            ).fetchone()

            if exists:
                continue

            c.execute(
                """
                INSERT INTO raw_stats
                (source_fixture_id, canonical_id, venue, match_date, competition_provider, competition_id, season, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fixture_id,
                    canonical_id,
                    venue,
                    match_date,
                    record.get("competition_provider"),
                    str(record.get("competition_id") or ""),
                    str(record.get("season") or ""),
                    _json.dumps({
                        "goals_scored": record.get("goals_scored"),
                        "goals_conceded": record.get("goals_conceded"),
                        "competition_provider": record.get("competition_provider"),
                        "competition_id": record.get("competition_id"),
                        "season": record.get("season"),
                    }, ensure_ascii=False)
                )
            )
        c.commit()


def fetch_bigballs_history(league_slug, before):
    if not BIGBALLS_KEY or not league_slug:
        return []
    try:
        records = []
        for page in range(1, 11):
            data = get_json(
                "https://api.bigballsdata.com/v1/matches?" +
                urllib.parse.urlencode({
                    "sport": "football",
                    "league": league_slug,
                    "limit": 200,
                    "page": page,
                }),
                {"Authorization": f"Bearer {BIGBALLS_KEY}"}
            )
            rows = data.get("data", []) if isinstance(data, dict) else []
            if not rows:
                break
            for item in rows:
                if not isinstance(item, dict):
                    continue
                kickoff = str(item.get("kickoff_utc") or item.get("kickoff") or "")
                if not kickoff or kickoff[:10] >= str(before)[:10]:
                    continue
                home = item.get("home", {}) or {}
                away = item.get("away", {}) or {}
                score = item.get("score", {}) or {}
                hf = score.get("home")
                af = score.get("away")
                if not isinstance(hf, (int, float)) or not isinstance(af, (int, float)):
                    continue
                fixture_id = item.get("id")
                records.append({
                    "source_fixture_id": f"bigballs:{fixture_id}",
                    "canonical_id": f"bigballs:{home.get('id')}",
                    "match_date": kickoff,
                    "venue": "HOME",
                    "goals_scored": hf,
                    "goals_conceded": af,
                    "home_team": home.get("name"),
                    "away_team": away.get("name"),
                    "competition_provider": "bigballs",
                    "competition_id": item.get("league") or league_slug,
                    "season": item.get("season"),
                })
                records.append({
                    "source_fixture_id": f"bigballs:{fixture_id}",
                    "canonical_id": f"bigballs:{away.get('id')}",
                    "match_date": kickoff,
                    "venue": "AWAY",
                    "goals_scored": af,
                    "goals_conceded": hf,
                    "home_team": home.get("name"),
                    "away_team": away.get("name"),
                    "competition_provider": "bigballs",
                    "competition_id": item.get("league") or league_slug,
                    "season": item.get("season"),
                })
            if len(rows) < 200:
                break
        return records
    except Exception:
        return []


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
            competition_provider = params.get("competition_provider", ["api-football"])[0].strip()
            competition_id = params.get("competition_id", [""])[0].strip()
            season = params.get("season", [""])[0].strip()

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
                    "away_canonical_id": away_id,
                    "competition_provider": competition_provider,
                    "competition_id": competition_id,
                    "season": season,
                })
                self.send_json(200, result)
            except Exception as exc:
                self.send_json(500, {
                    "error": "Falha no motor matemático.",
                    "detail": str(exc)
                })
            return

        if parsed.path == "/api/history-base":
            params = urllib.parse.parse_qs(parsed.query)
            league_id = params.get("league_id", [""])[0].strip()
            season = params.get("season", [""])[0].strip()
            before = params.get("before", [""])[0].strip()
            provider = params.get("provider", ["api-football"])[0].strip()
            competition_name = params.get("competition_name", [""])[0].strip()

            if not league_id or not season or not before:
                self.send_json(400, {
                    "error": "Informe league_id, season e before."
                })
                return

            try:
                records = []

                if provider == "api-football":
                    # The global historical base belongs to this competition.
                    # Discover all seasons available for the same league and load
                    # completed fixtures before the target match. The engine itself
                    # keeps the team-specific window at exactly 10.
                    meta = get_json(
                        "https://v3.football.api-sports.io/leagues?" +
                        urllib.parse.urlencode({"id": league_id}),
                        {"x-apisports-key": API_FOOTBALL_KEY}
                    )

                    available_seasons = []
                    for item in meta.get("response", []):
                        for season_info in (item.get("seasons", []) or []):
                            year = season_info.get("year")
                            if year is None:
                                continue
                            try:
                                year_int = int(year)
                            except (TypeError, ValueError):
                                continue
                            if year_int <= int(season):
                                available_seasons.append(year_int)

                    if not available_seasons:
                        available_seasons = [int(season)]

                    for season_year in sorted(set(available_seasons), reverse=True):
                        from db import db
                        with db.connect() as c:
                            cached = c.execute(
                                """
                                SELECT COUNT(*) AS total
                                FROM raw_stats
                                WHERE competition_provider = ?
                                  AND competition_id = ?
                                  AND season = ?
                                """,
                                ("api-football", str(league_id), str(season_year))
                            ).fetchone()["total"]
                        if cached >= 20:
                            continue

                        data = get_json(
                            "https://v3.football.api-sports.io/fixtures?" +
                            urllib.parse.urlencode({
                                "league": league_id,
                                "season": season_year
                            }),
                            {"x-apisports-key": API_FOOTBALL_KEY}
                        )

                        for item in data.get("response", []):
                            fixture = item.get("fixture", {}) or {}
                            match_date = str(fixture.get("date") or "")
                            if not match_date or match_date[:10] >= before:
                                continue

                            teams = item.get("teams", {}) or {}
                            goals = item.get("goals", {}) or {}
                            league = item.get("league", {}) or {}
                            home = teams.get("home", {}) or {}
                            away = teams.get("away", {}) or {}
                            home_id = home.get("id")
                            away_id = away.get("id")
                            home_goals = goals.get("home")
                            away_goals = goals.get("away")

                            if (
                                home_id is None or away_id is None or
                                not isinstance(home_goals, (int, float)) or
                                not isinstance(away_goals, (int, float))
                            ):
                                continue

                            fixture_id = fixture.get("id")
                            records.append({
                                "source_fixture_id": fixture_id,
                                "canonical_id": f"api-football:{home_id}",
                                "match_date": match_date,
                                "venue": "HOME",
                                "goals_scored": home_goals,
                                "goals_conceded": away_goals,
                                "home_team": home.get("name"),
                                "away_team": away.get("name"),
                                "competition_provider": "api-football",
                                "competition_id": str(league_id),
                                "season": season_year,
                            })
                            records.append({
                                "source_fixture_id": fixture_id,
                                "canonical_id": f"api-football:{away_id}",
                                "match_date": match_date,
                                "venue": "AWAY",
                                "goals_scored": away_goals,
                                "goals_conceded": home_goals,
                                "home_team": home.get("name"),
                                "away_team": away.get("name"),
                                "competition_provider": "api-football",
                                "competition_id": str(league_id),
                                "season": season_year,
                            })

                if len(records) < 4 and BIGBALLS_KEY:
                    leagues = search_bigballs_leagues(competition_name or league_id)
                    if leagues:
                        records.extend(fetch_bigballs_history(
                            leagues[0].get("slug"),
                            before
                        ))

                save_history(records)
                self.send_json(200, {
                    "league_id": league_id,
                    "season": season,
                    "provider": provider,
                    "fixtures_loaded": len(records) // 2,
                    "seasons_loaded": sorted({
                        str(r.get("season"))
                        for r in records
                        if r.get("season") is not None
                    })
                })
            except Exception as exc:
                self.send_json(502, {
                    "error": "Falha ao carregar a base histórica da competição.",
                    "detail": str(exc)
                })
            return

        if parsed.path == "/api/history":
            params = urllib.parse.parse_qs(parsed.query)
            team_id = params.get("team_id", [""])[0].strip()
            venue = params.get("venue", [""])[0].strip().upper()
            competition_id = params.get("competition_id", [""])[0].strip()
            season = params.get("season", [""])[0].strip()
            before = params.get("before", [""])[0].strip()
            provider = params.get("provider", ["api-football"])[0].strip()
            league_slug = params.get("league_slug", [""])[0].strip()

            if not team_id or venue not in ("HOME", "AWAY") or not before:
                self.send_json(400, {
                    "error": "Informe team_id, venue HOME/AWAY e before."
                })
                return

            try:
                provider_from_id = "api-football"
                numeric_id = team_id
                if ":" in team_id:
                    provider_from_id, numeric_id = team_id.split(":", 1)
                provider = provider_from_id

                from db import db
                cached_records = []
                with db.connect() as c:
                    rows = c.execute(
                        """
                        SELECT payload_json
                        FROM raw_stats
                        WHERE canonical_id = ?
                          AND venue = ?
                          AND match_date < ?
                          AND competition_provider = ?
                          AND competition_id = ?
                          AND season = ?
                        ORDER BY match_date DESC
                        LIMIT 10
                        """,
                        (
                            f"{provider}:{numeric_id}",
                            venue,
                            before,
                            provider,
                            str(competition_id),
                            str(season),
                        )
                    ).fetchall()
                    cached_records = [json.loads(row["payload_json"]) for row in rows]

                if len(cached_records) >= 10:
                    records = [
                        {
                            "canonical_id": f"{provider}:{numeric_id}",
                            "venue": venue,
                            "match_date": None,
                            "goals_scored": item.get("goals_scored"),
                            "goals_conceded": item.get("goals_conceded"),
                        }
                        for item in cached_records
                    ]
                else:
                    records = team_history(
                        numeric_id,
                        venue,
                        competition_id,
                        season,
                        before,
                        provider,
                        league_slug
                    )

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

            if parsed.path == "/api/next-fixture":
                params = urllib.parse.parse_qs(parsed.query)

            home_id = params.get("home_id", [""])[0].strip()
            away_id = params.get("away_id", [""])[0].strip()
            competition_name = params.get("competition", [""])[0].strip()
            competition_id = params.get("competition_id", [""])[0].strip()

            if not home_id or not away_id:
                self.send_json(400, {
                    "error": "Informe home_id e away_id."
                })
                return

            try:
                home_provider, home_numeric = (
                    home_id.split(":", 1)
                    if ":" in home_id
                    else ("api-football", home_id)
                )

                away_provider, away_numeric = (
                    away_id.split(":", 1)
                    if ":" in away_id
                    else ("api-football", away_id)
                )

                # Os dois times precisam pertencer ao mesmo provedor.
                if home_provider != away_provider:
                    self.send_json(400, {
                        "error": "Os times pertencem a provedores diferentes."
                    })
                    return

                fixtures = []

                # =========================================================
                # API-FOOTBALL
                # =========================================================
                if home_provider == "api-football":

                    data = get_json(
                        "https://v3.football.api-sports.io/fixtures?" +
                        urllib.parse.urlencode({
                            "team": home_numeric,
                            "next": 20
                        }),
                        {"x-apisports-key": API_FOOTBALL_KEY}
                    )

                    target_home = int(home_numeric)
                    target_away = int(away_numeric)

                    for item in data.get("response", []):
                        teams = item.get("teams", {}) or {}

                        home = teams.get("home", {}) or {}
                        away = teams.get("away", {}) or {}

                        if home.get("id") != target_home:
                            continue

                        if away.get("id") != target_away:
                            continue

                        fixture = item.get("fixture", {}) or {}
                        league = item.get("league", {}) or {}

                        if competition_id:
                            if str(league.get("id")) != str(competition_id):
                                continue

                        elif competition_name:
                            requested = normalize_text(competition_name)
                            actual = normalize_text(
                                league.get("name")
                            )

                            if similarity(requested, actual) < 0.45:
                                continue

                        match_date = fixture.get("date")

                        if not match_date:
                            continue

                        fixtures.append({
                            "fixture_id": fixture.get("id"),
                            "date": match_date[:10],
                            "datetime": match_date,
                            "competition": league.get("name"),
                            "competition_id": league.get("id"),
                            "competition_provider": "api-football",
                            "season": league.get("season"),
                            "home_name": home.get("name"),
                            "away_name": away.get("name")
                        })

                # =========================================================
                # BIGBALLS
                # =========================================================
                elif home_provider == "bigballs":

                    if not BIGBALLS_KEY:
                        self.send_json(503, {
                            "error": "BIGBALLS_KEY não configurada."
                        })
                        return

                    # Se recebemos um ID de competição, tentamos usá-lo
                    # diretamente. Caso contrário, tentamos descobrir o slug.
                    league_values = []

                    if competition_id:
                        league_values.append(competition_id)

                    if competition_name:
                        leagues = search_bigballs_leagues(
                            competition_name
                        )

                        for league in leagues:
                            slug = league.get("slug")

                            if slug and slug not in league_values:
                                league_values.append(slug)

                    # Sem competição conhecida, consulta o feed geral.
                    if not league_values:
                        league_values.append(None)

                    target_home = str(home_numeric)
                    target_away = str(away_numeric)

                    encontrados_ids = set()

                    for league_value in league_values:

                        for page in range(1, 11):

                            query_params = {
                                "sport": "football",
                                "limit": 200,
                                "page": page,
                            }

                            if league_value:
                                query_params["league"] = league_value

                            data = get_json(
                                "https://api.bigballsdata.com/v1/matches?" +
                                urllib.parse.urlencode(query_params),
                                {
                                    "Authorization":
                                    f"Bearer {BIGBALLS_KEY}"
                                }
                            )

                            rows = (
                                data.get("data", [])
                                if isinstance(data, dict)
                                else []
                            )

                            if not rows:
                                break

                            for item in rows:

                                if not isinstance(item, dict):
                                    continue

                                home = item.get("home", {}) or {}
                                away = item.get("away", {}) or {}

                                if str(home.get("id")) != target_home:
                                    continue

                                if str(away.get("id")) != target_away:
                                    continue

                                kickoff = str(
                                    item.get("kickoff_utc")
                                    or item.get("kickoff")
                                    or ""
                                )

                                if not kickoff:
                                    continue

                                # Evita duplicados quando consultamos
                                # mais de um slug/competição.
                                fixture_id = str(item.get("id"))

                                if fixture_id in encontrados_ids:
                                    continue

                                encontrados_ids.add(fixture_id)

                                league_value_from_match = (
                                    item.get("league")
                                )

                                fixtures.append({
                                    "fixture_id":
                                        f"bigballs:{item.get('id')}",
                                    "date": kickoff[:10],
                                    "datetime": kickoff,
                                    "competition":
                                        league_value_from_match,
                                    "competition_id":
                                        league_value_from_match,
                                    "competition_provider":
                                        "bigballs",
                                    "season":
                                        item.get("season"),
                                    "home_name":
                                        home.get("name"),
                                    "away_name":
                                        away.get("name")
                                })

                            if len(rows) < 200:
                                break

                else:
                    self.send_json(400, {
                        "error": f"Provedor não suportado: {home_provider}"
                    })
                    return

                # =========================================================
                # RESULTADO
                # =========================================================

                if not fixtures:
                    self.send_json(404, {
                        "error":
                        "Não foi encontrada uma próxima partida entre esses dois times."
                    })
                    return

                # Garante que pegamos o jogo mais próximo.
                fixtures.sort(
                    key=lambda x: x.get("datetime") or ""
                )

                self.send_json(200, fixtures[0])

            except Exception as exc:
                self.send_json(502, {
                    "error": "Falha ao localizar a próxima partida.",
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
            base_dir = os.path.dirname(os.path.abspath(__file__))
            index_path = os.path.join(base_dir, "index.html")

            try:
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
