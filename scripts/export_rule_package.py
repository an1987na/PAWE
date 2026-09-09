"""Explicit, offline import boundary; the PAWE service never reads legacy folders."""

import argparse
from pathlib import Path

from pawe_api.rules.packages import RuleDocument, RulePackage

SOURCE_FILES = ("ff_rules.md", "rule_playbook.md", "rule_data_contract.md", "rule_experiment.md")


def export_package(source: Path, version: str) -> RulePackage:
    documents = []
    for name in SOURCE_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 512_000:
            raise ValueError(f"source must be a bounded regular file: {name}")
        documents.append(RuleDocument(name=name, content=path.read_text(encoding="utf-8")))
    return RulePackage(version=version, engine="pick-weekly-v9-candidate", documents=documents)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = export_package(args.source, args.version)
    # Never overwrite a prior export or a frozen source document.
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(package.model_dump_json(indent=2))
        stream.write("\n")
    print(f"Exported {package.version}; sha256={package.content_hash()}; formal_activation=false")


if __name__ == "__main__":
    main()
