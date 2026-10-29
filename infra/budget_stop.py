"""Hourly, delayed cost protection for this team's existing test environment."""

from decimal import Decimal
import os

PROCESSES = [
    "Launch",
    "Terminate",
    "AddToLoadBalancer",
    "AlarmNotification",
    "AZRebalance",
    "HealthCheck",
    "InstanceRefresh",
    "ReplaceUnhealthy",
    "ScheduledActions",
]


def handler(event, context, clients=None):
    if clients is None:
        import boto3

        clients = {name: boto3.client(name) for name in ["budgets", "ec2", "autoscaling", "cloudfront"]}
    account = os.environ["ACCOUNT_ID"]
    environment = os.environ["TEAM_ENVIRONMENT"]
    environment_id = os.environ["TEAM_ENVIRONMENT_ID"]
    group_name = os.environ["TEAM_ASG"]
    distribution_id = os.environ["TEAM_DISTRIBUTION"]
    limit = Decimal(os.environ.get("MONTHLY_LIMIT_USD", "15"))
    budget = clients["budgets"].describe_budget(
        AccountId=account,
        BudgetName=os.environ["BUDGET_NAME"],
    )["Budget"]
    if (
        budget.get("TimeUnit") != "MONTHLY"
        or budget.get("CostFilters", {}).get("TagKeyValue") != ["user:Project$NYCActive"]
        or budget.get("CostTypes", {}).get("IncludeCredit") is not False
        or Decimal(budget["BudgetLimit"]["Amount"]) != limit
        or budget["BudgetLimit"]["Unit"] != "USD"
    ):
        raise ValueError("Budget scope or amount changed; refusing to stop resources")
    actual = budget.get("CalculatedSpend", {}).get("ActualSpend")
    if not actual:
        return {"action": "cost_data_pending", "limit_usd": str(limit)}
    spent = Decimal(actual["Amount"])
    if actual["Unit"] != "USD":
        raise ValueError("Unexpected cost unit")
    if spent < limit:
        return {"action": "below_limit", "spent_usd": str(spent), "limit_usd": str(limit)}

    # Validate every target before any mutation. Never accept target overrides from events.
    groups = clients["autoscaling"].describe_auto_scaling_groups(
        AutoScalingGroupNames=[group_name],
    )["AutoScalingGroups"]
    if len(groups) != 1:
        raise ValueError("Expected exactly one team Auto Scaling group")
    group_tags = {tag["Key"]: tag["Value"] for tag in groups[0].get("Tags", [])}
    if group_tags.get("elasticbeanstalk:environment-id") != environment_id:
        raise ValueError("Auto Scaling group does not belong to the team environment")
    reservations = clients["ec2"].describe_instances(
        Filters=[
            {"Name": "tag:elasticbeanstalk:environment-id", "Values": [environment_id]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ]
    )["Reservations"]
    running = []
    for reservation in reservations:
        for instance in reservation["Instances"]:
            tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
            if (
                tags.get("elasticbeanstalk:environment-name") != environment
                or tags.get("aws:autoscaling:groupName") != group_name
            ):
                raise ValueError("Instance target does not belong to the expected team group")
            if instance["State"]["Name"] == "running":
                running.append(instance["InstanceId"])
    response = clients["cloudfront"].get_distribution_config(Id=distribution_id)
    config = response["DistributionConfig"]
    if [origin["DomainName"] for origin in config["Origins"]["Items"]] != [os.environ["TEAM_ORIGIN"]]:
        raise ValueError("Distribution origin changed; refusing to stop resources")
    result = {
        "action": "stop_team_environment",
        "spent_usd": str(spent),
        "limit_usd": str(limit),
        "instances": running,
        "distribution": distribution_id,
    }
    if event.get("dry_run") is True:
        return {**result, "action": "dry_run"}
    # Latch the budget stop before pausing: deployment IAM refuses to resume
    # this group while tagged, including Beanstalk's internal cleanup calls.
    clients["autoscaling"].create_or_update_tags(
        Tags=[
            {
                "ResourceId": group_name,
                "ResourceType": "auto-scaling-group",
                "Key": "nyc-active:budget-stopped",
                "Value": "true",
                "PropagateAtLaunch": False,
            }
        ]
    )
    # Pausing replacement is essential: stopping an instance alone can make ASG
    # launch a billable replacement. Preserve the instance and its disk, not delete it.
    clients["autoscaling"].suspend_processes(
        AutoScalingGroupName=group_name,
        ScalingProcesses=PROCESSES,
    )
    if config["Enabled"]:
        config["Enabled"] = False
        clients["cloudfront"].update_distribution(
            Id=distribution_id,
            IfMatch=response["ETag"],
            DistributionConfig=config,
        )
    if running:
        clients["ec2"].stop_instances(InstanceIds=running)
    print(result)
    return result
