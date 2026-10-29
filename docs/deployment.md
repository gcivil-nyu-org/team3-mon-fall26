# AWS deployment

This deployment extends Rae's Django project from the team `develop` branch.
It does not introduce a second Django project or a separate core app.

## Existing test environment

- Region: `us-east-1`
- Elastic Beanstalk application: `nyc-active`
- Environment: `nyc-active-demo-287872994760`
- Platform: Amazon Linux 2023, Python 3.12
- One `t3.micro` instance, 8 GiB gp3 root disk
- PostgreSQL 16 on the same instance, accessible only through loopback with TLS
- Private encrypted S3 database backups, hourly with seven-day archive retention
- Private encrypted S3 bucket for uploaded media such as profile photos
- No load balancer, RDS instance, or NAT gateway

The running instance uses standard CPU credits to avoid surplus CPU charges.
Recheck that setting if Elastic Beanstalk replaces the instance. Compute,
storage, and public IPv4 still consume AWS credits while running.

## Environment variables

Configure these through AWS environment properties, never committed files:

| Variable | Online account testing |
| --- | --- |
| `DJANGO_SETTINGS_MODULE` | `config.production` |
| `DJANGO_SECRET_KEY` | Random private value stored in AWS |
| `DJANGO_ALLOWED_HOSTS` | Exact environment hostname, optionally localhost |
| `DJANGO_DEPLOYMENT_CHECK_ONLY` | `False` |
| `DJANGO_LOCAL_POSTGRES` | `True` |
| `DB_HOST` | `127.0.0.1` |
| `DB_PORT` | `5432` |
| `DB_NAME` / `DB_USER` | `nyc_active` |
| `DB_PASSWORD` | Private random URL-safe value, at least 40 characters |
| `DJANGO_DB_BACKUP_BUCKET` | `nyc-active-db-backups-287872994760-us-east-1` |
| `DJANGO_MEDIA_BUCKET` | `nyc-active-media-287872994760-us-east-1` |
| `DJANGO_MEDIA_REGION` | `us-east-1` |
| `DJANGO_HTTPS` | `True` |
| `DJANGO_TLS_DOMAIN` | Exact existing Beanstalk hostname |

This mode exposes Derah's landing page, `/register/`, `/login/`, and POST-only
`/logout/`, backed by Django authentication and PostgreSQL. Set
`DJANGO_CSRF_TRUSTED_ORIGINS` to both the HTTPS CloudFront and origin URLs.
`/ready/` checks the actual PostgreSQL connection.
The landing page's event board and testimonials are frontend sample content,
not live events or verified user reviews.

The optional restricted infrastructure mode remains available by setting
`DJANGO_DEPLOYMENT_CHECK_ONLY=True` and an explicit `DJANGO_DATABASE_PATH` for
temporary SQLite probes. It hides account/admin routes. PostgreSQL is required
when check mode is off, and HTTPS must stay enabled. Missing configuration fails
startup rather than silently falling back to SQLite.

## Low-cost PostgreSQL and recovery

`.platform/scripts/local_database.py` provisions the AWS-provided PostgreSQL 16
packages only when `DJANGO_LOCAL_POSTGRES=True`. The service listens on
`127.0.0.1` only, requires TLS and SCRAM password authentication for the
non-superuser application role, and uses conservative memory/connection limits.
Port 5432 is not opened in the security group. Passwords are read from AWS
environment properties and passed to SQL over stdin, never committed or logged.

Data lives in `/var/lib/pgsql/data`, outside application releases. Normal code
deployments and instance restarts preserve it. Migration hooks run for both app
and configuration deployments, taking a backup before and after migrations.
Schema changes still require backwards-compatible migrations; reverting code
does not reverse migrations.

The systemd `nyc-active-db-backup.timer` dumps PostgreSQL hourly to the private
bucket above. S3 blocks public access, requires HTTPS and uses AES256 server-side
encryption. Timestamped `postgres/hourly/` dumps expire after seven days;
`postgres/latest.dump` remains as the latest recovery point. The instance role
can list/read/write only this backup prefix, with no backup-deletion permission.
The bucket has `Project=NYCActive` for the existing budget's cost scope.

On a newly initialized instance, setup restores `postgres/latest.dump` before
migrations if it exists. An inaccessible backup fails setup rather than starting
with a blank database. An initialized database is never automatically overwritten.
If unmarked data already contains tables, setup also stops for manual inspection.
An owner can restore a selected timestamped dump to a separate empty PostgreSQL
database with `pg_restore --single-transaction --no-owner --no-acl`, verify it,
then schedule an application cutover. Do not restore over a live populated DB.

This is a single-instance **team test** database, without high availability or
point-in-time recovery. Instance/disk loss can lose changes since the last
successful backup (normally up to about one hour; longer if backups fail).
The latest backup is retained while a budget-stopped instance remains offline.
For wider public use, review managed PostgreSQL, monitoring and access controls.
Systems Manager access on the existing instance role enables maintenance without
opening SSH. Check backup service failures with
`journalctl -u nyc-active-db-backup.service`, and confirm the latest S3 timestamp.

Migration hooks also run `python manage.py sync_parks --if-empty --ignore-errors`
before taking the post-migration backup so a newly initialized database is
automatically populated with NYC Parks Properties (`enfh-gkve`) and Athletic
Facilities (`qnem-b8re`) from NYC Open Data. In addition, setup installs
`nyc-active-parks-sync.timer` to refresh park and facility records weekly as
the unprivileged `webapp` user (preserving any records with
`admin_modified=True`) and immediately write an encrypted S3 backup. See
[NYC Parks & Athletic Facilities data seeding](parks-data-seeding.md) for full
details and CLI options.

## Uploaded media

Instance disks do not survive deploys or instance replacement, and Django does
not serve `MEDIA_ROOT` with `DEBUG` off. Production therefore stores uploads
such as profile photos in the private `nyc-active-media-287872994760-us-east-1`
bucket through `django-storages` (`STORAGES["default"]` in `config/production.py`).
Startup fails if `DJANGO_MEDIA_BUCKET` or `DJANGO_MEDIA_REGION` is missing.
Restricted check mode has no upload routes and does not need them. Local
development keeps `FileSystemStorage`.

Objects live under the `media/` prefix. Pages link to presigned URLs that expire
after one hour; the bucket never serves anonymous requests. Signing happens on
the instance without an AWS call. Links signed with the instance role's
temporary credentials can stop working sooner if those credentials rotate, so
they are for rendering pages, not for sharing.

Presigned URLs were chosen over serving `/media/` through CloudFront because:

- Adding an S3 origin to distribution `E1YOQE0SL33H2U` would break the cost
  controls. `infra/budget_stop.py` and `infra/deploy_ci.py` refuse to act unless
  the Beanstalk hostname is the distribution's only origin.
- CloudFront would also need an origin access control, a bucket policy for the
  CloudFront service and a new cache behavior. Presigned URLs need none of these.
- Deleting an object revokes access at once. A CloudFront copy of a removed
  photo would stay reachable until its cache entry expired.
- At team-test traffic, S3 GET requests and transfer cost fractions of a cent.
  CloudFront would only be cheaper at far higher traffic.

Because every page render signs a new URL, browsers download the photo again on
each view. Profile photos are 512px JPEGs, so this is acceptable at this scale.

The instance role gets `infra/media-storage-policy.json`: `s3:GetObject`,
`s3:PutObject` and `s3:DeleteObject` on `media/*` only, with no bucket listing.
The storage keeps `file_overwrite` on so saving never runs an existence check,
which would need `s3:ListBucket`. Upload names are random UUIDs, so this never
replaces another user's file. Delete access lets the app remove replaced or
removed photos instead of leaving them in the bucket.

The bucket blocks all public access, enforces bucket-owner object ownership
(no ACLs), encrypts with SSE-S3 (AES256) and denies non-HTTPS requests
(`infra/media-bucket-policy.json`). Versioning stays off so deleted photos are
really gone. `infra/media-bucket-lifecycle.json` aborts incomplete uploads after
a day and expires noncurrent versions after a day if versioning is ever enabled.
It never expires current photos. The bucket carries `Project=NYCActive`.

### One-time setup

An account owner runs these steps **before** merging the settings change into
`develop`. Automatic deployment would otherwise start an app that fails on the
missing variables, and the release script would roll it back. The variables are
harmless to set early because older releases ignore them. Run from the repository root:

```bash
BUCKET=nyc-active-media-287872994760-us-east-1
aws s3api create-bucket --region us-east-1 --bucket "$BUCKET" --object-ownership BucketOwnerEnforced
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-policy --bucket "$BUCKET" --policy file://infra/media-bucket-policy.json
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
  --lifecycle-configuration file://infra/media-bucket-lifecycle.json
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=Project,Value=NYCActive}]'
```

Find the environment's instance role and attach the inline policy:

```bash
PROFILE=$(aws elasticbeanstalk describe-configuration-settings --region us-east-1 \
  --application-name nyc-active --environment-name nyc-active-demo-287872994760 \
  --query "ConfigurationSettings[0].OptionSettings[?OptionName=='IamInstanceProfile'].Value" --output text)
ROLE=$(aws iam get-instance-profile --instance-profile-name "${PROFILE##*/}" \
  --query 'InstanceProfile.Roles[0].RoleName' --output text)
aws iam put-role-policy --role-name "$ROLE" --policy-name nyc-active-media-storage \
  --policy-document file://infra/media-storage-policy.json
```

If another environment, such as the personal assignment, uses the same instance
profile, its instances would gain the same access. Give the team environment a
dedicated profile first in that case.

Then set the environment properties. This is a configuration deployment of the
current release:

```bash
aws elasticbeanstalk update-environment --region us-east-1 \
  --environment-name nyc-active-demo-287872994760 --option-settings \
  Namespace=aws:elasticbeanstalk:application:environment,OptionName=DJANGO_MEDIA_BUCKET,Value=nyc-active-media-287872994760-us-east-1 \
  Namespace=aws:elasticbeanstalk:application:environment,OptionName=DJANGO_MEDIA_REGION,Value=us-east-1
```

After the release, upload, replace and remove a photo with a disposable account.
The image URL should point at the bucket and carry `X-Amz-Signature`. The same
URL without its query string should return 403, and
`aws s3 ls s3://nyc-active-media-287872994760-us-east-1/media/avatars/` should
list only the current photo.

## Manual release

1. Test the intended commit: `python manage.py test` and `python manage.py check`.
2. Commit changes. Create a `dist` directory if necessary, then package that
   exact commit with `git archive --format=zip --output=dist/nyc-active.zip HEAD`.
3. Create an Elastic Beanstalk application version using the ZIP. Project files
   must be at the archive root. Do not include `.env`, virtual environments,
   local databases, or credentials.
4. Deploy that version to the **existing** test environment. When moving from
   the old skeleton, update `DJANGO_SETTINGS_MODULE` from
   `config.settings.production` to `config.production` in the same update.
5. Wait for Ready/Green and confirm `/`, `/health/`, `/ready/` return 200 over HTTPS.
   Confirm the landing page and its hashed CSS, JS and images load. Confirm
   `/login/` and `/register/` render and test sign-up/login/logout with a disposable
   account. Upload a profile photo and confirm it loads through a presigned S3 URL.
   In optional check mode, confirm account/admin routes return 404.

`Procfile` starts Gunicorn on port 8000. Platform predeploy hooks run migrations;
`.ebextensions/django.config` collects static files, served by WhiteNoise. Health middleware
permits internal Host headers only on `/health/`; other URLs keep host checks.

The manual procedure remains available for recovery. Normal updates to the
central `develop` branch now use the workflow described below.

## Automatic develop deployment

`.github/workflows/deploy-develop.yml` runs on pushes and PRs targeting `develop`.
Every run checks dependencies, Django configuration and migrations, application
tests, infrastructure safety tests and shell hook syntax. Only a successful
**push to the central develop branch** can obtain AWS credentials and deploy.
Forks, feature branches, `main`, and unmerged PRs cannot publish to this environment.

AWS authentication uses GitHub OIDC and the `nyc-active-github-deploy` role.
There are no long-lived AWS keys in GitHub. The trust policy uses the immutable
organization/repository IDs reported by this repository's OIDC settings, plus
the exact `refs/heads/develop` subject and `sts.amazonaws.com` audience.
The policy/trust documents are in `infra/github-deploy-*.json`. Only the existing
team environment, its application versions, dedicated source-bundle and
Beanstalk extension-cache and existing team-environment runtime prefixes in the existing bucket,
and read-only cost/pause checks are permitted. The separate
personal assignment is excluded. No account/admin credentials are passed to CI.
Beanstalk source validation also needs bucket location, ACL and key-listing
reads. Object-content reads and writes remain scoped to this application's
bundle, cache and team runtime prefixes; the role cannot deploy to the personal environment.
It may read older team source bundles for rollback and clean generated deployment
files only under this existing environment's `resources/environments/e-c27h4y6per/`
prefix. It cannot delete original source bundles or another environment's files.
It reads the existing team's CloudFormation metadata and can update only that
exact existing stack when Beanstalk changes deployment-command metadata.
There are no stack-creation or instance-start permissions.

The deploy job is serialized and an active release is never cancelled by a new
push. An older queued commit skips deployment if develop has already advanced.
The script publishes an exact git archive as `gha-<run id>-<attempt>-<commit>`, excluding
workflow files, infrastructure tooling, local databases and credentials.
It changes only the existing environment's version label, retaining AllAtOnce
deployment on one instance and the existing environment variables.

Before publishing and throughout the deployment wait, the script checks the
project's $15 pre-credit budget, the distribution's enabled state, the expected
environment identity and its unpaused single-instance Auto Scaling group.
Missing cost data, a reached threshold, a disabled distribution or a latched
budget stop blocks publication. Starting a release also requires unpaused
scaling. During an active update, Beanstalk's temporary health-check suspension
is permitted. IAM allows Beanstalk to suspend/resume only this group's normal
deployment processes, but explicitly denies resuming when the guard has set
`nyc-active:budget-stopped=true`. CI cannot clear that tag, start instances or
re-enable CloudFront. Billing remains delayed; these
checks do not turn the budget into an exact cap.

A successful run waits for Ready/Green, then checks the HTTPS homepage, its
referenced local static assets, `/health/` and `/ready/`. If release verification
fails and the environment is Ready and still passes the budget/pause checks,
the script attempts to restore the preceding application version.
An unhealthy environment is restored even if AWS still reports the old version
label after a failed update. Database
migrations are not reversed by this operation; review backwards compatibility
when introducing schema changes. If the environment is busy or budget-stopped,
the workflow fails without forcing recovery.

Find progress and failures under GitHub **Actions → Test and deploy develop**.
After fixing a transient problem, use **Re-run jobs** on the existing run;
outdated commits are skipped. Fix failed tests through a new PR. If publication
must be suspended, disable this workflow in GitHub Actions; this does not stop
the currently running site or its usage charges. Automatic deployment does not
change the environment's database credentials or check-mode flag. Account
activation requires the explicit environment configuration described above.

## HTTPS test entry point

- URL: https://dgaeptdmd25z5.cloudfront.net/
- CloudFront distribution: `E1YOQE0SL33H2U`
- Origin: the same Beanstalk environment above; no additional EC2 or database
- Configuration template: `infra/cloudfront.json` (excluded from the app ZIP)

CloudFront uses its default trusted certificate for the assigned hostname and
redirects browser HTTP requests to HTTPS. The existing Beanstalk hostname also
supports direct HTTPS with its own trusted certificate. The CloudFront hostname
remains the shared frontend URL.

The managed CachingDisabled policy prevents stale deployment pages and health
checks. AllViewerExceptHostHeader forwards viewer query strings and cookies,
while setting Host to the Beanstalk origin hostname so Django's explicit host
allowlist remains valid. No WAF, access logs, Lambda@Edge, or paid subscription
was added.

Both network legs use HTTPS: browser to CloudFront, and CloudFront to the
Beanstalk instance. The CloudFront origin protocol is `https-only`, restricted
to TLS 1.2. The instance's Nginx accepts TLS 1.2/1.3 and overwrites
X-Forwarded-Proto with `https` before forwarding to Gunicorn on loopback port
8000. Django redirects ordinary HTTP application requests to HTTPS; internal
health probes and ACME HTTP-01 validation remain available on port 80.

The prebuild hook obtains a free Let's Encrypt certificate for DJANGO_TLS_DOMAIN
using HTTP-01. Keys stay in `/etc/letsencrypt/` on the instance; they are never
committed or uploaded in the application ZIP. The postdeploy hook installs a
systemd timer that checks renewal twice daily and reloads Nginx after renewal.
Configuration-deployment hooks also regenerate the Nginx HTTPS listener and
maintain the renewal timer, so changing database/environment properties keeps
port 443 enabled when Beanstalk rebuilds its proxy configuration.
The migration hook also collects static files, since configuration deployments
rebuild the staging directory without running application container commands.
If no TLS domain is configured, these hooks do nothing. Instance replacement
will need fresh certificate issuance, subject to the certificate authority's
availability and limits. Maintain the same hostname and public DNS routing.

The application's CSRF trusted origins must
include its chosen HTTPS frontend URL. HTTPS does not make the temporary SQLite
database suitable for real user data.

The distribution uses pay-as-you-go pricing with CloudFront's applicable free
usage allowance; it is not subscribed to a flat-rate plan. AWS Free Tier accounts
are not eligible for CloudFront flat-rate plans. Existing EC2, disk and IPv4
charges continue. Relevant official documentation:

- https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-https-viewers-to-cloudfront.html
- https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/
- https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html

For recreation, copy the JSON template outside the source tree, replace the
origin placeholder with the existing environment hostname, and choose a unique
CallerReference. Review the account and existing distributions before calling
`aws cloudfront create-distribution --distribution-config file://<file>`.
Updating the existing distribution uses its current ETag and
`aws cloudfront update-distribution`, not a second distribution.
