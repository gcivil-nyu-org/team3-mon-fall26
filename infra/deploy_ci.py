"""Publish a tested develop commit without creating or resuming infrastructure."""

from decimal import Decimal
import json
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import zipfile

ACCOUNT = "287872994760"
REGION = "us-east-1"
REPOSITORY = "gcivil-nyu-org/team3-mon-fall26"
APPLICATION = "nyc-active"
ENVIRONMENT = "nyc-active-demo-287872994760"
ENVIRONMENT_ID = "e-c27h4y6per"
ORIGIN = ENVIRONMENT + ".us-east-1.elasticbeanstalk.com"
URL = "https://dgaeptdmd25z5.cloudfront.net"
DISTRIBUTION = "E1YOQE0SL33H2U"
ASG = "awseb-e-c27h4y6per-stack-AWSEBAutoScalingGroup-UQYtDQcWnyDe"
BUCKET = "elasticbeanstalk-us-east-1-" + ACCOUNT
BUDGET = "nyc-active-monthly-15-usd"
BUDGET_STOP_TAG = "nyc-active:budget-stopped"


def preflight(clients, require_ready=True):
    budget = clients["budgets"].describe_budget(AccountId=ACCOUNT, BudgetName=BUDGET)["Budget"]
    if (
        budget.get("TimeUnit") != "MONTHLY"
        or budget.get("CostFilters", {}).get("TagKeyValue") != ["user:Project$NYCActive"]
        or budget.get("CostTypes", {}).get("IncludeCredit") is not False
        or budget.get("BudgetLimit", {}).get("Unit") != "USD"
        or Decimal(budget["BudgetLimit"]["Amount"]) != 15
    ):
        raise RuntimeError("Team budget configuration changed; deployment blocked")
    actual = budget.get("CalculatedSpend", {}).get("ActualSpend")
    if not actual or actual.get("Unit") != "USD" or Decimal(actual["Amount"]) >= 15:
        raise RuntimeError("Cost data is unavailable or the $15 budget was reached; deployment blocked")
    distribution = clients["cloudfront"].get_distribution(Id=DISTRIBUTION)["Distribution"]["DistributionConfig"]
    if not distribution["Enabled"] or [o["DomainName"] for o in distribution["Origins"]["Items"]] != [ORIGIN]:
        raise RuntimeError("Team distribution is disabled or changed; deployment blocked")
    environments = clients["elasticbeanstalk"].describe_environments(EnvironmentNames=[ENVIRONMENT])["Environments"]
    if len(environments) != 1:
        raise RuntimeError("Existing team environment was not found")
    environment = environments[0]
    if (
        environment["ApplicationName"] != APPLICATION
        or environment["EnvironmentId"] != ENVIRONMENT_ID
        or environment["CNAME"] != ORIGIN
    ):
        raise RuntimeError("Environment identity changed; deployment blocked")
    if require_ready and environment["Status"] != "Ready":
        raise RuntimeError("The team environment is busy; retry after its current operation finishes")
    groups = clients["autoscaling"].describe_auto_scaling_groups(AutoScalingGroupNames=[ASG])["AutoScalingGroups"]
    if len(groups) != 1:
        raise RuntimeError("Expected team Auto Scaling group was not found")
    group = groups[0]
    tags = {t["Key"]: t["Value"] for t in group.get("Tags", [])}
    if (
        tags.get("elasticbeanstalk:environment-id") != ENVIRONMENT_ID
        or any(group[k] != 1 for k in ["DesiredCapacity", "MinSize", "MaxSize"])
        or tags.get(BUDGET_STOP_TAG) == "true"
        or (require_ready and group.get("SuspendedProcesses"))
    ):
        raise RuntimeError("Team capacity is changed or paused; deployment must not resume it")
    return environment


def wait_for_version(clients, version, attempts=60):
    for attempt in range(attempts):
        environment = preflight(clients, require_ready=False)
        if environment["Status"] == "Ready":
            if environment.get("VersionLabel") == version and environment.get("Health") == "Green":
                return
            # Allow a short propagation window after UpdateEnvironment.
            if attempt >= 3 and (environment.get("VersionLabel") != version or environment.get("Health") == "Red"):
                raise RuntimeError("The requested release did not become healthy")
        time.sleep(10)
    raise TimeoutError("The release did not become Ready/Green within ten minutes")


def verify_https():
    # Check availability without depending on a particular frontend design.
    with urllib.request.urlopen(URL + "/", timeout=25) as response:
        html = response.read().decode("utf-8")
        if response.status != 200:
            raise RuntimeError("The public homepage is unavailable")
    assets = set(re.findall(r'(?:src|href)="(/static/[^"?#]+)"', html))
    for path in sorted(assets):
        with urllib.request.urlopen(URL + path, timeout=25) as response:
            if response.status != 200:
                raise RuntimeError("A homepage static asset is unavailable")
    for path in ["/health/", "/ready/"]:
        with urllib.request.urlopen(URL + path, timeout=25) as response:
            if response.status != 200 or json.load(response).get("status") != "ok":
                raise RuntimeError("The application's health/readiness check failed")


def deploy_version(clients, version, previous):
    preflight(clients)
    clients["elasticbeanstalk"].update_environment(EnvironmentName=ENVIRONMENT, VersionLabel=version)
    try:
        wait_for_version(clients, version)
        verify_https()
    except Exception:
        # Never restart a stopped/paused environment or bypass the budget to roll back.
        environment = preflight(clients)
        if previous and (environment.get("VersionLabel") != previous or environment.get("Health") != "Green"):
            print("Release verification failed; restoring the previous application version", flush=True)
            clients["elasticbeanstalk"].update_environment(EnvironmentName=ENVIRONMENT, VersionLabel=previous)
            wait_for_version(clients, previous)
        raise


def main():
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != "refs/heads/develop"
        or os.environ.get("GITHUB_EVENT_NAME") != "push"
    ):
        raise RuntimeError("Only pushes to the central develop branch can deploy")
    sha = os.environ["GITHUB_SHA"]
    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not run_id.isdigit() or not attempt.isdigit():
        raise RuntimeError("Invalid workflow release identity")
    if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() != sha:
        raise RuntimeError("Checkout does not match the tested commit")
    latest = subprocess.check_output(["git", "ls-remote", "origin", "refs/heads/develop"], text=True).split()[0]
    if latest != sha:
        print("A newer develop commit exists; skipping this outdated release", flush=True)
        return
    import boto3

    session = boto3.Session(region_name=REGION)
    if session.client("sts").get_caller_identity()["Account"] != ACCOUNT:
        raise RuntimeError("Unexpected AWS account")
    clients = {name: session.client(name) for name in ["budgets", "cloudfront", "elasticbeanstalk", "autoscaling"]}
    previous = preflight(clients).get("VersionLabel")
    version = "gha-" + run_id + "-" + attempt + "-" + sha[:12]
    archive = Path("dist/nyc-active-ci.zip")
    archive.parent.mkdir(exist_ok=True)
    subprocess.run(["git", "archive", "--format=zip", "--output=" + str(archive), sha], check=True)
    with zipfile.ZipFile(archive) as bundle:
        if any(
            p.startswith((".github/", ".venv/", "infra/", "docs/"))
            or p.endswith((".sqlite3", ".pem", ".key"))
            or Path(p).name.startswith(".env")
            for p in bundle.namelist()
        ):
            raise RuntimeError("Source bundle contains excluded local/deployment files")
    key = "nyc-active/gha/" + version + ".zip"
    session.client("s3").put_object(Bucket=BUCKET, Key=key, Body=archive.read_bytes(), ServerSideEncryption="AES256")
    eb = clients["elasticbeanstalk"]
    existing = eb.describe_application_versions(ApplicationName=APPLICATION, VersionLabels=[version])[
        "ApplicationVersions"
    ]
    if not existing:
        eb.create_application_version(
            ApplicationName=APPLICATION,
            VersionLabel=version,
            Description="develop " + sha,
            Process=True,
            SourceBundle={"S3Bucket": BUCKET, "S3Key": key},
        )
    for _ in range(30):
        state = eb.describe_application_versions(ApplicationName=APPLICATION, VersionLabels=[version])[
            "ApplicationVersions"
        ][0]["Status"]
        if state == "PROCESSED":
            break
        if state == "FAILED":
            raise RuntimeError("AWS rejected the application bundle")
        time.sleep(4)
    else:
        raise TimeoutError("Application bundle processing did not finish")
    deploy_version(clients, version, previous)
    message = f"Deployed develop `{sha}` as `{version}` to {URL}/\n"
    print(message, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as output:
            output.write(message)


if __name__ == "__main__":
    main()
