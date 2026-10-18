# Local invoice workspace

A Django application for manual invoice entry and accountant review. Uses SQLite, Django templates and local CSS. Each business has client/accountant memberships; superusers administer accounts and memberships. No OCR or ML is performed.

## Setup

Use Python 3.12 or 3.13. Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

If `py` is unavailable, use your installed Python executable to create the environment. Activation is optional: run `.\.venv\Scripts\python.exe` directly. If PowerShell blocks activation, use the executable directly rather than changing machine policy.

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'
```

Paste the generated secret into `.env` as `SECRET_KEY='your-generated-value'` (single quotes preserve `#` characters). Keep `.env` private. Set `DEBUG=True` only for local development. Then:

```text
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

Open http://127.0.0.1:8000/. `seed_demo` prompts for a password for newly created accounts. An optional `--password` argument is available for scripted use; command arguments can remain in shell history. Reruns preserve existing passwords. Demo accounts are `demo_client_one`, `demo_client_two`, and `demo_accountant`; fictional records cover all four workflow states across two businesses.

## Accounts and workflow

Use `/admin/` as a superuser to create users, active businesses and memberships. Give each member the client or accountant role for that business. Staff status alone grants neither invoice access nor admin access. Invoice and event admin records are read-only so lifecycle rules cannot be bypassed there. Forgotten passwords can be reset in admin or with `python manage.py changepassword USERNAME`.

Login opens the business selector (or the sole permitted workspace). Upload one JPEG, PNG or unencrypted PDF, enter supplier, number, invoice date, currency and a non-negative total, and submit. A due date is optional but must not precede invoice date. Draft fields may be incomplete. Assigned accountants can return submitted invoices with a reason or approve them. Returned invoices may be corrected and resubmitted. Submitted and approved records are locked. Only drafts can be deleted, after confirmation. Approval does not record payment.

Identical documents are blocked within a business. Possible duplicates based on normalised supplier/number require explicit acknowledgement on submission; the acknowledgement appears in history. Lists offer search, status/date filters and 20-record pages. Accountants can export approved records using the current search/date filters, with spreadsheet formula protection. No mixed-currency totals are calculated.

## Storage and integration boundary

SQLite lives in ignored `db.sqlite3`. Documents use random filenames beneath ignored `private_media/documents/`. Every source request checks authentication and current membership; there is no public media route. PDFs download as attachments; images can be previewed. Do not serve this folder from a separate web server. Membership removal or business deactivation revokes subsequent requests. Historical references protect users and businesses from deletion.

Uploads are limited to 10 MiB, single-frame JPEG/PNG with at most 25 million pixels, or PDFs of 1–10 pages. Content is validated rather than trusting browser MIME types. Filesystem operations cannot share a database transaction: upload errors remove saved files, and draft deletion removes files after database commit. A machine crash between filesystem and database operations can require manual orphan cleanup. SQLite serialises writes; this local version is not a production multi-user deployment or a malware scanner.

Reusable validation, permissions and transitions live in `invoices/services.py` and `accounts/services.py`. Future extraction should read stored documents and supply candidate values to `InvoiceForm`, leaving manual corrections and review intact. It must never auto-approve or overwrite confirmed values. No speculative ML tables or dependencies are included.

## Checks

```text
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Tests generate tiny fictional image/PDF fixtures in temporary storage. They cover authentication, CSRF, access isolation, upload validation/cleanup, duplicate constraints, manual review, history, deletion, filters, CSV and idempotent demonstration data. The duplicate race test simulates a competing database winner; it is not a threaded load test.

`requirements.in` declares compatible dependency ranges; `requirements.txt` records exact installed versions. Initial migrations are included. To intentionally update dependencies, install from `requirements.in`, run checks/tests and regenerate the pinned file with `python -m pip freeze`.

Implementation follows the official [Django authentication](https://docs.djangoproject.com/en/5.2/topics/auth/default/) and [file-upload](https://docs.djangoproject.com/en/5.2/topics/http/file-uploads/) documentation.

## Verified build

Verified on Windows with Python 3.13.1 and Django 5.2.18 on 8 October 2026:

- Initial migrations generated and applied successfully.
- `manage.py check`: no issues.
- `manage.py makemigrations --check --dry-run`: no changes detected.
- `manage.py test`: all 20 tests passed (174.475 seconds).
- `pip check`: no broken requirements.
- Login page smoke check: HTTP 200 with the username field present.

The development environment and random local `.env` secret have been created in this workspace. No real user accounts have been created. Start with `.\.venv\Scripts\python.exe manage.py createsuperuser`, optionally run `seed_demo`, then run the local server. Automated tests use a separate temporary database and do not populate the development database. Browser appearance has not been manually inspected.
