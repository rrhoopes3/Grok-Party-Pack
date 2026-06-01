"""Minimal CLI for Surgeon (standalone).

This is intentionally small in the first extraction pass.
The real interface is the Python API in engine.py + the web UI.
"""

import argparse
import sys

from surgeon import check_dependencies, scan_model, operate, list_operations


def main():
    parser = argparse.ArgumentParser(prog="surgeon", description="Model surgery via OBLITERATUS")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # check
    p = sub.add_parser("check", help="Check dependencies and OBLITERATUS location")
    p.add_argument("--obliteratus-path", help="Path to OBLITERATUS-main checkout")

    # scan
    p = sub.add_parser("scan", help="Scan a model's refusal geometry (no modification)")
    p.add_argument("model")
    p.add_argument("--device", default="auto")
    p.add_argument("--dtype", default="float16")
    p.add_argument("--obliteratus-path")

    # operate
    p = sub.add_parser("operate", help="Run full abliteration on a model")
    p.add_argument("model")
    p.add_argument("--method", default="advanced")
    p.add_argument("--device", default="auto")
    p.add_argument("--dtype", default="float16")
    p.add_argument("--obliteratus-path")

    # list
    sub.add_parser("list", help="List previous operations")

    args = parser.parse_args()

    if args.cmd == "check":
        print(check_dependencies(args.obliteratus_path))
        return

    if args.cmd == "list":
        ops = list_operations()
        for o in ops:
            print(f"{o['id']}  {o['model_name']}  {o['method']}  {o['status']}")
        return

    if args.cmd == "scan":
        result = scan_model(
            args.model,
            device=args.device,
            dtype=args.dtype,
            obliteratus_path=args.obliteratus_path,
        )
        print(result.model_dump_json(indent=2))
        return

    if args.cmd == "operate":
        record = operate(
            model_name=args.model,
            method=args.method,
            device=args.device,
            dtype=args.dtype,
            obliteratus_path=args.obliteratus_path,
        )
        print(f"Done. Saved to: {record.output_path}")
        print(f"Operation ID: {record.id}")
        return


if __name__ == "__main__":
    main()
