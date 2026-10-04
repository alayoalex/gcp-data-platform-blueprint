"""Publica el reporte del gate como una nota en el merge request, actualizandola.

Por que no crear una nota nueva cada vez: un merge request con cinco iteraciones
terminaria con cinco reportes contradictorios y nadie los lee. La nota se identifica por
un marcador HTML invisible al principio del cuerpo; si ya existe, se actualiza.

Solo usa la biblioteca estandar, a proposito: el job del gate no deberia necesitar
instalar nada mas que Faro para poder comentar.

Variables de entorno (las tres primeras las inyecta GitLab):

    CI_API_V4_URL          https://gitlab.com/api/v4
    CI_PROJECT_ID          id numerico del proyecto
    CI_MERGE_REQUEST_IID   iid del merge request
    GITLAB_API_TOKEN       token con scope `api`

Sobre el token: `CI_JOB_TOKEN` NO sirve para escribir notas en la mayoria de las
instalaciones de GitLab. Hay que crear un project access token con scope `api` y rol
Reporter, y guardarlo como variable de CI enmascarada y protegida.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

MARKER = "<!-- faro-gate -->"


def _request(url: str, token: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("PRIVATE-TOKEN", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read().decode()
    return json.loads(body) if body else None


def _existing_note_id(base: str, token: str) -> int | None:
    """Busca la nota del gate entre las del merge request.

    Se pagina porque un merge request activo acumula notas rapido y la del gate puede
    quedar fuera de la primera pagina.
    """
    page = 1
    while page <= 10:
        notes = _request(f"{base}/notes?per_page=100&page={page}", token) or []
        for note in notes:
            if MARKER in (note.get("body") or ""):
                return int(note["id"])
        if len(notes) < 100:
            return None
        page += 1
    return None


def main() -> int:
    api = os.environ.get("CI_API_V4_URL")
    project = os.environ.get("CI_PROJECT_ID")
    mr_iid = os.environ.get("CI_MERGE_REQUEST_IID")
    token = os.environ.get("GITLAB_API_TOKEN")
    report_path = sys.argv[1] if len(sys.argv) > 1 else "gate-report.md"

    if not all([api, project, mr_iid, token]):
        # No es un error: el gate tambien corre en pipelines de rama, donde no hay
        # merge request al que comentar. Fallar aqui haria rojo un pipeline sano.
        print("faro: sin contexto de merge request o sin token; no se publica nota.")
        return 0

    try:
        body = MARKER + "\n\n" + Path(report_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"faro: no se pudo leer el reporte ({exc}).", file=sys.stderr)
        return 1

    base = f"{api}/projects/{project}/merge_requests/{mr_iid}"

    try:
        note_id = _existing_note_id(base, token)
        if note_id is None:
            _request(f"{base}/notes", token, method="POST", payload={"body": body})
            print("faro: nota publicada en el merge request.")
        else:
            _request(f"{base}/notes/{note_id}", token, method="PUT", payload={"body": body})
            print(f"faro: nota {note_id} actualizada.")
    except urllib.error.HTTPError as exc:
        # Que falle el comentario no debe bloquear el pipeline: el veredicto del gate ya
        # lo entrega el codigo de salida de `faro check` y el reporte de Code Quality.
        # Un token vencido no es razon para impedir un merge correcto.
        print(f"faro: no se pudo publicar la nota ({exc.code} {exc.reason}).", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
