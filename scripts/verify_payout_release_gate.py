#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ROOT.parent

REPOS = {
    "shkeeper": ROOT,
    "ethereum": PROJECTS / "ethereum-shkeeper",
    "ton": PROJECTS / "ton-shkeeper",
    "tron": PROJECTS / "tron-shkeeper",
    "helm": PROJECTS / "shkeeper-helm-charts",
}

PRODUCT_RUNTIME_NAME_TERMS = (
    "gr" "ither",
    "Gr" "ither",
    "GR" "ITHER",
    "find_" "gr" "ither",
)
PRODUCT_RUNTIME_NAME_PATTERN = "|".join(PRODUCT_RUNTIME_NAME_TERMS)
AMBIGUOUS_WORKER_LIMIT_ENV_PATTERN = (
    "PAYOUT_EXECUTION_RECONCILER_LIMIT|PAYOUT_CALLBACK_DISPATCHER_LIMIT"
)
PAYOUT_BUSINESS_POLICY_TERMS = (
    r"\bdaily_limit\b",
    r"\bdaily_cap\b",
    r"\bdaily_payout_limit\b",
    r"\bdaily_withdrawal_limit\b",
    r"\bday_limit\b",
    r"\bmin_single\b",
    r"\bmax_single\b",
    r"\bmin_amount\b",
    r"\bmax_amount\b",
    r"\bminimum_amount\b",
    r"\bmaximum_amount\b",
    r"\bmin_payout\b",
    r"\bmax_payout\b",
    r"\bpayout_min\b",
    r"\bpayout_max\b",
    r"\bwithdrawal_min\b",
    r"\bwithdrawal_max\b",
    r"\bwithdrawal_limit\b",
    r"\bwithdrawal_policy\b",
    r"\btier_limit\b",
    r"\bcustomer_tier\b",
    r"\bkyc_level\b",
    r"\bbusiness_policy\b",
    r"\bcustomer_limit\b",
    r"\bper_day\b",
)
PAYOUT_BUSINESS_POLICY_PATTERN = "|".join(PAYOUT_BUSINESS_POLICY_TERMS)
SHKEEPER_PAYOUT_EXECUTION_POLICY_SCAN_PATHS = [
    "shkeeper/services/payout_*.py",
    "shkeeper/api_v1.py",
    "migrations/versions/20260603_payout_execution_foundation.py",
]
SIDECAR_PAYOUT_EXECUTION_POLICY_SCAN_PATHS = [
    "app/payout_*.py",
    "app/api/payout.py",
    "app/models.py",
]
IMAGE_REPOSITORIES = {
    "shkeeper": "ghcr.io/nilof470/shkeeper.io",
    "tron": "ghcr.io/nilof470/tron-shkeeper",
    "ton": "ghcr.io/nilof470/ton-shkeeper",
    "ethereum": "ghcr.io/nilof470/ethereum-shkeeper",
}
PRODUCTION_OVERLAY_IMAGES = (
    ("values-prod-tron-payout.yaml", ("shkeeper", "tron")),
    ("values-prod-ton-payout.yaml", ("shkeeper", "ton")),
    ("values-prod-eth-payout.yaml", ("shkeeper", "ethereum")),
)
PRODUCTION_OVERLAY_IMAGE_FIELDS = {
    "shkeeper": ("shkeeper",),
    "tron": ("tron_shkeeper",),
    "ton": ("ton_shkeeper",),
    "ethereum": ("ethereum_shkeeper",),
}


class GateFailure(RuntimeError):
    pass


def env_with(**items):
    env = os.environ.copy()
    env.update({key: str(value) for key, value in items.items()})
    return env


def require_paths():
    missing = [f"{name}: {path}" for name, path in REPOS.items() if not path.exists()]
    if missing:
        raise GateFailure("Missing required checkout(s):\n" + "\n".join(missing))


def run(label, command, *, cwd, env=None):
    print(f"\n==> {label}")
    print(f"$ {' '.join(command)}")
    result = subprocess.run(command, cwd=cwd, env=env, text=True)
    if result.returncode != 0:
        raise GateFailure(f"{label} failed with exit code {result.returncode}")


def run_capture(label, command, *, cwd):
    print(f"\n==> {label}")
    print(f"$ {' '.join(command)}")
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        raise GateFailure(f"{label} failed with exit code {result.returncode}")
    return result.stdout


def run_no_matches(label, pattern, *, cwd, paths):
    existing_paths = []
    for item in paths:
        matches = sorted(cwd.glob(item)) if any(ch in item for ch in "*?[]") else [cwd / item]
        existing_paths.extend(str(path.relative_to(cwd)) for path in matches if path.exists())
    if not existing_paths:
        raise GateFailure(f"{label} has no existing paths to scan")

    command = ["rg", "-n", pattern, *existing_paths]
    print(f"\n==> {label}")
    print(f"$ {' '.join(command)}")
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode == 0:
        print(result.stdout, end="")
        raise GateFailure(f"{label} found forbidden matches")
    if result.returncode == 1:
        print("No matches.")
        return
    print(result.stdout, end="")
    raise GateFailure(f"{label} failed with exit code {result.returncode}")


def require_clean_worktrees():
    dirty = []
    for name, cwd in REPOS.items():
        output = run_capture(
            f"{name} clean worktree",
            ["git", "status", "--porcelain"],
            cwd=cwd,
        )
        if output.strip():
            dirty.append(f"{name} worktree is dirty:\n{output.rstrip()}")
    if dirty:
        raise GateFailure(
            "Commit or intentionally exclude changes before release:\n"
            + "\n\n".join(dirty)
        )


def git_short_head(cwd):
    return run_capture(
        f"{cwd.name} short git tag",
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=cwd,
    ).strip()


def validate_production_overlay_image_tags():
    tags = {
        name: git_short_head(cwd)
        for name, cwd in REPOS.items()
        if name in IMAGE_REPOSITORIES
    }
    env_dir = REPOS["helm"] / "charts" / "shkeeper" / "environments"
    missing = []
    for filename, projects in PRODUCTION_OVERLAY_IMAGES:
        path = env_dir / filename
        if not path.exists():
            missing.append(f"{filename}: file is missing")
            continue
        text = path.read_text(encoding="utf-8")
        for project in projects:
            expected = f"{IMAGE_REPOSITORIES[project]}:{tags[project]}"
            actual_values = image_values_for_sections(
                text, PRODUCTION_OVERLAY_IMAGE_FIELDS[project]
            )
            if expected not in actual_values:
                if actual_values:
                    missing.append(
                        f"{filename}: expected {expected}, found {', '.join(actual_values)}"
                    )
                else:
                    sections = ", ".join(PRODUCTION_OVERLAY_IMAGE_FIELDS[project])
                    missing.append(f"{filename}: missing image field in {sections}")
    if missing:
        raise GateFailure(
            "Production payout overlay image tags do not match current clean commits:\n"
            + "\n".join(missing)
        )


def image_values_for_sections(text, sections):
    values = []
    for section in sections:
        pattern = re.compile(
            rf"(?m)^{re.escape(section)}:\n"
            rf"(?:^[ ]+[^\n]*\n)*?"
            rf"^[ ]+image:[ ]*([^\n#]+)"
        )
        for match in pattern.finditer(text):
            values.append(match.group(1).strip().strip("'\""))
    return values


def validate_openapi_json():
    print("\n==> SHKeeper OpenAPI JSON parses")
    with (ROOT / "docs" / "openapi-3.json").open("r", encoding="utf-8") as handle:
        json.load(handle)
    print("docs/openapi-3.json OK")


def test_commands():
    tron_python = Path("/tmp/tron-shkeeper-py312-venv/bin/python")
    if not tron_python.exists():
        tron_python = REPOS["tron"] / ".venv" / "bin" / "python"
    return [
        (
            "SHKeeper full unittest suite",
            [".venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"],
            REPOS["shkeeper"],
            env_with(PYTHONDONTWRITEBYTECODE="1"),
        ),
        (
            "Ethereum sidecar full unittest suite",
            [".venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"],
            REPOS["ethereum"],
            env_with(PYTHONDONTWRITEBYTECODE="1"),
        ),
        (
            "TON sidecar full unittest suite",
            [".venv/bin/python", "-m", "unittest", "discover", "-s", "tests", "-v"],
            REPOS["ton"],
            env_with(PYTHONDONTWRITEBYTECODE="1"),
        ),
        (
            "TRON sidecar full unittest suite",
            [str(tron_python), "-m", "unittest", "discover", "-s", "tests", "-v"],
            REPOS["tron"],
            env_with(PYTHONDONTWRITEBYTECODE="1"),
        ),
        (
            "SHKeeper-to-sidecar payout e2e suite",
            [".venv/bin/python", "scripts/verify_payout_sidecar_e2e.py"],
            REPOS["shkeeper"],
            env_with(PYTHONDONTWRITEBYTECODE="1"),
        ),
        (
            "Helm chart unittest suite",
            ["python3", "-m", "unittest", "tests/test_shkeeper_fork_chart.py", "-v"],
            REPOS["helm"],
            None,
        ),
    ]


def compile_commands():
    tron_python = Path("/tmp/tron-shkeeper-py312-venv/bin/python")
    if not tron_python.exists():
        tron_python = REPOS["tron"] / ".venv" / "bin" / "python"
    return [
        (
            "SHKeeper compileall",
            [".venv/bin/python", "-m", "compileall", "-q", "shkeeper", "tests"],
            REPOS["shkeeper"],
            env_with(PYTHONPYCACHEPREFIX="/private/tmp/shkeeper-pycache"),
        ),
        (
            "Ethereum sidecar compileall",
            [".venv/bin/python", "-m", "compileall", "-q", "app", "tests"],
            REPOS["ethereum"],
            env_with(PYTHONPYCACHEPREFIX="/private/tmp/ethereum-shkeeper-pycache"),
        ),
        (
            "TON sidecar compileall",
            [".venv/bin/python", "-m", "compileall", "-q", "app", "tests"],
            REPOS["ton"],
            env_with(PYTHONPYCACHEPREFIX="/private/tmp/ton-shkeeper-pycache"),
        ),
        (
            "TRON sidecar compileall",
            [str(tron_python), "-m", "compileall", "-q", "app", "tests"],
            REPOS["tron"],
            env_with(PYTHONPYCACHEPREFIX="/private/tmp/tron-shkeeper-pycache"),
        ),
    ]


def diff_check_commands():
    return [
        (f"{name} git diff --check", ["git", "diff", "--check"], cwd, None)
        for name, cwd in REPOS.items()
    ]


def openapi_check_commands():
    return [
        (
            "SHKeeper OpenAPI artifact is up to date",
            [".venv/bin/python", "scripts/export_openapi.py", "--check"],
            REPOS["shkeeper"],
            env_with(PYTHONDONTWRITEBYTECODE="1"),
        )
    ]


def run_release_gate(args):
    require_paths()
    if args.list:
        for label, command, cwd, _ in (
            test_commands()
            + compile_commands()
            + openapi_check_commands()
            + diff_check_commands()
            + [("Helm lint", ["helm", "lint", "charts/shkeeper"], REPOS["helm"], None)]
        ):
            print(f"{label}: ({cwd}) {' '.join(command)}")
        print("Boundary checks: payout execution/routing allowlist tests, business-policy term scans, and product-specific runtime name scans")
        print(
            "Clean release scans: production payout overlay image fields match current git commits"
        )
        return

    if args.require_clean:
        require_clean_worktrees()
        validate_production_overlay_image_tags()

    validate_openapi_json()

    for label, command, cwd, env in test_commands():
        run(label, command, cwd=cwd, env=env)
    for label, command, cwd, env in compile_commands():
        run(label, command, cwd=cwd, env=env)
    for label, command, cwd, env in openapi_check_commands():
        run(label, command, cwd=cwd, env=env)
    for label, command, cwd, env in diff_check_commands():
        run(label, command, cwd=cwd, env=env)

    run("Helm lint", ["helm", "lint", "charts/shkeeper"], cwd=REPOS["helm"])

    run_no_matches(
        "SHKeeper runtime has no product-specific names",
        PRODUCT_RUNTIME_NAME_PATTERN,
        cwd=REPOS["shkeeper"],
        paths=["shkeeper", "migrations", "scripts", "docs/openapi-3.json"],
    )
    for name in ("ethereum", "ton", "tron"):
        run_no_matches(
            f"{name} sidecar runtime has no product-specific names",
            PRODUCT_RUNTIME_NAME_PATTERN,
            cwd=REPOS[name],
            paths=["app"],
        )
    run_no_matches(
        "Helm templates have no product-specific runtime names",
        PRODUCT_RUNTIME_NAME_PATTERN,
        cwd=REPOS["helm"],
        paths=["charts/shkeeper/templates", "charts/shkeeper/values.yaml"],
    )
    run_no_matches(
        "SHKeeper workers use batch size naming instead of limit env names",
        AMBIGUOUS_WORKER_LIMIT_ENV_PATTERN,
        cwd=REPOS["shkeeper"],
        paths=["shkeeper/__init__.py", "docs/DEPLOYMENT.md"],
    )
    run_no_matches(
        "Helm workers use batch size naming instead of limit env names",
        AMBIGUOUS_WORKER_LIMIT_ENV_PATTERN,
        cwd=REPOS["helm"],
        paths=[
            "charts/shkeeper/templates/deployments/shkeeper-payout-workers.yaml",
            "tests/test_shkeeper_fork_chart.py",
        ],
    )
    run_no_matches(
        "SHKeeper payout execution runtime has no customer business-policy fields",
        PAYOUT_BUSINESS_POLICY_PATTERN,
        cwd=REPOS["shkeeper"],
        paths=SHKEEPER_PAYOUT_EXECUTION_POLICY_SCAN_PATHS,
    )
    for name in ("ethereum", "ton", "tron"):
        run_no_matches(
            f"{name} payout execution runtime has no customer business-policy fields",
            PAYOUT_BUSINESS_POLICY_PATTERN,
            cwd=REPOS[name],
            paths=SIDECAR_PAYOUT_EXECUTION_POLICY_SCAN_PATHS,
        )

    print("\nPayout release gate passed.")


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Run the local SHKeeper USDT payout release gate across all payout repos."
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Fail if any participating repo has uncommitted changes. Use before publishing images.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the commands and scans without running them.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run_release_gate(args)
    except GateFailure as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
