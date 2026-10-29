"""Small, loopback-only PostgreSQL service for the single-instance team site."""

import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

DATA = Path("/var/lib/pgsql/data")
INSTALL = Path("/opt/nyc-active-db")
BUCKET = "nyc-active-db-backups-287872994760-us-east-1"


def environment():
    result = subprocess.run(
        ["/opt/elasticbeanstalk/bin/get-config", "environment"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def validate(env):
    if env.get("DJANGO_LOCAL_POSTGRES", "False").lower() not in {"true", "1"}:
        return False
    expected = {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5432",
        "DB_NAME": "nyc_active",
        "DB_USER": "nyc_active",
        "DJANGO_DB_BACKUP_BUCKET": BUCKET,
    }
    if any(env.get(key) != value for key, value in expected.items()):
        raise RuntimeError("Local database identity or backup bucket does not match the team environment")
    if not re.fullmatch(r"[A-Za-z0-9_-]{40,}", env.get("DB_PASSWORD", "")):
        raise RuntimeError("Configure a private, random URL-safe database password of at least 40 characters")
    return True


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def sql(statement, database="postgres"):
    # Passwords travel over stdin, never argv, a shell, or deployment output.
    result = subprocess.run(
        ["runuser", "-u", "postgres", "--", "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-d", database],
        input=statement,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("Local PostgreSQL administration failed; inspect the protected server logs")
    return result.stdout.strip()


def write_changed(path, content, mode=0o600):
    changed = not path.exists() or path.read_text() != content
    if changed:
        path.write_text(content)
    path.chmod(mode)
    return changed


def s3(*args, **kwargs):
    return run(["aws", "--region", "us-east-1", "s3api", *args], **kwargs)


def backup():
    env = environment()
    if not validate(env):
        return
    # Root owns this temporary directory; the peer-authenticated postgres process
    # writes its dump to stdout, so the web application never reads the archive.
    with tempfile.TemporaryDirectory(prefix="nyc-active-backup-") as directory:
        archive = Path(directory) / "database.dump"
        with archive.open("wb") as output:
            run(
                ["runuser", "-u", "postgres", "--", "pg_dump", "-Fc", "--no-owner", "--no-acl", "--dbname=nyc_active"],
                stdout=output,
            )
        # Validate the archive before replacing the latest recovery point.
        run(["pg_restore", "--list", str(archive)], stdout=subprocess.DEVNULL)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        for key in ["postgres/hourly/" + stamp + ".dump", "postgres/latest.dump"]:
            s3(
                "put-object",
                "--bucket",
                BUCKET,
                "--key",
                key,
                "--body",
                str(archive),
                "--server-side-encryption",
                "AES256",
                stdout=subprocess.DEVNULL,
            )
    print("Encrypted PostgreSQL backup uploaded", flush=True)


def restore_if_new():
    marker = DATA / ".nyc-active-initialized"
    if marker.exists():
        return
    tables = sql("SELECT count(*) FROM pg_tables WHERE schemaname='public';", "nyc_active")
    if tables != "0":
        raise RuntimeError("Unmarked database contains tables; refusing to overwrite existing data")
    # List failure is fatal. An inaccessible backup must never be mistaken for
    # an empty bucket, which could silently replace existing users with a blank DB.
    objects = json.loads(
        s3(
            "list-objects-v2", "--bucket", BUCKET, "--prefix", "postgres/latest.dump", capture_output=True, text=True
        ).stdout
    )
    if any(item["Key"] == "postgres/latest.dump" for item in objects.get("Contents", [])):
        with tempfile.TemporaryDirectory(prefix="nyc-active-restore-") as directory:
            archive = Path(directory) / "database.dump"
            s3(
                "get-object",
                "--bucket",
                BUCKET,
                "--key",
                "postgres/latest.dump",
                str(archive),
                stdout=subprocess.DEVNULL,
            )
            # postgres can traverse/read only this short-lived restore directory.
            shutil.chown(directory, "postgres", "postgres")
            shutil.chown(archive, "postgres", "postgres")
            run(
                [
                    "runuser",
                    "-u",
                    "postgres",
                    "--",
                    "pg_restore",
                    "--exit-on-error",
                    "--single-transaction",
                    "--no-owner",
                    "--no-acl",
                    "--role=nyc_active",
                    "--dbname=nyc_active",
                    str(archive),
                ]
            )
        print("Recovered PostgreSQL from the latest encrypted backup", flush=True)
    marker.touch(mode=0o600)


def setup():
    env = environment()
    if not validate(env):
        return
    if not shutil.which("aws"):
        raise RuntimeError("AWS CLI is required for database backup and recovery")
    run(["dnf", "install", "-y", "postgresql16-server", "postgresql16"])
    if not (DATA / "PG_VERSION").exists():
        run(["postgresql-setup", "--initdb"])
    if (DATA / "PG_VERSION").read_text().strip() != "16":
        raise RuntimeError("Unexpected PostgreSQL major version; no automatic destructive upgrade is allowed")
    certificate = DATA / "server.crt"
    if (
        not certificate.exists()
        or subprocess.run(
            ["openssl", "x509", "-checkend", "2592000", "-noout", "-in", str(certificate)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
    ):
        run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-nodes",
                "-newkey",
                "rsa:2048",
                "-days",
                "825",
                "-subj",
                "/CN=localhost",
                "-keyout",
                str(DATA / "server.key"),
                "-out",
                str(certificate),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for name in ["server.crt", "server.key"]:
            shutil.chown(DATA / name, "postgres", "postgres")
            (DATA / name).chmod(0o600)
    config = DATA / "postgresql.conf"
    include = "include_if_exists = 'nyc-active.conf'"
    if include not in config.read_text():
        with config.open("a") as output:
            output.write("\n" + include + "\n")
    changed = write_changed(
        DATA / "nyc-active.conf",
        """# Single-instance team testing; never expose PostgreSQL on the network.
listen_addresses = '127.0.0.1'
max_connections = 25
shared_buffers = '32MB'
work_mem = '1MB'
maintenance_work_mem = '32MB'
jit = off
password_encryption = 'scram-sha-256'
ssl = on
ssl_min_protocol_version = 'TLSv1.2'
""",
    )
    changed = (
        write_changed(
            DATA / "pg_hba.conf",
            """# OS postgres account uses peer authentication for local administration/backups.
local all postgres peer
local all all scram-sha-256
hostssl nyc_active nyc_active 127.0.0.1/32 scram-sha-256
host all all 127.0.0.1/32 reject
host all all ::1/128 reject
""",
        )
        or changed
    )
    for name in ["nyc-active.conf", "pg_hba.conf"]:
        shutil.chown(DATA / name, "postgres", "postgres")
    run(["systemctl", "enable", "postgresql"])
    active = subprocess.run(["systemctl", "is-active", "--quiet", "postgresql"]).returncode == 0
    run(["systemctl", "restart" if changed and active else "start", "postgresql"])
    sql(
        "SELECT 'CREATE ROLE nyc_active LOGIN' WHERE NOT EXISTS "
        "(SELECT FROM pg_roles WHERE rolname='nyc_active')\\gexec\n"
    )
    sql(
        "ALTER ROLE nyc_active WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
        "PASSWORD '" + env["DB_PASSWORD"] + "';"
    )
    sql(
        "SELECT 'CREATE DATABASE nyc_active OWNER nyc_active' WHERE NOT EXISTS "
        "(SELECT FROM pg_database WHERE datname='nyc_active')\\gexec\n"
    )
    restore_if_new()
    INSTALL.mkdir(mode=0o755, exist_ok=True)
    shutil.copyfile(__file__, INSTALL / "local_database.py")
    (INSTALL / "local_database.py").chmod(0o700)
    write_changed(
        Path("/etc/systemd/system/nyc-active-db-backup.service"),
        """[Unit]
Description=Back up the NYC Active PostgreSQL database to private encrypted S3
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3.12 /opt/nyc-active-db/local_database.py backup
TimeoutStartSec=180
Nice=10
UMask=0077
""",
        0o644,
    )
    write_changed(
        Path("/etc/systemd/system/nyc-active-db-backup.timer"),
        """[Unit]
Description=Hourly NYC Active PostgreSQL backup

[Timer]
OnCalendar=*-*-* *:07:00
RandomizedDelaySec=120
Persistent=true

[Install]
WantedBy=timers.target
""",
        0o644,
    )
    write_changed(
        Path("/etc/systemd/system/nyc-active-parks-sync.service"),
        """[Unit]
Description=Refresh NYC Parks and Athletic Facilities from NYC Open Data
After=network-online.target postgresql.service
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/var/app/current
ExecStart=/usr/bin/python3.12 /opt/nyc-active-db/local_database.py sync_parks
TimeoutStartSec=300
Nice=10
UMask=0077
""",
        0o644,
    )
    write_changed(
        Path("/etc/systemd/system/nyc-active-parks-sync.timer"),
        """[Unit]
Description=Weekly NYC Open Data parks and facilities sync

[Timer]
OnCalendar=Sun *-*-* 04:15:00
RandomizedDelaySec=300
Persistent=true

[Install]
WantedBy=timers.target
""",
        0o644,
    )
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "nyc-active-db-backup.timer"])
    run(["systemctl", "enable", "--now", "nyc-active-parks-sync.timer"])
    print("Local PostgreSQL is ready; encrypted hourly backups and weekly park syncs are enabled", flush=True)


def sync_parks():
    env = environment()
    os.environ.update(env)
    if not validate(env):
        return
    executable = next(Path("/var/app/venv").glob("*/bin/python"))
    child_env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": env.get("DJANGO_SETTINGS_MODULE", "config.production"),
        "HOME": "/var/app",
        "USER": "webapp",
        "LOGNAME": "webapp",
    }
    run(
        [
            "runuser",
            "-u",
            "webapp",
            "--preserve-environment",
            "--",
            str(executable),
            "manage.py",
            "sync_parks",
            "--ignore-errors",
        ],
        cwd="/var/app/current",
        env=child_env,
    )
    backup()


def migrate():
    env = environment()
    os.environ.update(env)
    local = validate(env)
    if local:
        backup()
    # Both app and configuration deployments run this hook. Run Django with
    # the same unprivileged OS account and environment as the web process.
    executable = next(Path("/var/app/venv").glob("*/bin/python"))
    child_env = {**os.environ, "HOME": "/var/app", "USER": "webapp", "LOGNAME": "webapp"}
    run(
        [
            "runuser",
            "-u",
            "webapp",
            "--preserve-environment",
            "--",
            str(executable),
            "manage.py",
            "migrate",
            "--noinput",
        ],
        env=child_env,
    )
    if local:
        run(
            [
                "runuser",
                "-u",
                "webapp",
                "--preserve-environment",
                "--",
                str(executable),
                "manage.py",
                "sync_parks",
                "--if-empty",
                "--ignore-errors",
            ],
            env=child_env,
        )
    # Configuration deployments rebuild staging but skip container_commands.
    # Like the existing app-deployment collectstatic command, run as root so
    # previously collected root-owned files can be replaced safely.
    run([str(executable), "manage.py", "collectstatic", "--noinput"], env=child_env)
    if local:
        backup()


if __name__ == "__main__":
    {"setup": setup, "migrate": migrate, "backup": backup, "sync_parks": sync_parks}[sys.argv[1]]()
