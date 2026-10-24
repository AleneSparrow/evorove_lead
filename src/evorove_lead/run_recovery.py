"""Operator-only interruption, never checkpoint resume or automatic retry."""
import argparse
import json
from evorove_lead.searches import search_targets_from_env
from evorove_lead.run_ownership import LeaseStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-id", required=True)
    parser.add_argument("--legacy-workers-stopped", action="store_true",
                        help="Confirm all pre-heartbeat workers have been stopped; required for legacy running records")
    args = parser.parse_args(argv)
    targets = search_targets_from_env()
    db = getattr(targets, "database_engine", None)
    if db is None:
        print(json.dumps({"status": "refused", "stop_reason": "persistent_database_required"}))
        return 2
    changed = LeaseStore(db).recover(args.business_id, legacy_workers_stopped=args.legacy_workers_stopped)
    print(json.dumps({"business_id": args.business_id, "recovered": changed,
                      "status": "interrupted" if changed else "unchanged", "restarted": False}))
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
