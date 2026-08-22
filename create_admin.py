"""
Create or promote an administrator account.

Run this once after setting up the database (it is the only way to mint the
first admin — there is deliberately no self-service admin signup).

    python create_admin.py                      # interactive prompts
    python create_admin.py admin@college.edu    # promote an existing user

Passwords are read via getpass, so they never appear in your shell history.
"""
import getpass
import sys

from app.database import SessionLocal, init_db
from app.models import User
from app.security import hash_password


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        email = sys.argv[1] if len(sys.argv) > 1 else input("Admin email: ").strip()
        if not email:
            print("Email is required.")
            return 1

        existing = db.query(User).filter(User.email == email).first()

        if existing:
            if existing.is_admin:
                print(f"'{email}' is already an administrator.")
                return 0
            existing.is_admin = True
            existing.is_active = True
            db.commit()
            print(f"Promoted existing user '{email}' to administrator.")
            return 0

        name = input("Full name: ").strip() or "Administrator"
        password = getpass.getpass("Password (min 8 chars): ")
        if len(password) < 8:
            print("Password must be at least 8 characters.")
            return 1
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            return 1

        db.add(
            User(
                name=name,
                email=email,
                password_hash=hash_password(password),
                is_admin=True,
                is_active=True,
            )
        )
        db.commit()
        print(f"Created administrator '{email}'. Sign in, then open /admin.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
