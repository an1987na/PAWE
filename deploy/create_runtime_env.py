"""Create a protected first-deployment environment; never overwrite or print secrets."""

import argparse
import os
import re
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    for name in ("postgres", "api", "worker", "web"):
        parser.add_argument(f"--{name}-image", required=True)
    args = parser.parse_args()
    values = {
        "PAWE_ENV": "production",
        "PAWE_ENV_FILE": "/mnt/media/projects/PAWE/config/runtime.env",
        "PAWE_COMPOSE_FILE": "deploy/compose.tunnel.yaml",
        "PAWE_STORAGE_MOUNT": "/mnt/media",
        "PAWE_DATA_DIR": "/mnt/media/projects/PAWE/data",
        "PAWE_BACKUP_DIR": "/mnt/media/projects/PAWE/backups",
        "PAWE_DOCKER_SUBNET": "172.30.80.0/24",
        "PAWE_POSTGRES_PASSWORD": secrets.token_urlsafe(36),
        "PAWE_AI_CREDENTIAL_ENCRYPTION_KEY": secrets.token_urlsafe(48),
        "PAWE_ALLOWED_WEB_ORIGINS": "https://pawe.foxerlove.cn",
        "PAWE_SESSION_COOKIE_SECURE": "true",
        "PAWE_AI_ENABLED": "false",
        "PAWE_EXPERIMENT_ACTIVATION_ENABLED": "false",
    }
    for name in ("postgres", "api", "worker", "web"):
        image = getattr(args, f"{name}_image")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
            parser.error(f"{name} image must be a full immutable image ID")
        values[f"PAWE_{name.upper()}_IMAGE"] = image
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")
    print("Protected environment created; secret values were not printed.")


if __name__ == "__main__":
    main()
