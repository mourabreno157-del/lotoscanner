from __future__ import annotations

import json
import math
import sqlite3
from statistics import mean, pstdev

from db import db


JANELA = 10

FEATURES = (
    "forca_defensiva_visitante",
    "diferenca_derrota",
    "forca_ofensiva_visitante",
    "expectativa_casa",
    "ataque_visitante_vs_defesa",
    "diferenca_defesa",
    "ataque_casa_vs_defesa",
    "expectativa_visitante",
    "forca_defensiva_casa",
    "diferenca_empate",
)

DIRECAO_CASA = {
    "forca_defensiva_visitante": -1,
    "diferenca_derrota": -1,
    "forca_ofensiva_visitante": -1,
    "expectativa_casa": 1,
    "ataque_visitante_vs_defesa": -1,
    "diferenca_defesa": 1,
    "ataque_casa_vs_defesa": 1,
    "expectativa_visitante": -1,
    "forca_defensiva_casa": 1,
    "diferenca_empate": 1,
}

DIRECAO_VISITANTE = {
    "forca_defensiva_visitante": 1,
    "diferenca_derrota": 1,
    "forca_ofensiva_visitante": 1,
    "expectativa_casa": -1,
    "ataque_visitante_vs_defesa": 1,
    "diferenca_defesa": -1,
    "ataque_casa_vs_defesa": -1,
    "expectativa_visitante": 1,
    "forca_defensiva_casa": -1,
    "diferenca_empate": -1,
}


class Engine:
    def __init__(self):
        pass

    # ==========================================================
    # FASE 2
    # COLETA DE HISTÓRICO PURO
    # ==========================================================

    def _records(
        self,
        canonical_id,
        venue,
        before,
    ):
        with db.connect() as c:
            rows = c.execute(
                """
                SELECT payload_json
                FROM raw_stats
                WHERE canonical_id = ?
                AND venue = ?
                AND match_date < ?
                ORDER BY match_date DESC
                LIMIT ?
                """,
                (
                    canonical_id,
                    venue,
                    before,
                    JANELA,
                ),
            ).fetchall()

        records = []

        for row in rows:
            try:
                payload = json.loads(
                    row["payload_json"]
                )
            except (
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue

            if not isinstance(
                payload,
                dict,
            ):
                continue

            records.append(payload)

        if len(records) < JANELA:
            return None

        return records

    # ==========================================================
    # FASE 3
    # MÉTRICAS PRIMÁRIAS
    # ==========================================================

    def _basic(
        self,
        records,
    ):
        if not records:
            return None

        gols_marcados = []
        gols_sofridos = []
        gols_totais = []
        vitorias = 0
        empates = 0
        derrotas = 0

        for record in records:
            gf = record.get(
                "goals_scored"
            )
            ga = record.get(
                "goals_conceded"
            )

            if not isinstance(
                gf,
                (int, float),
            ):
                return None

            if not isinstance(
                ga,
                (int, float),
            ):
                return None

            if not math.isfinite(
                float(gf)
            ):
                return None

            if not math.isfinite(
                float(ga)
            ):
                return None

            gf = float(gf)
            ga = float(ga)

            gols_marcados.append(gf)
            gols_sofridos.append(ga)
            gols_totais.append(gf + ga)

            if gf > ga:
                vitorias += 1
            elif gf == ga:
                empates += 1
            else:
                derrotas += 1

        total = len(records)

        return {
            "gols_marcados": mean(gols_marcados),
            "gols_sofridos": mean(gols_sofridos),
            "gols_totais": mean(gols_totais),
            "vitoria": vitorias / total,
            "empate": empates / total,
            "derrota": derrotas / total,
        }

    # ==========================================================
    # FASE 4
    # CRUZAMENTO DE FORÇAS CRUZADAS
    # ==========================================================

    def _confronto(
        self,
        casa,
        visitante,
    ):
        gols_marcados_casa = (
            casa["gols_marcados"]
        )
        gols_sofridos_casa = (
            casa["gols_sofridos"]
        )
        gols_marcados_visitante = (
            visitante["gols_marcados"]
        )
        gols_sofridos_visitante = (
            visitante["gols_sofridos"]
        )

        forca_defensiva_casa = (
            1.0
            / (
                1.0
                + max(
                    gols_sofridos_casa,
                    0.0,
                )
            )
        )

        forca_defensiva_visitante = (
            1.0
            / (
                1.0
                + max(
                    gols_sofridos_visitante,
                    0.0,
                )
            )
        )

        ataque_casa_vs_defesa = (
            gols_marcados_casa
            * forca_defensiva_visitante
        )

        ataque_visitante_vs_defesa = (
            gols_marcados_visitante
            * forca_defensiva_casa
        )

        expectativa_casa = (
            gols_marcados_casa
            + gols_sofridos_visitante
        ) / 2.0

        expectativa_visitante = (
            gols_marcados_visitante
            + gols_sofridos_casa
        ) / 2.0

        diferenca_derrota = (
            casa["derrota"]
            - visitante["derrota"]
        )

        diferenca_defesa = (
            forca_defensiva_casa
            - forca_defensiva_visitante
        )

        diferenca_empate = (
            casa["empate"]
            - visitante["empate"]
        )

        return {
            "forca_defensiva_visitante": forca_defensiva_visitante,
            "diferenca_derrota": diferenca_derrota,
            "forca_ofensiva_visitante": gols_marcados_visitante,
            "expectativa_casa": expectativa_casa,
            "ataque_visitante_vs_defesa": ataque_visitante_vs_defesa,
            "diferenca_defesa": diferenca_defesa,
            "ataque_casa_vs_defesa": ataque_casa_vs_defesa,
            "expectativa_visitante": expectativa_visitante,
            "forca_defensiva_casa": forca_defensiva_casa,
            "diferenca_empate": diferenca_empate,
        }

    # ==========================================================
    # FASE 5
    # PARÂMETROS HISTÓRICOS GLOBAIS
    # ==========================================================

    def _historical_params(
        self,
        before,
    ):
        valores = {
            feature: []
            for feature in FEATURES
        }

        with db.connect() as c:
            fixtures = c.execute(
                """
                SELECT source_fixture_id, match_date
                FROM raw_stats
                WHERE match_date < ?
                GROUP BY source_fixture_id, match_date
                ORDER BY match_date ASC
                """,
                (before,),
            ).fetchall()

        for fixture in fixtures:
            fixture_id = (
                fixture["source_fixture_id"]
            )
            match_date = (
                fixture["match_date"]
            )

            with db.connect() as c:
                rows = c.execute(
                    """
                    SELECT canonical_id, venue
                    FROM raw_stats
                    WHERE source_fixture_id = ?
                    AND match_date = ?
                    """,
                    (
                        fixture_id,
                        match_date,
                    ),
                ).fetchall()

            casa_id = None
            visitante_id = None

            for row in rows:
                if row["venue"] == "HOME":
                    casa_id = (
                        row["canonical_id"]
                    )
                elif row["venue"] == "AWAY":
                    visitante_id = (
                        row["canonical_id"]
                    )

            if not casa_id:
                continue

            if not visitante_id:
                continue

            casa_records = self._records(
                casa_id,
                "HOME",
                match_date,
            )

            visitante_records = self._records(
                visitante_id,
                "AWAY",
                match_date,
            )

            if not casa_records:
                continue

            if not visitante_records:
                continue

            casa = self._basic(
                casa_records
            )
            visitante = self._basic(
                visitante_records
            )

            if not casa:
                continue

            if not visitante:
                continue

            confronto = self._confronto(
                casa,
                visitante,
            )

            for feature in FEATURES:
                value = confronto[feature]

                if not isinstance(
                    value,
                    (int, float),
                ):
                    continue

                value = float(value)

                if not math.isfinite(value):
                    continue

                valores[feature].append(value)

        params = {}

        for feature in FEATURES:
            series = valores[feature]

            if len(series) < 2:
                return None

            media_global = mean(series)
            desvio_padrao = pstdev(series)

            if not math.isfinite(
                media_global
            ):
                return None

            if (
                not math.isfinite(
                    desvio_padrao
                )
                or desvio_padrao <= 0
            ):
                return None

            params[feature] = {
                "media": media_global,
                "desvio": desvio_padrao,
            }

        return params

    # ==========================================================
    # FASE 5
    # ESCORE-Z E SCORES V1 / VX / V2
    # ==========================================================

    def _scores(
        self,
        features,
        params,
    ):
        score_casa = 0.0
        score_empate = 0.0
        score_visitante = 0.0
        z_values = {}

        for feature in FEATURES:
            media = params[feature]["media"]
            desvio = params[feature]["desvio"]
            valor = features[feature]

            z = (
                valor - media
            ) / desvio

            if not math.isfinite(z):
                return None

            z_values[feature] = z

            impacto_casa = (
                z
                * DIRECAO_CASA[feature]
            )

            impacto_visitante = (
                z
                * DIRECAO_VISITANTE[feature]
            )

            score_casa += max(
                impacto_casa,
                0.0,
            )

            score_visitante += max(
                impacto_visitante,
                0.0,
            )

            score_empate += max(
                1.0 - abs(z),
                0.0,
            )

        if (
            score_casa <= 0
            and score_visitante <= 0
        ):
            score_casa = 1.0
            score_visitante = 1.0

        return {
            "1": score_casa,
            "X": score_empate,
            "2": score_visitante,
            "_z": z_values,
        }

    # ==========================================================
    # FASE 6
    # DISTRIBUIÇÃO PROPORCIONAL
    # ==========================================================

    def _probabilities(
        self,
        scores,
    ):
        score_casa = scores["1"]
        score_empate = scores["X"]
        score_visitante = scores["2"]

        total = (
            score_casa
            + score_empate
            + score_visitante
        )

        if (
            total <= 0
            or not math.isfinite(total)
        ):
            return None

        return {
            "1": score_casa / total,
            "X": score_empate / total,
            "2": score_visitante / total,
        }

    # ==========================================================
    # FASE 6
    # OTIMIZAÇÃO COMBINATÓRIA
    # ==========================================================

    def _optimize(
        self,
        results,
    ):
        if len(results) != 14:
            return results

        if not all(
            result.get("status") == "OK"
            for result in results
        ):
            return results

        pmax = []
        segundo = []

        for result in results:
            ordered = sorted(
                result["probabilities"].values(),
                reverse=True,
            )
            pmax.append(ordered[0])
            segundo.append(ordered[1])

        cobertura_base = 1.0

        for valor in pmax:
            cobertura_base *= valor

        melhor_cobertura = -1.0
        melhor_tripla = None
        melhor_dupla = None

        for tripla in range(14):
            for dupla in range(14):
                if tripla == dupla:
                    continue

                cobertura_dupla = (
                    pmax[dupla]
                    + segundo[dupla]
                )

                cobertura = (
                    cobertura_base
                    * cobertura_dupla
                    / pmax[dupla]
                    / pmax[tripla]
                )

                if cobertura > melhor_cobertura:
                    melhor_cobertura = cobertura
                    melhor_tripla = tripla
                    melhor_dupla = dupla

        if (
            melhor_tripla is None
            or melhor_dupla is None
        ):
            return results

        for index, result in enumerate(
            results
        ):
            if index == melhor_tripla:
                result["uncertainty"] = 2.0
            elif index == melhor_dupla:
                result["uncertainty"] = 1.0
            else:
                result["uncertainty"] = 0.0

        return results

    # ==========================================================
    # CÁLCULO INDIVIDUAL
    # ==========================================================

    def game(
        self,
        g,
    ):
        before = str(
            g.get(
                "date",
                "",
            )
        )[:10]

        if not before:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        home_id = g.get(
            "home_canonical_id"
        )
        away_id = g.get(
            "away_canonical_id"
        )

        if not home_id:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        if not away_id:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        casa_records = self._records(
            home_id,
            "HOME",
            before,
        )

        visitante_records = self._records(
            away_id,
            "AWAY",
            before,
        )

        if not casa_records:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        if not visitante_records:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        casa = self._basic(
            casa_records
        )
        visitante = self._basic(
            visitante_records
        )

        if not casa:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        if not visitante:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        features = self._confronto(
            casa,
            visitante,
        )

        params = self._historical_params(
            before
        )

        if not params:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        scores = self._scores(
            features,
            params,
        )

        if not scores:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        probabilities = self._probabilities(
            scores
        )

        if not probabilities:
            return {
                "status": "SEM_DADOS",
                "game": g,
            }

        ordered = sorted(
            probabilities.values(),
            reverse=True,
        )

        return {
            "status": "OK",
            "game": g,
            "scores": {
                "1": scores["1"],
                "X": scores["X"],
                "2": scores["2"],
            },
            "probabilities": probabilities,
            "prediction": max(
                probabilities,
                key=probabilities.get,
            ),
            "uncertainty": 1.0
            - (
                ordered[0]
                - ordered[1]
            ),
            "z": scores["_z"],
            "features": features,
        }

    # ==========================================================
    # GRADE COMPLETA
    # ==========================================================

    def grid(
        self,
        games,
    ):
        results = [
            self.game(game)
            for game in games
        ]

        if (
            len(results) == 14
            and all(
                result.get("status") == "OK"
                for result in results
            )
        ):
            results = self._optimize(
                results
            )

        return results
