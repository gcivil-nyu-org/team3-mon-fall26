# NYC Active

[![Build Status](https://app.travis-ci.com/gcivil-nyu-org/team3-mon-fall26.svg?branch=develop)](https://app.travis-ci.com/gcivil-nyu-org/team3-mon-fall26)
[![Coverage Status](https://coveralls.io/repos/github/gcivil-nyu-org/team3-mon-fall26/badge.svg?branch=develop)](https://coveralls.io/github/gcivil-nyu-org/team3-mon-fall26?branch=develop)

The team's Django project. Feature branches should start from `develop` and
return through a pull request. `main` is the stable branch.

## Local development

Use Python 3.12. Create a virtual environment, activate it, then install and run:

```sh
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py sync_parks
python manage.py runserver
```

Open http://127.0.0.1:8000/. `config/settings.py` remains the local development
configuration. `python manage.py sync_parks` populates `db.sqlite3` with NYC
parks and athletic facilities from NYC Open Data (see
[parks data seeding](docs/parks-data-seeding.md)). The home page renders the
team's landing-page design. `/health/` checks the process, and `/ready/`
checks database connectivity. Run `python manage.py test` for deployment tests.

## AWS

The existing single-instance Elastic Beanstalk test environment uses
`config.production`, which extends the team's settings. See
[deployment instructions](docs/deployment.md) for environment variables,
packaging, PostgreSQL backups and the single-instance test environment's limits.

Pushes and merged PRs to the central repository's `develop` branch run tests
and then automatically deploy to the existing AWS test environment. Other
branches and fork PRs do not deploy. Track the run under GitHub **Actions →
Test and deploy develop**. See the deployment guide for safeguards and recovery.
This project uses Django 5.2 LTS because Django 4.2 support ended in April 2026.

The shared HTTPS URL is https://dgaeptdmd25z5.cloudfront.net/.
CloudFront supplies the browser-facing certificate and redirects HTTP to HTTPS.
Its connection to the existing Beanstalk instance also uses HTTPS, with a
Let's Encrypt certificate renewed on the same instance. The account backend
uses PostgreSQL 16 on that existing instance, with hourly encrypted S3 backups.
Normal deployments keep the database outside the application directory.
No additional database server is required for the team's current testing.
See the deployment guide for configuration, restore steps and data-loss limits.

The team has a US$15 monthly usage budget before promotional credits. An hourly
guard stops only the existing team environment after AWS reports that threshold.
Billing/tag data is delayed, so this is not a precise spending cap; disk and IP
costs can continue after stopping. See [cost controls](docs/cost-controls.md).

## Continuous integration

Travis CI (`.travis.yml`) runs on pushes to `develop`/`main` and on pull requests into them:
Black formatting (`pyproject.toml`) and flake8 (`.flake8`), both at 120 columns, Django and infra tests under coverage (`.coveragerc`),
the Node JS tests and deploy-hook syntax checks, then reports coverage to Coveralls.
CI-only tools are in `requirements-dev.txt`. Run the same checks locally:

```sh
python -m pip install -r requirements-dev.txt
black .          # formats code; CI runs black --check
flake8 .
coverage run manage.py test && coverage report
```
