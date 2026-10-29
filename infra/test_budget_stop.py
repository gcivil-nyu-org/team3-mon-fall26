import os
import unittest
from unittest.mock import MagicMock, patch

from budget_stop import handler


class BudgetStopTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "ACCOUNT_ID": "123456789012",
                "TEAM_ENVIRONMENT": "team-demo",
                "TEAM_ENVIRONMENT_ID": "e-team",
                "TEAM_ASG": "team-group",
                "TEAM_DISTRIBUTION": "TEAMCF",
                "BUDGET_NAME": "team-budget",
                "TEAM_ORIGIN": "team.example.com",
                "MONTHLY_LIMIT_USD": "15",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.clients = {name: MagicMock() for name in ["budgets", "ec2", "autoscaling", "cloudfront"]}
        self.budget = {
            "TimeUnit": "MONTHLY",
            "BudgetLimit": {"Amount": "15", "Unit": "USD"},
            "CostFilters": {"TagKeyValue": ["user:Project$NYCActive"]},
            "CostTypes": {"IncludeCredit": False},
            "CalculatedSpend": {"ActualSpend": {"Amount": "15", "Unit": "USD"}},
        }
        self.clients["budgets"].describe_budget.return_value = {"Budget": self.budget}
        self.clients["autoscaling"].describe_auto_scaling_groups.return_value = {
            "AutoScalingGroups": [{"Tags": [{"Key": "elasticbeanstalk:environment-id", "Value": "e-team"}]}],
        }
        self.instance = {
            "InstanceId": "i-team",
            "State": {"Name": "running"},
            "Tags": [
                {"Key": "elasticbeanstalk:environment-name", "Value": "team-demo"},
                {"Key": "aws:autoscaling:groupName", "Value": "team-group"},
            ],
        }
        self.clients["ec2"].describe_instances.return_value = {"Reservations": [{"Instances": [self.instance]}]}
        self.clients["cloudfront"].get_distribution_config.return_value = {
            "ETag": "version",
            "DistributionConfig": {"Enabled": True, "Origins": {"Items": [{"DomainName": "team.example.com"}]}},
        }

    def assert_no_changes(self):
        self.clients["autoscaling"].create_or_update_tags.assert_not_called()
        self.clients["autoscaling"].suspend_processes.assert_not_called()
        self.clients["cloudfront"].update_distribution.assert_not_called()
        self.clients["ec2"].stop_instances.assert_not_called()

    def test_below_limit_never_stops_even_with_event_overrides(self):
        self.budget["CalculatedSpend"]["ActualSpend"]["Amount"] = "14.99"
        result = handler({"amount": "999", "instances": ["i-personal"]}, None, self.clients)
        self.assertEqual(result["action"], "below_limit")
        self.assert_no_changes()

    def test_missing_cost_data_does_not_guess_a_spend(self):
        self.budget.pop("CalculatedSpend")
        self.assertEqual(handler({}, None, self.clients)["action"], "cost_data_pending")
        self.assert_no_changes()

    def test_changed_budget_scope_refuses_all_mutations(self):
        self.budget["CostFilters"] = {}
        with self.assertRaises(ValueError):
            handler({}, None, self.clients)
        self.assert_no_changes()

    def test_personal_instance_is_never_stopped(self):
        self.instance["Tags"][0]["Value"] = "personal-homework"
        with self.assertRaises(ValueError):
            handler({}, None, self.clients)
        self.assert_no_changes()

    def test_threshold_suspends_replacement_disables_cf_and_stops_team(self):
        result = handler({}, None, self.clients)
        self.assertEqual(result["action"], "stop_team_environment")
        self.clients["autoscaling"].create_or_update_tags.assert_called_once_with(
            Tags=[
                {
                    "ResourceId": "team-group",
                    "ResourceType": "auto-scaling-group",
                    "Key": "nyc-active:budget-stopped",
                    "Value": "true",
                    "PropagateAtLaunch": False,
                }
            ]
        )
        self.assertEqual(
            [c[0] for c in self.clients["autoscaling"].method_calls[-2:]],
            ["create_or_update_tags", "suspend_processes"],
        )
        processes = self.clients["autoscaling"].suspend_processes.call_args.kwargs["ScalingProcesses"]
        self.assertIn("ReplaceUnhealthy", processes)
        self.assertIn("Launch", processes)
        self.clients["ec2"].stop_instances.assert_called_once_with(InstanceIds=["i-team"])
        updated = self.clients["cloudfront"].update_distribution.call_args.kwargs["DistributionConfig"]
        self.assertFalse(updated["Enabled"])

    def test_dry_run_checks_targets_but_never_stops(self):
        self.assertEqual(handler({"dry_run": True}, None, self.clients)["action"], "dry_run")
        self.assert_no_changes()

    def test_already_stopped_instance_and_disabled_distribution_are_idempotent(self):
        self.instance["State"]["Name"] = "stopped"
        self.clients["cloudfront"].get_distribution_config.return_value["DistributionConfig"]["Enabled"] = False
        handler({}, None, self.clients)
        self.clients["ec2"].stop_instances.assert_not_called()
        self.clients["cloudfront"].update_distribution.assert_not_called()


if __name__ == "__main__":
    unittest.main()
