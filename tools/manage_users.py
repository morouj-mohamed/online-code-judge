#!/usr/bin/env python3
"""
tools/manage_users.py - manage accounts from the command line.

The first admin cannot be created from the website (nobody is admin yet), so
it is created here, on the server:

    python3 tools/manage_users.py create-admin <username>     # asks for a password
    python3 tools/manage_users.py promote <username>           # make an existing user admin
    python3 tools/manage_users.py demote <username>
    python3 tools/manage_users.py set-password <username>
    python3 tools/manage_users.py list

With Docker (while "docker compose up" is running):

    docker compose exec judge python3 tools/manage_users.py create-admin teacher

The password is read with getpass (not echoed, not stored in shell history).
For scripts, JUDGE_ADMIN_PASSWORD may hold it instead.
"""

import getpass
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from judge import auth  # noqa: E402
from judge.db import Database  # noqa: E402


def ask_password(username):
    password = os.environ.get("JUDGE_ADMIN_PASSWORD")
    if password:
        return auth.validate_password(password, username)
    while True:
        first = getpass.getpass("Password: ")
        try:
            auth.validate_password(first, username)
        except auth.ValidationError as exc:
            print(exc)
            continue
        if getpass.getpass("Repeat password: ") == first:
            return first
        print("The two passwords are different, try again.")


def main(argv):
    if len(argv) < 2 or argv[1] not in ("create-admin", "promote", "demote", "set-password", "list"):
        print(__doc__)
        return 2
    command = argv[1]
    db = Database()
    if command == "list":
        users = db.list_users()
        if not users:
            print("No accounts yet.")
        for u in users:
            role = "admin" if u["is_admin"] else "user "
            print(f"{u['id']:>4}  {role}  {u['username']:<24} {u['submissions']:>5} submissions  "
                  f"created {time.strftime('%Y-%m-%d', time.localtime(u['created_at']))}")
        return 0
    if len(argv) != 3:
        print(f"usage: {argv[0]} {command} <username>")
        return 2
    username = argv[2]
    try:
        auth.validate_username(username)
    except auth.ValidationError as exc:
        print(exc)
        return 2
    user = db.get_user_by_name(username)

    if command == "create-admin":
        if user:
            db.set_admin(user["id"], True)
            print(f"'{user['username']}' already exists; it is now an admin.")
            return 0
        try:
            password = ask_password(username)
            db.create_user(username, auth.hash_password(password), is_admin=True)
        except auth.ValidationError as exc:
            print(exc)
            return 2
        except sqlite3.IntegrityError:
            print("That username is taken.")
            return 1
        print(f"Admin '{username}' created. Log in at /login, then open /admin.")
        return 0

    if user is None:
        print(f"No account named '{username}'.")
        return 1
    if command in ("promote", "demote"):
        db.set_admin(user["id"], command == "promote")
        print(f"'{user['username']}' is {'now an admin' if command == 'promote' else 'no longer an admin'}.")
    elif command == "set-password":
        db.set_password(user["id"], auth.hash_password(ask_password(username)))
        print(f"Password changed; '{user['username']}' was logged out everywhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
