"""Isolated native development; never starts a Worker or reads the old .env."""

import argparse
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
ENV_FILE = LOCAL / "native.env"
PG_DATA = LOCAL / "postgres-native"
PG_BIN = LOCAL / "tools/Postgres.app/Contents/Versions/17/bin"
PG_PORT = "55432"
API_PORT = "18000"
WEB_PORT = "15173"


def environment(*, testing: bool = False) -> dict[str, str]:
    if not ENV_FILE.is_file() or ENV_FILE.is_symlink():
        raise SystemExit("Initialize the independent native environment first.")
    values = dotenv_values(ENV_FILE)
    target = urlsplit(values.get("PAWE_DATABASE_URL") or "")
    if (
        target.scheme != "postgresql+asyncpg"
        or target.hostname != "127.0.0.1"
        or target.port != int(PG_PORT)
        or target.path != "/pawe_native"
        or target.username != "pawe_native"
        or target.query
        or target.fragment
    ):
        raise SystemExit("Refusing a database outside the isolated native instance.")
    env = {key: value for key, value in os.environ.items() if not key.startswith("PAWE_")}
    env.update({key: value for key, value in values.items() if value is not None})
    env["PAWE_ENV_FILE"] = str(ENV_FILE)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), str(ROOT / "apps/api"), str(ROOT / "services/worker")]
    )
    env["VITE_API_BASE_URL"] = f"http://127.0.0.1:{API_PORT}"
    if testing:
        env["PAWE_DATABASE_URL"] = env["PAWE_DATABASE_URL"].rsplit("/", 1)[0] + "/pawe_native_test"
        env["PAWE_ENV"] = "test"
        env["PAWE_AI_ENABLED"] = "false"
        env["PAWE_OPENAI_API_KEY"] = ""
    return env


def run(arguments: list[str], env: dict[str, str] | None = None) -> None:
    subprocess.run(arguments, cwd=ROOT, env=env, check=True)


def initialize() -> None:
    if not (PG_BIN / "initdb").is_file():
        raise SystemExit("Install verified PostgreSQL 17 binaries under .local/tools first.")
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    if not ENV_FILE.exists():
        if PG_DATA.exists():
            raise SystemExit("Existing data without its config: refuse reinitialization.")
        content = {
            "PAWE_ENV": "development",
            "PAWE_DATABASE_URL": f"postgresql+asyncpg://pawe_native:{secrets.token_urlsafe(32)}@127.0.0.1:{PG_PORT}/pawe_native",
            "PAWE_BOOTSTRAP_ADMIN_USERNAME": "local-admin",
            "PAWE_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "PAWE_AI_CREDENTIAL_ENCRYPTION_KEY": secrets.token_urlsafe(32),
            "PAWE_OPENAI_MODEL": "gpt-6.1-sol",
            "PAWE_AI_ENABLED": "false",
            "PAWE_EXPERIMENT_ACTIVATION_ENABLED": "false",
            "PAWE_ALLOWED_WEB_ORIGINS": f"http://127.0.0.1:{WEB_PORT}",
            "PAWE_SESSION_COOKIE_SECURE": "false",
        }
        with open(ENV_FILE, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
            file.write("".join(f"{key}={value}\n" for key, value in content.items()))
    env = environment()
    if not PG_DATA.exists():
        password = env["PAWE_DATABASE_URL"].split(":", 2)[2].split("@", 1)[0]
        pw_file = LOCAL / "initdb-password"
        with open(pw_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
            file.write(password)
        try:
            run(
                [
                    str(PG_BIN / "initdb"),
                    "-D",
                    str(PG_DATA),
                    "-U",
                    "pawe_native",
                    "--auth-local=trust",
                    "--auth-host=scram-sha-256",
                    f"--pwfile={pw_file}",
                    "--encoding=UTF8",
                    "--locale=C",
                ]
            )
        finally:
            pw_file.unlink()
    start_database()
    # Creation is idempotent; names never come from external input.
    db_env = {**env, "PGPASSWORD": env["PAWE_DATABASE_URL"].split(":", 2)[2].split("@", 1)[0]}
    for name in ["pawe_native", "pawe_native_test"]:
        exists = subprocess.check_output(
            [
                str(PG_BIN / "psql"),
                "-h",
                "127.0.0.1",
                "-p",
                PG_PORT,
                "-U",
                "pawe_native",
                "-d",
                "postgres",
                "-Atc",
                f"SELECT 1 FROM pg_database WHERE datname='{name}'",
            ],
            env=db_env,
            text=True,
        ).strip()
        if not exists:
            run(
                [
                    str(PG_BIN / "createdb"),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    PG_PORT,
                    "-U",
                    "pawe_native",
                    name,
                ],
                db_env,
            )
    for testing in [False, True]:
        run([sys.executable, "-m", "alembic", "upgrade", "head"], environment(testing=testing))
    run([sys.executable, "-m", "pawe_api.auth.bootstrap"], env)
    print("Native environment ready; admin credentials are in .local/native.env (not printed).")


def start_database() -> None:
    status = subprocess.run(
        [str(PG_BIN / "pg_ctl"), "-D", str(PG_DATA), "status"], capture_output=True
    )
    if status.returncode != 0:
        run(
            [
                str(PG_BIN / "pg_ctl"),
                "-D",
                str(PG_DATA),
                "-l",
                str(LOCAL / "postgres-native.log"),
                "-o",
                f"-h 127.0.0.1 -p {PG_PORT} -k {LOCAL}",
                "-w",
                "start",
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "api", "web", "stop-db", "test"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "init":
        initialize()
    elif args.action == "stop-db":
        run([str(PG_BIN / "pg_ctl"), "-D", str(PG_DATA), "-m", "fast", "stop"])
    elif args.action == "api":
        start_database()
        run(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "pawe_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                API_PORT,
                "--no-access-log",
            ],
            environment(),
        )
    elif args.action == "web":
        pnpm = shutil.which("pnpm")
        if pnpm is None:
            raise SystemExit("pnpm and Node.js must be available on PATH.")
        run(
            [pnpm, "--filter", "@pawe/web", "dev", "--port", WEB_PORT, "--strictPort"],
            environment(),
        )
    else:
        run([sys.executable, "-m", "pytest", *args.arguments], environment(testing=True))


if __name__ == "__main__":
    main()
