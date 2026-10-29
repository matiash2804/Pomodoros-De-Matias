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

    def test_sessions_require_a_bearer_token(self):
        response = self.client.get("/api/sessions")
        self.assertEqual(response.status_code, 401)

    def test_public_config_does_not_expose_database_credentials(self):
        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://private"}, clear=False):
            response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("DATABASE_URL", response.json())
        self.assertNotIn("private", response.text)

    def test_sessions_fail_closed_until_owner_is_configured(self):
        headers = {"Authorization": "Bearer deliberately.invalid.token"}
        with patch.dict("os.environ", {"SUPABASE_URL": "https://example.supabase.co", "POMODORO_ALLOWED_USER_ID": ""}, clear=False):
            response = self.client.get("/api/sessions", headers=headers)
        self.assertEqual(response.status_code, 503)

    def test_frontend_and_pwa_resources_are_served(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/app.js").status_code, 200)
        self.assertEqual(self.client.get("/manifest.json").json()["short_name"], "Pomodoro MH")
        self.assertEqual(self.client.get("/sw.js").status_code, 200)

    def test_health_does_not_require_production_credentials(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["database_configured"])


class CrudRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        server.app.dependency_overrides[server.current_user] = lambda: "9b3095b1-d97e-47b4-80be-79325b8f77cb"
        now = datetime.now(timezone.utc).isoformat()
        self.record = {"id_estudio": "Est-test", "fecha": "2026-01-02", "materia": "Cálculo 1",
                       "cantidad": 2, "tiempo": 25, "total_minutos": 50, "created_at": now, "updated_at": now}
        self.rest_patch = patch("server.rest_request", side_effect=self.fake_rest)
        self.rest_patch.start()

    def fake_rest(self, method, path, bearer, *, params=None, json=None, prefer=None):
        if method == "GET":
            return [self.record]
        if method == "POST":
            self.record.update(json)
            self.record["total_minutos"] = self.record["cantidad"] * self.record["tiempo"]
            return [self.record]
        if method == "PATCH":
            self.record.update(json)
            self.record["total_minutos"] = self.record["cantidad"] * self.record["tiempo"]
            return [self.record]
        return [self.record]

    def tearDown(self):
        self.rest_patch.stop()
        server.app.dependency_overrides.clear()

    def test_create_update_delete_and_export(self):
        payload = {"fecha": "2026-01-02", "materia": "Cálculo 1", "cantidad": 2, "tiempo": 25}
        headers = {"Authorization": "Bearer mock-token"}
        created = self.client.post("/api/sessions", json=payload, headers=headers)
        self.assertEqual(created.status_code, 201)
        record_id = created.json()["session"]["id_estudio"]
        self.assertEqual(created.json()["session"]["total_minutos"], 50)
        updated = self.client.put(f"/api/sessions/{record_id}", json={**payload, "cantidad": 3}, headers=headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["session"]["total_minutos"], 75)
        self.assertEqual(self.client.delete(f"/api/sessions/{record_id}", headers=headers).status_code, 204)
        xlsx = self.client.get("/api/export/xlsx", headers=headers)
        self.assertEqual(xlsx.status_code, 200)
        self.assertTrue(xlsx.content.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
