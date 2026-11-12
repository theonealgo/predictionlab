import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from ncaaw_score_sync import final_rows, sync_scores

DAY = "2026-11-11"


def board(score="0", final=True):
    return {"events": [{"id": "401123456", "date": "2026-11-12T01:00Z",
        "status": {"type": {"completed": final, "name": "STATUS_FINAL" if final else "STATUS_IN_PROGRESS"}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"displayName": "Home"}, "score": score},
            {"homeAway": "away", "team": {"displayName": "Away"}, "score": "50"},
        ]}]}]}


class ScoreTests(unittest.TestCase):
    def test_missing_forecasts_are_not_reconstructed_after_the_game(self):
        import ast
        import types
        model_guard = types.ModuleType("ncaaw_guard_fixture")
        source = Path(__file__).resolve().parents[1] / "NHL77FINAL.py"
        tree = ast.parse(source.read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_model_probs_for_grading")
        function.decorator_list = []
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), model_guard.__dict__)
        model_guard._row_field = lambda row, key: row.get(key)
        model_guard._to_float_safe = lambda value: None if value is None else float(value)
        def forbidden(*args, **kwargs):
            raise AssertionError("An unforecast final must not run a model")
        model_guard._model_probs_from_row_and_v2 = forbidden
        self.assertEqual(model_guard._model_probs_for_grading("NCAAW", {}, "Home", "Away", DAY),
                         (None, None, None, None, None))
        model_guard._model_probs_from_row_and_v2 = lambda *args, **kwargs: (.61, .62, .63, .64, .65)
        self.assertEqual(model_guard._model_probs_for_grading(
            "NCAAW", {"win_probability": .65}, "Home", "Away", DAY), (.61, .62, .63, .64, .65))
    def test_real_zero_final_only_and_et_date(self):
        self.assertEqual(final_rows(board(), DAY)[0][-2:], (0, 50))
        self.assertEqual(final_rows(board(final=False), DAY), [])
        self.assertEqual(final_rows(board(score=None), DAY), [])
        self.assertEqual(final_rows(board(), "2026-11-12"), [])

    def test_idempotent_score_only_updates_and_provider_failure(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            db = Path(folder)/"scores.sqlite"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE games(sport,league,game_id UNIQUE,season,game_date,home_team_id,"
                        "away_team_id,home_score,away_score,status)")
            con.execute("CREATE TABLE predictions(game_id,original_probability,original_grade)")
            con.execute("INSERT INTO predictions VALUES('NCAAW_401123456',0.72,'original')")
            con.commit(); con.close()
            first = sync_scores(db, [DAY], fetch=lambda day: board())
            self.assertEqual(first["changed"], 1)
            self.assertEqual(sync_scores(db, [DAY], fetch=lambda day: board())["changed"], 0)
            con = sqlite3.connect(db)
            con.execute("UPDATE games SET away_score=NULL");con.commit();con.close()
            self.assertEqual(sync_scores(db, [DAY], fetch=lambda day: board())["changed"], 1)
            def offline(day): raise RuntimeError
            self.assertEqual(sync_scores(db, [DAY], fetch=offline)["unavailable_days"], [DAY])
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT * FROM predictions").fetchall(),
                             [("NCAAW_401123456",0.72,"original")])
            self.assertEqual(con.execute("SELECT home_score,away_score FROM games").fetchone(), (0,50))
            con.close()


if __name__ == "__main__":
    unittest.main()
