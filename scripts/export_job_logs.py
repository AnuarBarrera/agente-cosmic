#!/usr/bin/env python3
"""Exporta el log completo de analisis+generacion de un AnalysisJob (busqueda
por nombre de negocio) a un archivo de texto legible — pensado para mostrar
evidencia del pipeline en vivo (ej. presentaciones/concurso).

Uso:
    python3 scripts/export_job_logs.py "<nombre o parte del negocio>" [job_id] [--out RUTA]

Fuentes de datos:
  1. Metadata del job (negocio, usuario, estado, piezas generadas con sus
     URLs) — via `docker compose exec backend manage.py shell`.
  2. `logs/llm_audit.jsonl` — un archivo persistente en disco (bind mount,
     sobrevive reinicios/recreacion de contenedores) con el detalle de CADA
     llamada a IA (prompt + respuesta) hecha durante el analisis y la
     generacion de contenido. Se filtra por nombre de negocio, ya que ese
     archivo no guarda job_id ni timestamp por linea.
  3. `docker compose logs backend rqworker` en la ventana de tiempo del job
     — SOLO si el contenedor no se reinicio desde entonces (el log de stdout
     de docker NO sobrevive un `--force-recreate`, a diferencia del punto 2).
     Si la ventana ya no esta disponible, se omite esa seccion con una nota
     explicita en vez de fallar.

Nota: si el mismo negocio se analizo mas de una vez, el filtro de
`llm_audit.jsonl` (por nombre, no hay timestamp) puede traer llamadas de
otra corrida. Para un negocio con una sola corrida esto no es un problema.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = Path.home() / "cosmic-exports"

LOG_LINE_RE = re.compile(
    r"^(?P<service>\S+)\s+\|\s+(?P<level>\w+)\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2},\d+)\s+(?P<module>\S+)\s+(?P<pid>\d+)\s+"
    r"(?P<thread>\d+)\s+(?P<msg>.*)$"
)


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0 and not result.stdout:
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"Comando fallo: {' '.join(cmd)}")
    return result.stdout


def find_job(business: str, job_id: str | None) -> dict:
    filter_line = f"qs = qs.filter(id='{job_id}')" if job_id else ""
    script = f"""
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.models import ContentPost
qs = AnalysisJob.objects.filter(brand_dna__business_name__icontains='{business}')
{filter_line}
j = qs.order_by('-created_at').first()
if not j:
    print('NOTFOUND')
else:
    print('JOBID=' + str(j.id))
    print('BUSINESS=' + j.brand_dna.business_name)
    print('USER=' + (j.user.email if j.user else ''))
    print('STATUS=' + j.status)
    print('MODE=' + j.generation_mode)
    print('START=' + j.created_at.strftime('%Y-%m-%dT%H:%M:%S'))
    posts = ContentPost.objects.filter(calendar__brand_dna__job=j).order_by('day_number')
    for p in posts:
        print(f'POST={{p.day_number}}|{{p.format}}|{{p.image_url}}|{{p.video_url}}')
        for i, u in enumerate(p.image_urls, 1):
            print(f'SLIDE={{p.day_number}}|{{i}}|{{u}}')
"""
    out = run(["docker", "compose", "exec", "-T", "backend", "python", "manage.py", "shell", "-c", script])
    if any(l.strip() == "NOTFOUND" for l in out.splitlines()):
        raise SystemExit(f"No se encontro ningun AnalysisJob para negocio que matchee '{business}'")
    lines = [l for l in out.splitlines() if "=" in l and not l.startswith("INFO") and "objects imported" not in l]

    data = {"posts": [], "slides": []}
    for line in lines:
        key, _, value = line.partition("=")
        if key == "POST":
            data["posts"].append(value)
        elif key == "SLIDE":
            data["slides"].append(value)
        else:
            data[key] = value
    return data


def fetch_audit_entries(business: str) -> list[dict]:
    """Fuente principal y persistente: logs/llm_audit.jsonl (bind mount,
    sobrevive recreacion de contenedores). Filtra por nombre de negocio ya
    que el archivo no trae job_id ni timestamp por linea."""
    out = run(["docker", "compose", "exec", "-T", "backend", "grep", "-F", business, "/app/logs/llm_audit.jsonl"])
    entries = []
    for line in out.splitlines():
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def fetch_window_logs(start: datetime, end: datetime) -> str | None:
    """Fuente secundaria, best-effort: docker compose logs. Se pierde si el
    contenedor se reinicio (--force-recreate) despues de correr el job."""
    since = start.strftime("%Y-%m-%dT%H:%M:%S")
    until = end.strftime("%Y-%m-%dT%H:%M:%S")
    raw = run(["docker", "compose", "logs", "backend", "rqworker", "--since", since, "--until", until])
    return raw if raw.strip() else None


def filter_to_job_threads(raw_log: str, job_id: str) -> str:
    """Aisla las lineas del/los thread(s) que tocaron este job_id — el pipeline
    corre en varios workers en paralelo (fan-out de imagenes), y puede haber
    OTROS jobs corriendo al mismo tiempo en otros threads que hay que excluir."""
    job_threads: set[tuple[str, str]] = set()
    lines = raw_log.splitlines()

    for line in lines:
        if job_id not in line:
            continue
        m = LOG_LINE_RE.match(line)
        if m:
            job_threads.add((m.group("service"), m.group("thread")))

    if not job_threads:
        return raw_log

    kept = []
    for line in lines:
        if job_id in line:
            kept.append(line)
            continue
        m = LOG_LINE_RE.match(line)
        if m and (m.group("service"), m.group("thread")) in job_threads:
            kept.append(line)
    return "\n".join(kept)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("business", help="Nombre (o parte) del negocio a buscar")
    parser.add_argument("job_id", nargs="?", default=None, help="job_id exacto, si hay ambiguedad")
    parser.add_argument("--out", default=None, help="Ruta del archivo de salida (default: ~/cosmic-exports/)")
    parser.add_argument("--window-minutes", type=int, default=45,
                         help="Minutos maximos de ventana a buscar tras el inicio del job (default 45)")
    args = parser.parse_args()

    print(f"Buscando job para negocio '{args.business}'...")
    job = find_job(args.business, args.job_id)
    job_id = job["JOBID"]
    start = datetime.strptime(job["START"], "%Y-%m-%dT%H:%M:%S")
    end = min(start + timedelta(minutes=args.window_minutes), datetime.now(timezone.utc).replace(tzinfo=None))

    print(f"Job encontrado: {job_id} ({job['BUSINESS']}, {job['STATUS']}, usuario={job['USER']})")

    print("Trayendo detalle de llamadas de IA desde logs/llm_audit.jsonl (persistente)...")
    audit_entries = fetch_audit_entries(job["BUSINESS"])

    print("Intentando traer logs de docker compose (solo si el contenedor no se reinicio desde entonces)...")
    raw_log = fetch_window_logs(start - timedelta(seconds=5), end + timedelta(seconds=5))
    filtered_log = filter_to_job_threads(raw_log, job_id) if raw_log else None

    out_path = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"{job['BUSINESS'].replace(' ', '_')}-{job_id}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w") as f:
        f.write("=" * 70 + "\n")
        f.write("REPORTE DE ANALISIS Y GENERACION — Agente Cosmic\n")
        f.write("=" * 70 + "\n")
        f.write(f"Negocio:      {job['BUSINESS']}\n")
        f.write(f"Job ID:       {job_id}\n")
        f.write(f"Usuario:      {job['USER']}\n")
        f.write(f"Modo:         {job['MODE']}\n")
        f.write(f"Estado final: {job['STATUS']}\n")
        f.write(f"Inicio:       {job['START']} UTC\n")
        f.write("\nPiezas generadas:\n")
        for post in job["posts"]:
            day, fmt, image_url, video_url = post.split("|", 3)
            f.write(f"  Dia {day} ({fmt}):\n")
            if image_url:
                f.write(f"    imagen/poster: {image_url}\n")
            if video_url:
                f.write(f"    video: {video_url}\n")
            for slide in job["slides"]:
                sday, idx, url = slide.split("|", 2)
                if sday == day:
                    f.write(f"    slide {idx}: {url}\n")

        f.write("\n" + "=" * 70 + "\n")
        f.write(f"LLAMADAS DE IA ({len(audit_entries)} encontradas en logs/llm_audit.jsonl)\n")
        f.write("Nota: si este negocio se analizo mas de una vez, puede incluir\n")
        f.write("llamadas de otra corrida (el archivo no guarda timestamp/job_id).\n")
        f.write("=" * 70 + "\n\n")
        for i, entry in enumerate(audit_entries, 1):
            f.write(f"--- Llamada {i}: {entry.get('operation')} "
                     f"(tokens in={entry.get('input_tokens')} out={entry.get('output_tokens')} "
                     f"costo=${entry.get('est_cost_usd')}) ---\n")
            if entry.get("prompt_preview"):
                f.write(f"PROMPT: {entry['prompt_preview']}\n")
            if entry.get("response_preview"):
                f.write(f"RESPUESTA: {entry['response_preview']}\n")
            f.write("\n")

        f.write("=" * 70 + "\n")
        if filtered_log:
            f.write("LOG DE EJECUCION (docker compose logs — RQ, warnings, etc.)\n")
            f.write("=" * 70 + "\n\n")
            f.write(filtered_log)
            f.write("\n")
        else:
            f.write("LOG DE EJECUCION: NO DISPONIBLE\n")
            f.write("=" * 70 + "\n\n")
            f.write("El log de stdout de docker no sobrevive una recreacion de contenedor\n")
            f.write("(docker compose up --force-recreate) posterior a la ejecucion del job.\n")
            f.write("La seccion de 'LLAMADAS DE IA' arriba (logs/llm_audit.jsonl) si es\n")
            f.write("persistente y cubre el detalle de cada llamada igual.\n")

    print(f"\nListo: {out_path}")


if __name__ == "__main__":
    main()
