
import secrets
import string
from pathlib import Path
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import generate_password_hash

from app import app, db, User


def generate_username():
    """Generate a unique Super Admin username."""
    base_username = f"superadmin_{datetime.now().year}"
    username = base_username
    counter = 1

    while User.query.filter_by(username=username).first():
        username = f"{base_username}_{counter}"
        counter += 1

    return username


def generate_password(length=20):
    """Generate a strong random password."""
    if length < 12:
        raise ValueError("Password length must be at least 12 characters.")

    characters = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"

    while True:
        password = "".join(
            secrets.choice(characters) for _ in range(length)
        )

        if (
            any(c.islower() for c in password)
            and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password)
            and any(c in "!@#$%^&*()-_=+" for c in password)
        ):
            return password


def main():
    with app.app_context():

        # Prevent duplicate Super Admin accounts.
        existing_admin = User.query.filter_by(
            role="super_admin"
        ).first()

        if existing_admin:
            print("A Super Admin account already exists.")
            print("No new account was created.")
            return

        # Generate login credentials automatically.
        username = generate_username()
        password = generate_password()

        super_admin = User(
            username=username,
            password=generate_password_hash(password),
            role="super_admin",
            school_id=None
        )

        try:
            db.session.add(super_admin)
            db.session.commit()

        except SQLAlchemyError:
            db.session.rollback()
            app.logger.exception(
                "Failed to create the Super Admin account."
            )
            raise

        # Save credentials in a file beside this script.
        credentials_file = (
            Path(__file__).resolve().parent
            / "super_admin_credentials.txt"
        )

        try:
            credentials_file.write_text(
                f"SUPER ADMIN LOGIN CREDENTIALS\n"
                f"{'=' * 35}\n"
                f"Username: {username}\n"
                f"Password: {password}\n"
                f"Role: Super Admin\n"
                f"Created: {datetime.now().isoformat(timespec='seconds')}\n"
                f"\nKeep these credentials private and secure.\n",
                encoding="utf-8"
            )

        except OSError:
            # The database account already exists, so do not
            # delete it just because saving the file failed.
            print("\nWARNING: The account was created, but the")
            print("credentials file could not be saved.")
            print("Keep the credentials displayed below securely.")

            print(f"\nUsername: {username}")
            print(f"Password: {password}")
            return

        print("\n" + "=" * 45)
        print("SUPER ADMIN ACCOUNT CREATED SUCCESSFULLY")
        print("=" * 45)
        print(f"Username: {username}")
        print(f"Password: {password}")
        print(f"Credentials saved to: {credentials_file}")
        print("=" * 45)
        print("Keep your credentials private and secure.")


if __name__ == "__main__":
    main()

