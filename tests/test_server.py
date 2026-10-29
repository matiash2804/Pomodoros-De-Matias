import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class CsvParsingTests(unittest.TestCase):
    def test_hh_mm_ss_is_converted_to_integer_minutes(self):
        self.assertEqual(server.parse_minutes("02:40:00"), 160)
        self.assertEqual(server.parse_minutes("00:25:00"), 25)

    def test_csv_headers_dates_unicode_and_totals(self):
        content = (
            "IDEstudio,Fecha,Materia,cantidad ,Tiempo,Total minutos\n"
            "Est001,17/11/2025,Ciencia de datos 1,4,40,02:40:00\n"
            "Est002,18/11/2025,Cálculo 1,2,40,01:20:00\n"
        ).encode("utf-8")
        rows, rejected = server.csv_rows(content)
        self.assertEqual(rejected, 0)
        self.assertEqual(rows[0]["total_minutos"], 160)
        self.assertEqual(rows[1]["fecha"].isoformat(), "2025-11-18")
        self.assertEqual(rows[1]["materia"], "Cálculo 1")

    def test_csv_rejects_bad_total_and_duplicate_identifier(self):
        content = (
            "IDEstudio,Fecha,Materia,cantidad ,Tiempo,Total minutos\n"
            "Est001,17/11/2025,Cálculo 1,2,40,01:19:00\n"
            "Est001,18/11/2025,Cálculo 1,2,40,01:20:00\n"
        ).encode("utf-8")
        rows, rejected = server.csv_rows(content)
        self.assertEqual(rows, [])
        self.assertEqual(rejected, 2)


class ApiGuardTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_sessions_require_private_cookie(self):
        response = self.client.get("/api/sessions")
        self.assertEqual(response.status_code, 401)

    def test_password_login_sets_secure_persistent_cookie_without_otp(self):
        env = {"POMODORO_ACCESS_PASSWORD": "test-access-password", "POMODORO_SESSION_SECRET": "x" * 48,
               "POMODORO_ALLOWED_USER_ID": "9b3095b1-d97e-47b4-80be-79325b8f77cb"}
        with patch.dict("os.environ", env, clear=False):
            https_client = TestClient(server.app, base_url="https://pomodoro.test")
            response = https_client.post("/api/auth/login", json={"password": "test-access-password"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["authenticated"])
            cookie = response.headers["set-cookie"].lower()
            self.assertIn("httponly", cookie)
            self.assertIn("secure", cookie)
            self.assertIn("samesite=strict", cookie)
            self.assertIn("max-age=2592000", cookie)
            self.assertEqual(https_client.get("/api/auth/session").json(), {"authenticated": True})

    def test_login_rejects_cross_origin_requests(self):
        env = {"POMODORO_ACCESS_PASSWORD": "test-access-password", "POMODORO_SESSION_SECRET": "x" * 48,
               "POMODORO_ALLOWED_USER_ID": "9b3095b1-d97e-47b4-80be-79325b8f77cb"}
        with patch.dict("os.environ", env, clear=False):
            response = self.client.post("/api/auth/login", json={"password": "test-access-password"},
                                        headers={"Origin": "https://attacker.example"})
        self.assertEqual(response.status_code, 403)

    def test_wrong_password_is_rejected(self):
        with patch.dict("os.environ", {"POMODORO_ACCESS_PASSWORD": "correct", "POMODORO_SESSION_SECRET": "x" * 48,
                                        "POMODORO_ALLOWED_USER_ID": "9b3095b1-d97e-47b4-80be-79325b8f77cb"}, clear=False):
            self.assertEqual(self.client.post("/api/auth/login", json={"password": "wrong"}).status_code, 401)

    def test_frontend_and_pwa_resources_are_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/app.js").status_code, 200)
        self.assertEqual(self.client.get("/manifest.json").json()["short_name"], "Pomodoro MH")
        self.assertEqual(self.client.get("/sw.js").status_code, 200)

    def test_health_does_not_require_production_credentials(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["database_configured"])


class FakeResult:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return [self.row] if self.row else []


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        now = datetime.now(timezone.utc)
        if query.lstrip().startswith("UPDATE"):
            row = (params[6], params[0], params[1], params[2], params[3], params[2] * params[3], now, now)
            return FakeResult(row)
        if query.lstrip().startswith("INSERT"):
            row = (params[0], params[2], params[3], params[4], params[5], params[4] * params[5], params[6], params[7])
            return FakeResult(row)
        if query.lstrip().startswith("DELETE"):
            return FakeResult(rowcount=1)
        if "SELECT id_estudio" in query:
            return FakeResult(("Est-test", date(2026, 1, 2), "Cálculo 1", 1, 25, 25, now, now))
        return FakeResult()


class CrudRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        self.env_patch = patch.dict("os.environ", {"POMODORO_ACCESS_PASSWORD": "test-access-password",
             "POMODORO_SESSION_SECRET": "x" * 48, "POMODORO_ALLOWED_USER_ID": "9b3095b1-d97e-47b4-80be-79325b8f77cb"}, clear=False)
        self.env_patch.start()
        self.client.post("/api/auth/login", json={"password": "test-access-password"})
        self.db_patch = patch("server.db_connection", return_value=FakeConnection())
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.env_patch.stop()

    def test_create_update_delete_and_export(self):
        payload = {"fecha": "2026-01-02", "materia": "Cálculo 1", "cantidad": 2, "tiempo": 25}
        created = self.client.post("/api/sessions", json=payload)
        self.assertEqual(created.status_code, 201)
        record_id = created.json()["session"]["id_estudio"]
        self.assertEqual(created.json()["session"]["total_minutos"], 50)
        updated = self.client.put(f"/api/sessions/{record_id}", json={**payload, "cantidad": 3})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["session"]["total_minutos"], 75)
        self.assertEqual(self.client.delete(f"/api/sessions/{record_id}").status_code, 204)
        xlsx = self.client.get("/api/export/xlsx")
        self.assertEqual(xlsx.status_code, 200)
        self.assertTrue(xlsx.content.startswith(b"PK"))

    def test_logout_clears_session(self):
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/session").json(), {"authenticated": False})
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)


if __name__ == "__main__":
    unittest.main()
