"""python -m app.cli add-group alt.binaries.example
                  | import-groups [PATTERN] [--min-articles N]   (default: alt.binaries.* with >= 1 article)
                  | create-user NAME [--admin] | list"""
import secrets
import sys

from sqlalchemy import select

from .db import SessionLocal, init_db
from .models import Group, User


def main():
    init_db()
    args = sys.argv[1:] or ["help"]
    cmd, rest = args[0], args[1:]
    with SessionLocal() as db:
        if cmd == "add-group" and rest:
            db.add(Group(name=rest[0].lower()))
            db.commit()
            print("added", rest[0])
        elif cmd == "import-groups":
            from .scanner import HOST, NNTP, PASS, PORT, USE_SSL, USER, import_groups
            minimum = 1
            if "--min-articles" in rest:
                i = rest.index("--min-articles")
                minimum = int(rest[i + 1])
                rest = rest[:i] + rest[i + 2:]
            pattern = rest[0] if rest else "alt.binaries.*"
            if not HOST:
                sys.exit("NNTP_HOST is not set")
            nntp = NNTP(HOST, PORT, USER, PASS, USE_SSL)
            try:
                added, matched = import_groups(db, nntp, pattern, minimum)
            finally:
                nntp.close()
            print(f"{matched} groups match {pattern!r}, {added} new added")
        elif cmd == "create-user" and rest:
            key = secrets.token_hex(16)
            db.add(User(username=rest[0], api_key=key, is_admin="--admin" in rest))
            db.commit()
            print(f"{rest[0]} API key: {key}")
        elif cmd == "list":
            for g in db.scalars(select(Group)):
                print("group", g.name, g.last_article)
            for u in db.scalars(select(User)):
                print("user", u.username, u.api_key, "(admin)" if u.is_admin else "")
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
