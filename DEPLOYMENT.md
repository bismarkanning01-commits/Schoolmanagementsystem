# Deployment setup

The application creates its first Super Admin during startup. You do not need
to run `create_admin.py` on the host.

## Configure the host

Create a persistent production database and set these environment variables in
the hosting provider's secret/environment settings before the first deployment:

- `APP_ENV=production`
- `SECRET_KEY`: a long, random value used to sign sessions and tokens.
- `DATABASE_URL`: a SQLAlchemy connection URL for the production database.
- `SUPER_ADMIN_USERNAME`: the initial Super Admin username.
- `SUPER_ADMIN_PASSWORD`: a unique password with at least 12 characters.
- `SUPER_ADMIN_EMAIL`: the initial Super Admin's valid recovery email.

Generate `SECRET_KEY` with a cryptographically secure random generator, for
example `python -c "import secrets; print(secrets.token_hex(32))"`. Store the
result directly in the hosting provider's secret settings. URL-encode special
characters in database usernames or passwords used in `DATABASE_URL`.

The values in `.env.example` show the expected names and formats. Do not copy
real secrets into that file or commit a `.env` file.

When the app starts with an empty database, it creates the schema and the
Super Admin account. The password is stored as a hash; the plaintext password
is not written to a file or printed. Once a Super Admin exists, the three
`SUPER_ADMIN_*` variables can be removed from the host configuration. Future
restarts will not recreate or change the account.

In development, automatically generated Super Admin credentials are saved in
`super_admin_credentials.txt`. This file is excluded by `.gitignore`; do not
force-add or commit it.

Production startup fails with a clear error if the required database, session
secret, or initial Super Admin settings are missing or invalid. Use the
hosting provider's recommended production WSGI server and set its start command
to load the `app` object from `app.py`; do not enable Flask debug mode in
production.

## Password reset email

Set `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, and
`MAIL_PASSWORD` in the host's environment settings to enable password reset
emails. Do not keep SMTP credentials in source code. Rotate any SMTP password
that was previously embedded in the application source; moving it to an
environment variable does not invalidate copies or repository history.

Set `MAIL_DEFAULT_SENDER` to the sender address if it differs from
`MAIL_USERNAME`. If no sender is configured, password-reset requests show a
clear unavailable message instead of attempting to send mail.

Authenticated school admins can reset teacher, accountant, and other
administrator passwords for their own school from Manage Staff. Super Admins
can reset other user accounts across schools from Manage Users. The app shows
the generated temporary password once after a successful reset, and the user
must choose a new password at their next sign-in. To change your own password,
use Change Password; password resets do not run automatically at startup.

School admins can create Headmaster accounts from Manage Staff. Headmasters
have a separate, read-only academic dashboard for their own school's students,
classes, attendance, and performance. They cannot manage staff or access
finance features; school admins retain responsibility for headmaster account
and password management.

The local Flask development server uses HTTP, so browsers may label
`http://localhost` as “Not secure.” That warning is separate from the reset
email configuration. Use HTTPS for deployments accessible over a network.
In development mode, saving Python changes restarts the server and saving
template changes takes effect on the next browser refresh. Both reload
features are disabled when `APP_ENV=production`.

## After deployment

1. Sign in with the initial Super Admin username, password, and email.
2. Change the password from the account settings page.
3. Remove the `SUPER_ADMIN_*` bootstrap variables from the host configuration.
4. Keep `SECRET_KEY` and `DATABASE_URL` configured for normal operation.

The existing `create_admin.py` utility is not part of this deployment flow.
