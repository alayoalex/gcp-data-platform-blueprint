"""Presentacion de hallazgos.

El formato importa mas de lo que parece. Un gate que escupe 300 lineas de JSON en el
log de CI se ignora; uno que deja un comentario corto y accionable en el merge request
cambia el comportamiento del equipo. Por eso el formato por defecto para CI es
Markdown agrupado por activo, con la recomendacion concreta al lado del problema.

Hay ademas un formato `codequality`, que GitLab entiende de forma nativa: los hallazgos
aparecen en el widget del merge request, anclados al archivo y la linea, y GitLab
distingue solo cuales son nuevos respecto de la rama base. Es mas util que el comentario
para el detalle, y el comentario sigue siendo mejor para el resumen. Se emiten ambos.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path

from faro_governance.models import Assessment, Report, Severity

_ICON = {Severity.BLOCK: "🔴", Severity.WARN: "🟡", Severity.INFO: "🔵"}
_CHECK_LABEL = {
    "naming.table": "Nombre de tabla",
    "naming.column": "Nombre de columna",
    "naming.dataset": "Nombre de dataset",
    "privacy.forbidden": "Dato prohibido",
    "privacy.missing_policy_tag": "Policy tag ausente",
    "privacy.treatment": "Tratamiento insuficiente",
    "privacy.special_category": "Dato sensible",
    "privacy.review": "Revision humana",
    "contract.missing": "Contrato ausente",
    "contract.incomplete": "Contrato incompleto",
    "contract.breaking_change": "Cambio incompatible",
    "contract.type_drift": "Contrato desactualizado",
    "contract.undeclared_column": "Columna fuera de contrato",
    "contract.invalid_grain": "Grano invalido",
    "contract.under_classified": "Clasificacion insuficiente",
}


def to_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def to_markdown(report: Report, *, title: str = "Faro · gate de gobierno de datos") -> str:
    failures = report.failures
    lines = [f"## {title}", ""]

    if not failures:
        lines += [
            (
                f"✅ **{report.assets_evaluated} activos evaluados, "
                f"{len(report.assessments)} verificaciones, sin hallazgos.**"
            ),
            "",
            (
                "Los nombres siguen la convencion, los datos personales estan etiquetados "
                "y los contratos coinciden con el esquema."
            ),
        ]
        return "\n".join(lines)

    blocking = [a for a in failures if a.severity is Severity.BLOCK]
    warnings = [a for a in failures if a.severity is Severity.WARN]
    infos = [a for a in failures if a.severity is Severity.INFO]

    verdict = "🔴 **Bloqueado**" if blocking else "🟡 **Aprobado con observaciones**"
    lines += [
        (
            f"{verdict} — {report.assets_evaluated} activos · "
            f"{len(blocking)} bloqueantes · {len(warnings)} advertencias · {len(infos)} informativos"
        ),
        "",
    ]

    by_asset: dict[str, list[Assessment]] = defaultdict(list)
    for finding in failures:
        by_asset[finding.asset_id].append(finding)

    for asset_id in sorted(by_asset):
        findings = sorted(by_asset[asset_id], key=lambda a: (_severity_rank(a.severity), a.target))
        short = asset_id.replace("bigquery://", "")
        source = findings[0].source_path
        header = f"### `{short}`"
        if source:
            header += f"\n<sub>{source}</sub>"
        lines += [header, ""]
        lines += ["| | Objetivo | Verificacion | Hallazgo | Accion |", "|---|---|---|---|---|"]
        for f in findings:
            action = f.recommendation or "—"
            if f.check.startswith("naming.") and f.recommendation:
                action = f"renombrar a `{f.recommendation}`"
            lines.append(
                f"| {_ICON[f.severity]} | `{f.target}` | {_CHECK_LABEL.get(f.check, f.check)} "
                f"| {_clean(f.message)} | {_clean(action)} |"
            )
        lines.append("")

    if blocking:
        lines += [
            "---",
            "",
            (
                "Los hallazgos 🔴 impiden el merge. Los 🟡 no bloquean pero quedan "
                "registrados en la deuda de gobierno del producto."
            ),
        ]
    return "\n".join(lines)


def to_text(report: Report) -> str:
    lines = [
        f"Activos evaluados : {report.assets_evaluated}",
        f"Verificaciones    : {len(report.assessments)}",
        f"Hallazgos         : {len(report.failures)} ({len(report.blocking)} bloqueantes)",
        "",
    ]
    for f in sorted(report.failures, key=lambda a: (_severity_rank(a.severity), a.asset_id, a.target)):
        lines.append(f"[{f.severity.value:<5}] {f.target}  ({f.check})")
        lines.append(f"         {f.message}")
        if f.recommendation:
            lines.append(f"         -> {f.recommendation}")
    if not report.failures:
        lines.append("Sin hallazgos.")
    return "\n".join(lines)


#: Severidad de Faro -> severidad de GitLab Code Quality.
_GITLAB_SEVERITY = {
    Severity.BLOCK: "blocker",
    Severity.WARN: "major",
    Severity.INFO: "info",
}


def to_codequality(report: Report, *, path_prefix: str = "", repo_root: str | Path = ".") -> str:
    """Emite el reporte en el formato Code Quality que GitLab lee de forma nativa.

    Dos detalles que deciden si la integracion sirve:

    - **El fingerprint tiene que ser estable.** GitLab lo usa para saber que hallazgo ya
      existia y cual introdujo este merge request. Si cambiara entre corridas, cada push
      mostraria todos los hallazgos como nuevos y el widget dejaria de significar algo.
      Por eso se calcula sobre la identidad del hallazgo (activo + objetivo + control) y
      no sobre su mensaje, que puede reformularse.
    - **La ruta es relativa a la raiz del repositorio.** La compilacion de Dataform
      entrega rutas relativas al proyecto Dataform, asi que `path_prefix` las completa.
      Sin eso GitLab no encuentra el archivo y no ancla nada.
    """
    root = Path(repo_root)
    items = []
    for finding in report.failures:
        full_path = _repo_path(finding.source_path, path_prefix, root)
        fingerprint = hashlib.sha256(
            f"{finding.asset_id}|{finding.target}|{finding.check}".encode()
        ).hexdigest()

        description = _clean(finding.message)
        if finding.recommendation:
            description += f" Accion: {_clean(finding.recommendation)}"

        items.append(
            {
                "description": description,
                "check_name": finding.check,
                "fingerprint": fingerprint,
                "severity": _GITLAB_SEVERITY[finding.severity],
                "location": {
                    "path": full_path,
                    "lines": {"begin": _locate(root / full_path, finding.target)},
                },
            }
        )
    return json.dumps(items, indent=2, ensure_ascii=False)


def _repo_path(raw: str | None, prefix: str, root: Path) -> str:
    """Normaliza la ruta de un hallazgo para que GitLab la encuentre.

    Los hallazgos llegan con rutas de dos procedencias distintas: las de la compilacion
    de Dataform son relativas al proyecto Dataform, y las de los contratos ya son
    relativas a la raiz del repositorio. Anteponer el prefijo a las segundas produce
    rutas como `products/x/products/x/contracts/y.yaml`, que no existen.

    La regla: si la ruta ya resuelve desde la raiz, se deja; si no, se le antepone el
    prefijo. Y siempre con barras normales, porque GitLab las espera asi aunque el gate
    haya corrido en Windows.
    """
    if not raw:
        return ""
    path = raw.replace("\\", "/")
    if (root / path).exists():
        return path
    if prefix:
        return f"{prefix.strip('/')}/{path}"
    return path


def _locate(path: Path, target: str) -> int:
    """Busca en que linea del archivo aparece el objetivo del hallazgo.

    GitLab exige un numero de linea. Devolver siempre 1 funciona, pero deja todos los
    hallazgos apilados al principio del archivo, que es justo donde no esta el problema.
    Buscar el nombre de la columna o de la tabla acierta en la gran mayoria de los casos
    y cuesta una lectura de archivo.
    """
    needle = target.rsplit(".", 1)[-1]
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if needle in line:
                return number
    except (OSError, UnicodeDecodeError):
        pass
    return 1


def _severity_rank(severity: Severity) -> int:
    return [Severity.BLOCK, Severity.WARN, Severity.INFO].index(severity)


def _clean(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")
