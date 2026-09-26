"""실행: python -m mdm [--config config/sources.toml] [--db output/mdm.sqlite] [--out output]"""
import argparse
import logging
import sys
from pathlib import Path

from .pipeline import run

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    p = argparse.ArgumentParser(description="이종 시스템 거래처 마스터 데이터 통합")
    p.add_argument("--config", default=ROOT / "config" / "sources.toml")
    p.add_argument("--db", default=ROOT / "output" / "mdm.sqlite")
    p.add_argument("--out", default=ROOT / "output")
    args = p.parse_args(argv)

    Path(args.out).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(Path(args.out) / "run.log", encoding="utf-8")]
    for h in handlers:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=handlers)

    run(args.config, args.db, args.out)


if __name__ == "__main__":
    main()
