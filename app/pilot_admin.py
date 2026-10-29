"""Administrator CLI. Access is controlled by access to the server shell."""
from __future__ import annotations
import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "app"))
load_dotenv(PROJECT / ".env", override=False)
from pilot.store import db, initialize, principal, uid


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("grant", "revoke"):
        p = sub.add_parser(command)
        p.add_argument("--issuer", required=True)
        p.add_argument("--subject", required=True)
        p.add_argument("--workspace", required=command == "revoke")
        if command == "grant":
            p.add_argument("--name", default="Private review workspace")
    sub.add_parser("list-workspaces")
    recover = sub.add_parser("recover-interrupted")
    recover.add_argument("--workers-stopped", action="store_true", required=True)
    args = parser.parse_args()
    initialize()
    with db() as c:
        if args.command == "grant":
            workspace = uid(args.workspace) if args.workspace else uuid.uuid4().hex
            if args.workspace and not c.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace,)).fetchone():
                raise SystemExit("Workspace does not exist.")
            c.execute("INSERT OR IGNORE INTO workspaces VALUES(?,?)", (workspace, args.name[:120]))
            c.execute("INSERT OR IGNORE INTO members VALUES(?,?)", (principal(args.issuer, args.subject), workspace))
            print("Workspace:", workspace)
        elif args.command == "revoke":
            who = principal(args.issuer, args.subject)
            c.execute("DELETE FROM members WHERE principal=? AND workspace=?", (who, uid(args.workspace)))
            print("Access revoked. Pending actions recheck membership server-side.")
        elif args.command == "list-workspaces":
            print(json.dumps([dict(r) for r in c.execute("SELECT * FROM workspaces")], indent=2))
        elif args.command == "recover-interrupted":
            c.execute("UPDATE jobs SET status='paused',run_token=NULL,stage='Interrupted. Explicit retry required.',updated=? WHERE status='running'", (time.time(),))
            print("Interrupted jobs marked paused. Start the worker and resume them explicitly.")


if __name__ == "__main__":
    main()

# """Administrator CLI. Access is controlled by access to the server shell."""
# from __future__ import annotations
# import argparse
# import json
# import sys
# import time
# import uuid
# from pathlib import Path
# from dotenv import load_dotenv
# PROJECT = Path(__file__).resolve().parents[1]
# sys.path.insert(0, str(PROJECT / "app"))
# load_dotenv(PROJECT / ".env", override=False)
# from pilot.store import db, initialize, principal, uid


# def main():
#     parser = argparse.ArgumentParser()
#     sub = parser.add_subparsers(dest="command", required=True)
#     for command in ("grant", "revoke"):
#         p = sub.add_parser(command)
#         p.add_argument("--issuer", required=True)
#         p.add_argument("--subject", required=True)
#         p.add_argument("--workspace", required=command == "revoke")
#         if command == "grant":
#             p.add_argument("--name", default="Private review workspace")
#     sub.add_parser("list-workspaces")
#     recover = sub.add_parser("recover-interrupted")
#     recover.add_argument("--workers-stopped", action="store_true", required=True)
#     args = parser.parse_args()
#     initialize()
#     with db() as c:
#         if args.command == "grant":
#             workspace = uid(args.workspace) if args.workspace else uuid.uuid4().hex
#             if args.workspace and not c.execute("SELECT 1 FROM workspaces WHERE id=?", (workspace,)).fetchone():
#                 raise SystemExit("Workspace does not exist.")
#             c.execute("INSERT OR IGNORE INTO workspaces VALUES(?,?)", (workspace, args.name[:120]))
#             c.execute("INSERT OR IGNORE INTO members VALUES(?,?)", (principal(args.issuer, args.subject), workspace))
#             print("Workspace:", workspace)
#         elif args.command == "revoke":
#             who = principal(args.issuer, args.subject)
#             c.execute("DELETE FROM members WHERE principal=? AND workspace=?", (who, uid(args.workspace)))
#             print("Access revoked. Pending actions recheck membership server-side.")
#         elif args.command == "list-workspaces":
#             print(json.dumps([dict(r) for r in c.execute("SELECT * FROM workspaces")], indent=2))
#         elif args.command == "recover-interrupted":
#             c.execute("UPDATE jobs SET status='paused',run_token=NULL,stage='Interrupted. Explicit retry required.',updated=? WHERE status='running'", (time.time(),))
#             print("Interrupted jobs marked paused. Start the worker and resume them explicitly.")


# if __name__ == "__main__":
#     main()
