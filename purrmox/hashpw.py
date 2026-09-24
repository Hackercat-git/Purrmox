"""Generate a password hash for the ``auth.password_hash`` setting.

Usage: python -m purrmox.hashpw
"""
import getpass

from werkzeug.security import generate_password_hash


def main():
    pw = getpass.getpass("Choose a password: ")
    if getpass.getpass("Repeat the password: ") != pw:
        raise SystemExit("The passwords do not match.")
    print("\nAdd this to the configuration under 'auth':\n")
    print(f'  password_hash: "{generate_password_hash(pw)}"')


if __name__ == "__main__":
    main()
