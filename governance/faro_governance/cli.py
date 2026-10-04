"""CLI de Faro. Es la unica superficie que el CI conoce.

    faro check --source dataform --input build/compile.json --contracts ../products/*/contracts
    faro scan  --source information-schema --input columns.json --format md
    faro suggest order_dow --type INT64

Codigos de salida, que son el contrato con el pipeline:

    0  sin hallazgos bloqueantes
    1  hay hallazgos que bloquean el merge
    2  error de invocacion (archivo faltante, formato invalido)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from faro_governance import parser as adapters
from faro_governance.contracts import ContractEngine, load_contracts
from faro_governance.models import Column, DataAsset, Layer, Report, Severity
from faro_governance.naming import NamingEngine
from faro_governance.privacy import PrivacyEngine
from faro_governance.report import to_codequality, to_json, to_markdown, to_text

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_USAGE = 2


class FaroUsageError(Exception):
    """Error de invocacion: entrada faltante o ilegible."""


def run_checks(
    assets: list[DataAsset],
    contracts_dir: Path | None,
    *,
    checks: set[str],
) -> Report:
    report = Report(assets_evaluated=len(assets))
    contracts = load_contracts(contracts_dir) if contracts_dir else {}

    naming = NamingEngine()
    privacy = PrivacyEngine()
    contract_engine = ContractEngine()

    for asset in assets:
        if "naming" in checks:
            report.extend(naming.evaluate(asset))
        if "privacy" in checks:
            report.extend(privacy.evaluate(asset))
        if "contracts" in checks:
            ref = f"{asset.project_id}.{asset.dataset_id}.{asset.name}"
            report.extend(contract_engine.evaluate(asset, contracts.get(ref)))
    return report


def _bigquery_client(endpoint: str | None):
    """Construye el cliente de BigQuery, opcionalmente contra un emulador."""
    from google.cloud import bigquery

    if not endpoint:
        return bigquery.Client()
    from google.api_core.client_options import ClientOptions
    from google.auth.credentials import AnonymousCredentials

    return bigquery.Client(
        credentials=AnonymousCredentials(),
        client_options=ClientOptions(api_endpoint=endpoint),
    )


def _load_assets(args: argparse.Namespace) -> list[DataAsset]:
    if args.source == "bigquery-api":
        if not args.project:
            raise FaroUsageError("faro: --source bigquery-api requiere --project.")
        client = _bigquery_client(getattr(args, "bq_endpoint", None))
        datasets = args.datasets.split(",") if args.datasets else None
        return adapters.parse_bigquery_api(client, args.project, datasets)

    if not args.input:
        raise FaroUsageError("faro: falta --input.")
    path = Path(args.input)
    if not path.exists():
        raise FaroUsageError(f"faro: no existe el archivo de entrada: {path}")

    if args.source == "dataform":
        hints = {}
        if args.contracts:
            hints = adapters.type_hints_from_contracts(load_contracts(args.contracts).values())
        return adapters.parse_dataform_compilation(path, type_hints=hints)
    if args.source == "information-schema":
        return adapters.parse_information_schema(path)
    return adapters.parse_assets_json(path)


def _emit(report: Report, fmt: str, out: Path | None, *, path_prefix: str = "") -> None:
    if fmt == "codequality":
        rendered = to_codequality(report, path_prefix=path_prefix)
    else:
        rendered = {"json": to_json, "md": to_markdown, "text": to_text}[fmt](report)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered, encoding="utf-8")
        print(f"faro: reporte escrito en {out}")
    else:
        print(rendered)


def cmd_check(args: argparse.Namespace) -> int:
    assets = _load_assets(args)
    if args.layer:
        forced = Layer[args.layer.upper()]
        for asset in assets:
            asset.layer = forced

    checks = set(args.checks.split(","))
    contracts_dir = Path(args.contracts) if args.contracts else None
    report = run_checks(assets, contracts_dir, checks=checks)
    _emit(report, args.format, Path(args.out) if args.out else None,
          path_prefix=args.path_prefix or "")

    if args.fail_on == "none":
        return EXIT_OK
    if args.fail_on == "warn":
        offending = [a for a in report.failures if a.severity in (Severity.BLOCK, Severity.WARN)]
        return EXIT_BLOCKED if offending else EXIT_OK
    return EXIT_BLOCKED if report.should_block else EXIT_OK


def cmd_suggest(args: argparse.Namespace) -> int:
    engine = NamingEngine()
    if args.type:
        column = Column(name=args.name, data_type=args.type)
        print(engine.suggest_name(args.name, column=column))
    else:
        layer = Layer[args.layer.upper()] if args.layer else None
        print(engine.suggest_name(args.name, layer=layer))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="faro", description="Gate de gobierno de datos.")
    sub = ap.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Evalua activos contra las politicas.")
    check.add_argument("--input", help="Archivo de entrada. No aplica a --source bigquery-api.")
    check.add_argument(
        "--source",
        default="dataform",
        choices=["dataform", "information-schema", "assets", "bigquery-api"],
        help="Origen de los activos. 'bigquery-api' los lee en vivo, sin archivo.",
    )
    check.add_argument("--project", help="Proyecto GCP a escanear con --source bigquery-api.")
    check.add_argument(
        "--datasets",
        help="Datasets a escanear, separados por coma. Por defecto, todos los del proyecto.",
    )
    check.add_argument(
        "--bq-endpoint",
        help="Endpoint alternativo de la API de BigQuery (para apuntar a un emulador local).",
    )
    check.add_argument("--contracts", help="Directorio con los contratos de datos.")
    check.add_argument(
        "--checks",
        default="naming,privacy,contracts",
        help="Verificaciones a correr, separadas por coma.",
    )
    check.add_argument("--format", default="text", choices=["text", "md", "json", "codequality"])
    check.add_argument(
        "--path-prefix",
        default="",
        help=(
            "Prefijo que antepone a las rutas de los hallazgos para que queden relativas "
            "a la raiz del repositorio. Necesario con --format codequality cuando el "
            "proyecto Dataform no esta en la raiz."
        ),
    )
    check.add_argument("--out", help="Escribir el reporte a un archivo en vez de stdout.")
    check.add_argument("--layer", help="Forzar la capa de todos los activos (para escaneos parciales).")
    check.add_argument(
        "--fail-on",
        default="block",
        choices=["block", "warn", "none"],
        help="Que severidad produce codigo de salida 1.",
    )
    check.set_defaults(func=cmd_check)

    scan = sub.add_parser("scan", help="Alias de check para escanear un warehouse existente.")
    scan.add_argument("--input")
    scan.add_argument(
        "--source",
        default="information-schema",
        choices=["dataform", "information-schema", "assets", "bigquery-api"],
    )
    scan.add_argument("--project")
    scan.add_argument("--datasets")
    scan.add_argument("--bq-endpoint")
    scan.add_argument("--contracts")
    scan.add_argument("--checks", default="naming,privacy")
    scan.add_argument("--format", default="md", choices=["text", "md", "json", "codequality"])
    scan.add_argument("--path-prefix", default="")
    scan.add_argument("--out")
    scan.add_argument("--layer")
    scan.add_argument("--fail-on", default="none", choices=["block", "warn", "none"])
    scan.set_defaults(func=cmd_check)

    suggest = sub.add_parser("suggest", help="Propone un nombre conforme.")
    suggest.add_argument("name")
    suggest.add_argument("--type", help="Tipo BigQuery de la columna (activa la regla de categoria).")
    suggest.add_argument("--layer", help="Capa del activo (activa la regla de prefijo).")
    suggest.set_defaults(func=cmd_suggest)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except FaroUsageError as exc:
        print(exc, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
