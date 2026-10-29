"""Create a private, idempotent SQL seed file from the original CSV.

The generated file contains personal study dates and subjects. It is written to
backups/private/ (ignored by Git); never move it into the public repository.
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "Control de horas de estudio (Copia 1) - Hoja 1.csv"
OUTPUT = ROOT / "backups" / "private" / "002_seed_sessions.sql"


def sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def load_rows() -> list[tuple[str, str, str, int, int]]:
    data = SOURCE.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    seen = set()
    for row in reader:
        identifier = row["IDEstudio"].strip()
        if not identifier or identifier in seen:
            raise ValueError(f"Identificador vacío o repetido: {identifier!r}")
        seen.add(identifier)
        raw_date = row["Fecha"].strip()
        day, month, year = raw_date.split("/")
        subject = row["Materia"].strip()
        cycles = int(row["cantidad "])
        duration = int(row["Tiempo"])
        hour, minute, second = map(int, row["Total minutos"].split(":"))
        total = hour * 60 + minute + (1 if second >= 30 else 0)
        if total != cycles * duration or not subject or cycles <= 0 or duration <= 0:
            raise ValueError(f"Registro inválido: {identifier}")
        rows.append((identifier, f"{year}-{month}-{day}", subject, cycles, duration))
    return rows


def main() -> None:
    rows = load_rows()
    if len(rows) != 61:
        raise ValueError(f"Se esperaban 61 registros y se encontraron {len(rows)}.")
    total = sum(cycles * duration for _, _, _, cycles, duration in rows)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    values = ",\n".join(
        f"  ({sql_string(identifier)}, DATE '{date}', {sql_string(subject)}, {cycles}, {duration})"
        for identifier, date, subject, cycles, duration in rows
    )
    script = f"""-- PRIVATE: contains Matias's personal study history. Never commit or share publicly.
-- Run only in the new, independent pomodoro-matias Supabase project.
-- Replace the UUID marker with the sole authorized Supabase Auth user ID first.
-- This single INSERT is safe to rerun: matching IDs are updated, foreign-owner IDs are untouched.
insert into public.pomodoro_sessions (id_estudio,user_id,fecha,materia,cantidad,tiempo)
select seed.id_estudio, 'REPLACE_WITH_AUTH_USER_UUID'::uuid, seed.fecha, seed.materia, seed.cantidad, seed.tiempo
from (values
{values}
) as seed(id_estudio,fecha,materia,cantidad,tiempo)
on conflict (id_estudio) do update set fecha=excluded.fecha,materia=excluded.materia,
  cantidad=excluded.cantidad,tiempo=excluded.tiempo,updated_at=now()
where public.pomodoro_sessions.user_id=excluded.user_id;
"""
    OUTPUT.write_text(script, encoding="utf-8")
    print(f"rows={len(rows)} minutes={total} hours={total // 60} remainder={total % 60}")
    print(f"generated={OUTPUT}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
