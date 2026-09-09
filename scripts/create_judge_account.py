#!/usr/bin/env python3
"""Create a judge account with producer access.

    uv run python scripts/create_judge_account.py --project PROJECT judge@example.com

Enable Email/Password in Firebase Authentication before signing in. Live use
requires application-default credentials with Firebase Auth admin permission.
Set FIREBASE_AUTH_EMULATOR_HOST to use the Auth emulator instead.
Passwords are prompted for, never accepted as command-line arguments.
"""

# firebase-admin ships no type information.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportMissingTypeStubs=false, reportAny=false
# pyright: reportUnknownArgumentType=false
import argparse
import getpass
import os
import sys
import warnings

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials, exceptions
from google.auth.exceptions import DefaultCredentialsError
from orchestrator.auth import PRODUCER_ROLE
from orchestrator.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("email")
    _ = parser.add_argument("--project", default="", help="Firebase project ID")
    args = parser.parse_args(argv)
    project = args.project or Settings().gcp_project
    emulated = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST", "")
    if not emulated and project == "demo-cinema":
        print("Refusing to use demo-cinema without the Auth emulator. Set --project.")
        return 2
    print(f"Creating {args.email} in project {project}.")
    if emulated:
        print(f"Auth emulator: {emulated}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            password = getpass.getpass("Password: ")
            confirmation = getpass.getpass("Confirm password: ")
    except EOFError, KeyboardInterrupt, getpass.GetPassWarning:
        print("Cancelled. Password entry requires a terminal with hidden input.")
        return 2
    if not password or password != confirmation:
        print("Passwords must be nonempty and match. No account was created.")
        return 2

    try:
        app = firebase_admin.initialize_app(
            None if emulated else credentials.ApplicationDefault(),
            {"projectId": project},
            name="judge-account-creation",
        )
    except ValueError, DefaultCredentialsError:
        print("Could not initialise Firebase. Check the project and admin credentials.")
        return 1
    try:
        try:
            user = firebase_auth.create_user(
                email=args.email.strip(), password=password, app=app
            )
        except firebase_auth.EmailAlreadyExistsError:
            print("That email already exists. The existing account was not changed.")
            return 1
        except (ValueError, exceptions.FirebaseError, DefaultCredentialsError) as cause:
            # SDK errors can include supplied values, including the password.
            print(f"Account creation failed ({type(cause).__name__}).")
            print(
                "Check the email, password requirements, project and admin credentials."
            )
            return 1
        try:
            firebase_auth.set_custom_user_claims(
                user.uid, {"role": PRODUCER_ROLE}, app=app
            )
        except (exceptions.FirebaseError, DefaultCredentialsError) as cause:
            print(
                f"Account created ({user.uid}), but producer access failed ({type(cause).__name__})."
            )
            print(
                "Use scripts/grant_producer.py with the same project and email to finish."
            )
            return 1
        print(f"Created producer: {user.email} | UID: {user.uid} | Project: {project}")
        return 0
    finally:
        firebase_admin.delete_app(app)


if __name__ == "__main__":
    sys.exit(main())
