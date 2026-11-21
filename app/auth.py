"""One account (username + scrypt hash in the settings table) and the session tokens issued at login.

Set or change it on the server:  docker compose exec api python -m app.auth set-password
"""
import argparse
import getpass
import hashlib
import hmac
import secrets
import sys

from . import db

SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 8


def hash_password(password):
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${key.hex()}"


def verify_password(password, stored):
    try:
        _, n, r, p, salt, key = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(key) // 2)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(got.hex(), key)


def username(conn):
    return db.get_setting(conn, "auth_user")


def has_account(conn):
    return bool(db.get_setting(conn, "auth_hash"))


def set_account(conn, user, password):
    db.set_setting(conn, "auth_user", user)
    db.set_setting(conn, "auth_hash", hash_password(password))


def check_login(conn, user, password):
    stored = db.get_setting(conn, "auth_hash")
    if not stored:
        hash_password(password)
        return False
    ok = verify_password(password, stored)
    return ok and hmac.compare_digest((user or "").encode(), (username(conn) or "").encode())


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn, label):
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions(token_hash, label, created_at) VALUES (?, ?, ?)",
                 (_digest(token), (label or "")[:80], db.now()))
    conn.commit()
    return token


def session_ok(conn, token):
    return bool(token) and conn.execute("SELECT 1 FROM sessions WHERE token_hash=?", (_digest(token),)).fetchone() is not None


def revoke(conn, token):
    conn.execute("DELETE FROM sessions WHERE token_hash=?", (_digest(token),))
    conn.commit()


def revoke_others(conn, keep_token):
    conn.execute("DELETE FROM sessions WHERE token_hash<>?", (_digest(keep_token or ""),))
    conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m app.auth")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("set-password", help="create the account or change its username/password")
    sp.add_argument("--username")
    sub.add_parser("sign-out-all", help="revoke every session and Shortcut token")
    a = ap.parse_args(argv)
    conn = db.open_db()
    if a.cmd == "sign-out-all":
        conn.execute("DELETE FROM sessions")
        conn.commit()
        print("All sessions revoked.")
        return 0
    user = (a.username or input(f"Username [{username(conn) or ''}]: ").strip() or username(conn) or "").strip()
    if not user:
        print("A username is required.", file=sys.stderr)
        return 1
    pw = getpass.getpass("New password: ")
    if len(pw) < MIN_PASSWORD:
        print(f"Use at least {MIN_PASSWORD} characters.", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat it: ") != pw:
        print("The passwords don't match.", file=sys.stderr)
        return 1
    set_account(conn, user, pw)
    conn.execute("DELETE FROM sessions")
    conn.commit()
    print(f"Saved the account for {user}. Existing sessions were signed out.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
