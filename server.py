"""FastAPI API for the personal Pomodoro app. PostgreSQL is the only record store."""
from __future__ import annotations

import csv
import io
import os
import re
import uuid
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Annotated, Any

import jwt
import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from jwt import PyJWKClient
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from dotenv import load_dotenv

APP_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(APP_DIR, ".env"))
app = FastAPI(title="Pomodoro Matías Henquín", version="1.0.0")


class SessionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    id_estudio: str | None = Field(default=None, max_length=80)
    fecha: date
    materia: str = Field(min_length=1, max_length=120)
    cantidad: int = Field(gt=0, le=100)
    tiempo: int = Field(gt=0, le=600)

    @field_validator("id_estudio")
    @classmethod
    def clean_id(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("materia")
    @classmethod
    def clean_subject(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("La materia es obligatoria.")
        return value

    @model_validator(mode="after")
    def check_total(self) -> "SessionInput":
        if self.cantidad * self.tiempo > 60000:
            raise ValueError("La sesión excede el máximo permitido.")
        return self


def config() -> dict[str, str | None]:
    return {
        "supabaseUrl": os.getenv("SUPABASE_URL"),
        "supabaseAnonKey": os.getenv("SUPABASE_ANON_KEY"),
    }


def rest_request(method: str, path: str, bearer: str, *, params=None, json=None, prefer=None):
    base = (os.getenv("SUPABASE_URL") or "").rstrip("/")
    key = os.getenv("SUPABASE_ANON_KEY", "")
    if not base or not key:
        raise HTTPException(503, "Falta configurar la conexión al proyecto Supabase de Pomodoro.")
    headers = {"apikey": key, "Authorization": f"Bearer {bearer}", "Accept": "application/json"}
    if json is not None:
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    try:
        response = httpx.request(method, f"{base}/rest/v1/{path}", headers=headers, params=params,
                                 json=json, timeout=15.0)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "No se pudo contactar la base PostgreSQL de Pomodoro.") from exc
    if response.status_code >= 400:
        detail = "La base PostgreSQL rechazó la operación."
        try:
            detail = response.json().get("message", detail)
        except (ValueError, AttributeError):
            pass
        raise HTTPException(409 if response.status_code == 409 else 503, detail)
    return response.json() if response.content else []


@lru_cache(maxsize=8)
def jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, cache_keys=True, lifespan=600, timeout=5)


def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "Iniciá sesión para continuar.", headers={"WWW-Authenticate": "Bearer"})
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    allowed_user_id = os.getenv("POMODORO_ALLOWED_USER_ID", "").strip()
    if not supabase_url or not allowed_user_id:
        raise HTTPException(503, "Falta configurar el acceso personal de Pomodoro.")
    token = authorization.split(" ", 1)[1].strip()
    try:
        issuer = f"{supabase_url}/auth/v1"
        key = jwks_client(f"{issuer}/.well-known/jwks.json").get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256", "EdDSA"],
            audience="authenticated",
            issuer=issuer,
            options={"require": ["exp", "sub", "iss", "aud"]},
            leeway=30,
        )
        user_id = str(uuid.UUID(claims["sub"]))
        expected_id = str(uuid.UUID(allowed_user_id))
        if user_id != expected_id:
            raise HTTPException(403, "Esta cuenta no está autorizada para Pomodoro.")
        return user_id
    except HTTPException:
        raise
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise HTTPException(401, "La sesión no es válida o venció.", headers={"WWW-Authenticate": "Bearer"}) from exc
    except Exception as exc:
        raise HTTPException(503, "No se pudo verificar la sesión con Supabase Auth.") from exc


UserId = Annotated[str, Depends(current_user)]


def get_sessions(user_id: str, token: str) -> list[dict[str, Any]]:
    return rest_request("GET", "pomodoro_sessions", token, params={
        "select": "id_estudio,fecha,materia,cantidad,tiempo,total_minutos,created_at,updated_at",
        "user_id": f"eq.{user_id}", "order": "fecha.desc,created_at.desc,id_estudio",
        "limit": "10000",
    })


def write_session(data: SessionInput, user_id: str, token: str, *, update_id: str | None = None) -> dict[str, Any]:
    record_id = update_id or data.id_estudio or str(uuid.uuid4())
    payload = {"id_estudio": record_id, "user_id": user_id, "fecha": data.fecha.isoformat(),
               "materia": data.materia, "cantidad": data.cantidad, "tiempo": data.tiempo}
    if update_id:
        rows = rest_request("PATCH", "pomodoro_sessions", token,
                            params={"id_estudio": f"eq.{update_id}", "user_id": f"eq.{user_id}",
                                    "select": "id_estudio,fecha,materia,cantidad,tiempo,total_minutos,created_at,updated_at"},
                            json=payload, prefer="return=representation")
    else:
        rows = rest_request("POST", "pomodoro_sessions", token,
                            params={"on_conflict": "id_estudio", "select": "id_estudio,fecha,materia,cantidad,tiempo,total_minutos,created_at,updated_at"},
                            json=payload, prefer="resolution=merge-duplicates,return=representation")
    if not rows:
        raise HTTPException(404 if update_id else 409, "No se encontró la sesión o no se pudo guardar.")
    return rows[0]


@app.get("/api/config")
def public_config():
    """Only publishable Supabase client settings; never return database or service credentials."""
    return config()


@app.get("/api/health")
def health():
    return {"ok": True, "database_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY")), "auth_configured": bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_ANON_KEY") and os.getenv("POMODORO_ALLOWED_USER_ID"))}


@app.get("/api/sessions")
def list_sessions(user_id: UserId, authorization: Annotated[str, Header()]):
    return {"sessions": get_sessions(user_id, authorization.split(" ", 1)[1])}


@app.post("/api/sessions", status_code=201)
def create_session(data: SessionInput, user_id: UserId, authorization: Annotated[str, Header()]):
    record = write_session(data, user_id, authorization.split(" ", 1)[1])
    return {"session": record}


@app.put("/api/sessions/{session_id}")
def update_session(session_id: str, data: SessionInput, user_id: UserId, authorization: Annotated[str, Header()]):
    return {"session": write_session(data, user_id, authorization.split(" ", 1)[1], update_id=session_id)}


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, user_id: UserId, authorization: Annotated[str, Header()]):
    rows = rest_request("DELETE", "pomodoro_sessions", authorization.split(" ", 1)[1],
                        params={"id_estudio": f"eq.{session_id}", "user_id": f"eq.{user_id}"},
                        prefer="return=representation")
    if not rows:
        raise HTTPException(404, "No se encontró esa sesión.")
    return JSONResponse(status_code=204, content=None)


@app.get("/api/subjects")
def list_subjects(user_id: UserId, authorization: Annotated[str, Header()]):
    records = get_sessions(user_id, authorization.split(" ", 1)[1])
    return {"subjects": sorted({item["materia"] for item in records})}


@app.get("/api/statistics")
def statistics(user_id: UserId, authorization: Annotated[str, Header()]):
    records = get_sessions(user_id, authorization.split(" ", 1)[1])
    subjects, days, weeks, months = {}, {}, {}, {}
    for item in records:
        day = date.fromisoformat(item["fecha"])
        minutes = item["total_minutos"]
        subjects[item["materia"]] = subjects.get(item["materia"], 0) + minutes
        days[day] = days.get(day, 0) + minutes
        week = day.fromordinal(day.toordinal() - day.weekday())
        month = day.replace(day=1)
        weeks[week] = weeks.get(week, 0) + minutes
        months[month] = months.get(month, 0) + minutes
    total_minutes = sum(item["total_minutos"] for item in records)
    return {
        "session_count": len(records), "total_minutos": total_minutes,
        "ciclos": sum(item["cantidad"] for item in records), "materias": len(subjects),
        "primera_fecha": min((item["fecha"] for item in records), default=None),
        "ultima_fecha": max((item["fecha"] for item in records), default=None),
        "por_materia": [{"materia": name, "total_minutos": value} for name, value in sorted(subjects.items(), key=lambda pair: (-pair[1], pair[0]))],
        "por_dia": [{"fecha": day.isoformat(), "total_minutos": value} for day, value in sorted(days.items())],
        "por_semana": [{"fecha": day.isoformat(), "total_minutos": value} for day, value in sorted(weeks.items())],
        "por_mes": [{"fecha": day.isoformat(), "total_minutos": value} for day, value in sorted(months.items())],
    }


def parse_minutes(value: str) -> int:
    value = value.strip()
    if re.fullmatch(r"\d{1,3}:\d{2}:\d{2}", value):
        hours, minutes, seconds = map(int, value.split(":"))
        if minutes > 59 or seconds > 59:
            raise ValueError("Duración horaria inválida")
        return hours * 60 + minutes + (1 if seconds >= 30 else 0)
    if re.fullmatch(r"\d+", value):
        return int(value)
    raise ValueError("Se esperaban minutos enteros o HH:MM:SS")


def csv_rows(file_bytes: bytes) -> tuple[list[dict[str, Any]], int]:
    text: str
    try:
        text = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = file_bytes.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    fields = {re.sub(r"\s+", "", name or "").lower(): name for name in (reader.fieldnames or [])}
    required = ["idestudio", "fecha", "materia", "cantidad", "tiempo", "totalminutos"]
    if not all(name in fields for name in required):
        raise HTTPException(422, "El CSV no contiene los encabezados esperados.")
    parsed: list[dict[str, Any]] = []
    rejected = 0
    seen: set[str] = set()
    for row in reader:
        try:
            identifier = str(row[fields["idestudio"]]).strip()
            if not identifier or identifier in seen:
                raise ValueError("ID vacío o repetido")
            seen.add(identifier)
            raw_date = str(row[fields["fecha"]]).strip()
            try:
                session_date = date.fromisoformat(raw_date)
            except ValueError:
                session_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
            subject = str(row[fields["materia"]]).strip()
            quantity = int(str(row[fields["cantidad"]]).strip())
            duration = parse_minutes(str(row[fields["tiempo"]]))
            total_cell = parse_minutes(str(row[fields["totalminutos"]]))
            if not subject or quantity <= 0 or duration <= 0 or quantity > 100 or duration > 600:
                raise ValueError("Datos fuera de rango")
            calculated = quantity * duration
            if total_cell != calculated:
                raise ValueError("El total no coincide con cantidad × duración")
            parsed.append({"id_estudio": identifier, "fecha": session_date, "materia": subject,
                           "cantidad": quantity, "tiempo": duration, "total_minutos": calculated})
        except (ValueError, TypeError, KeyError, OverflowError):
            rejected += 1
    return parsed, rejected


@app.post("/api/import/csv")
async def import_csv(user_id: UserId, authorization: Annotated[str, Header()], file: UploadFile = File(...)):
    raw = await file.read(5_000_001)
    if len(raw) > 5_000_000:
        raise HTTPException(413, "El archivo supera 5 MB.")
    records, rejected = csv_rows(raw)
    token = authorization.split(" ", 1)[1]
    before = {item["id_estudio"] for item in get_sessions(user_id, token)}
    payload = [{**item, "fecha": item["fecha"].isoformat(), "user_id": user_id} for item in records]
    if payload:
        rest_request("POST", "pomodoro_sessions", token, params={"on_conflict": "id_estudio"},
                     json=payload, prefer="resolution=merge-duplicates,return=minimal")
    updated = sum(item["id_estudio"] in before for item in records)
    imported = len(records) - updated
    return {"imported": imported, "updated": updated, "rejected": rejected, "received": imported + updated + rejected}


@app.get("/api/export/csv")
def export_csv(user_id: UserId, authorization: Annotated[str, Header()]):
    records = get_sessions(user_id, authorization.split(" ", 1)[1])
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["IDEstudio", "Fecha", "Materia", "Cantidad", "Tiempo (min)", "Total minutos"])
    for item in records:
        writer.writerow([item["id_estudio"], item["fecha"], item["materia"], item["cantidad"], item["tiempo"], item["total_minutos"]])
    data = ("\ufeff" + output.getvalue()).encode("utf-8")
    return StreamingResponse(io.BytesIO(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=pomodoro-sesiones.csv"})


@app.get("/api/export/xlsx")
def export_xlsx(user_id: UserId, authorization: Annotated[str, Header()]):
    records = get_sessions(user_id, authorization.split(" ", 1)[1])
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sesiones"
    sheet.append(["IDEstudio", "Fecha", "Materia", "Cantidad", "Tiempo (min)", "Total minutos"])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="6D5731")
        cell.alignment = Alignment(horizontal="center")
    for item in records:
        sheet.append([item["id_estudio"], date.fromisoformat(item["fecha"]), item["materia"], item["cantidad"], item["tiempo"], item["total_minutos"]])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in zip("ABCDEF", [40, 15, 32, 14, 17, 18]):
        sheet.column_dimensions[column].width = width
    for cell in sheet["B"][1:]:
        cell.number_format = "dd/mm/yyyy"
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return StreamingResponse(output, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=pomodoro-sesiones.xlsx"})


from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory=APP_DIR, html=True), name="static")
