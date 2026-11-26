import argparse
from getpass import getpass

from sqlalchemy.exc import IntegrityError

from app.core.auth import hash_password
from app.db.database import SessionLocal
from app.db.models import User


def main():
    parser = argparse.ArgumentParser(description="Create an API Health Monitor account")
    parser.add_argument("username")
    parser.add_argument("--role", choices=("admin", "viewer"), default="viewer")
    parser.add_argument(
        "--practice-password",
        action="store_true",
        help="Allow a password as short as 6 characters for local practice",
    )
    args = parser.parse_args()
    if not 1 <= len(args.username) <= 64 or not args.username.isprintable() or args.username.strip() != args.username:
        parser.error("Username must be 1–64 printable characters with no surrounding whitespace")

    password = getpass("Password: ")
    minimum_length = 6 if args.practice_password else 12
    if not minimum_length <= len(password) <= 256:
        parser.error(f"Password must contain {minimum_length}–256 characters")
    if getpass("Confirm password: ") != password:
        parser.error("Passwords do not match")

    with SessionLocal() as db:
        db.add(User(username=args.username, password_hash=hash_password(password), role=args.role))
        try:
            db.commit()
        except IntegrityError:
            parser.error("Username already exists")

    print(f"Created {args.role} account {args.username}.")


if __name__ == "__main__":
    main()
