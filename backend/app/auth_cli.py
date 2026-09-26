"""Phase 12: operator accounts for this single-workspace app (no registration).

    python -m app.auth_cli create-user <username> [--role operator|viewer]
    python -m app.auth_cli set-password <username>
    python -m app.auth_cli disable <username> | enable <username>
    python -m app.auth_cli revoke-sessions <username>
    python -m app.auth_cli list

The password is read from a hidden prompt (asked twice), or from the
environment variable GTMFLOW_NEW_PASSWORD for scripted setup. It is never
echoed or logged. Disabling a user or changing a password revokes their
sessions. Uses DATABASE_URL like the API.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import select

from app.core.actor import Actor, set_actor
from app.core.database import get_sessionmaker
from app.models.auth import ROLES, User
from app.services import auth as auth_service


def _password() -> str:
    env = os.environ.get("GTMFLOW_NEW_PASSWORD")
    if env:
        return env
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.auth_cli")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create-user")
    c.add_argument("username")
    c.add_argument("--role", choices=ROLES, default="operator")
    for name in ("set-password", "disable", "enable", "revoke-sessions"):
        sub.add_parser(name).add_argument("username")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    set_actor(Actor(label="cli:auth"))
    session = get_sessionmaker()()
    try:
        if args.command == "list":
            for user in session.scalars(select(User).order_by(User.username)):
                live = sum(1 for s in user.sessions if s.revoked_at is None)
                print(f"{user.username}\trole={user.role}\tactive={user.is_active}\tsessions_not_revoked={live}")
            return 0
        if args.command == "create-user":
            try:
                user = auth_service.create_user(session, args.username, _password(), args.role)
            except ValueError as error:
                sys.exit(str(error))
            session.commit()
            print(f"created {user.username} (role {user.role})")
            return 0
        user = session.scalar(select(User).where(User.username == auth_service.normalize_username(args.username)))
        if user is None:
            sys.exit("No such user.")
        if args.command == "set-password":
            try:
                auth_service.set_password(session, user, _password())
            except ValueError as error:
                sys.exit(str(error))
        elif args.command in ("disable", "enable"):
            auth_service.set_active(session, user, args.command == "enable")
        elif args.command == "revoke-sessions":
            count = auth_service.revoke_user_sessions(session, user.id)
            print(f"revoked {count} session(s)")
        session.commit()
        print(f"{args.command}: {user.username} ok")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
