import copy
import os
import unittest
from unittest.mock import MagicMock, call, patch

import deploy_ci as deploy


class AutomaticDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.clients = {name: MagicMock() for name in ["budgets", "cloudfront", "elasticbeanstalk", "autoscaling"]}
        self.budget = {
            "TimeUnit": "MONTHLY", "BudgetLimit": {"Amount": "15", "Unit": "USD"},
            "CostFilters": {"TagKeyValue": ["user:Project$NYCActive"]},
            "CostTypes": {"IncludeCredit": False},
            "CalculatedSpend": {"ActualSpend": {"Amount": "1", "Unit": "USD"}},
        }
        self.clients["budgets"].describe_budget.return_value = {"Budget": self.budget}
        self.distribution = {"Enabled": True, "Origins": {"Items": [{"DomainName": deploy.ORIGIN}]}}
        self.clients["cloudfront"].get_distribution.return_value = {
            "Distribution": {"DistributionConfig": self.distribution}}
        self.environment = {"ApplicationName": deploy.APPLICATION, "EnvironmentId": deploy.ENVIRONMENT_ID,
                            "CNAME": deploy.ORIGIN, "Status": "Ready", "Health": "Green", "VersionLabel": "previous"}
        self.clients["elasticbeanstalk"].describe_environments.return_value = {"Environments": [self.environment]}
        self.group = {"DesiredCapacity": 1, "MinSize": 1, "MaxSize": 1, "SuspendedProcesses": [],
                      "Tags": [{"Key": "elasticbeanstalk:environment-id", "Value": deploy.ENVIRONMENT_ID}]}
        self.clients["autoscaling"].describe_auto_scaling_groups.return_value = {"AutoScalingGroups": [self.group]}

    def test_budget_threshold_blocks_deployment(self):
        self.budget["CalculatedSpend"]["ActualSpend"]["Amount"] = "15"
        with self.assertRaisesRegex(RuntimeError, "budget was reached"):
            deploy.deploy_version(self.clients, "new", "previous")
        self.clients["elasticbeanstalk"].update_environment.assert_not_called()

    def test_missing_cost_data_blocks_deployment(self):
        self.budget.pop("CalculatedSpend")
        with self.assertRaisesRegex(RuntimeError, "Cost data"):
            deploy.preflight(self.clients)

    def test_changed_budget_scope_is_rejected(self):
        self.budget["CostFilters"] = {}
        with self.assertRaisesRegex(RuntimeError, "budget configuration changed"):
            deploy.preflight(self.clients)

    def test_disabled_distribution_is_not_reenabled(self):
        self.distribution["Enabled"] = False
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            deploy.preflight(self.clients)
        self.clients["cloudfront"].update_distribution.assert_not_called()

    def test_budget_paused_group_is_not_resumed(self):
        self.group["SuspendedProcesses"] = [{"ProcessName": "Launch"}]
        with self.assertRaisesRegex(RuntimeError, "paused"):
            deploy.preflight(self.clients)
        self.clients["autoscaling"].resume_processes.assert_not_called()

    def test_budget_stop_tag_blocks_even_if_processes_were_manually_resumed(self):
        self.group["Tags"].append({"Key": deploy.BUDGET_STOP_TAG, "Value": "true"})
        for require_ready in [True, False]:
            with self.assertRaisesRegex(RuntimeError, "paused"):
                deploy.preflight(self.clients, require_ready=require_ready)

    def test_in_progress_release_allows_beanstalk_temporary_health_check_pause(self):
        self.group["SuspendedProcesses"] = [{"ProcessName": "HealthCheck"}]
        self.environment["Status"] = "Updating"
        self.assertEqual(deploy.preflight(self.clients, require_ready=False), self.environment)

    def test_replaced_environment_is_rejected(self):
        self.environment["EnvironmentId"] = "e-personal"
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            deploy.preflight(self.clients)

    def test_expanded_capacity_is_rejected(self):
        self.group["MaxSize"] = 2
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            deploy.preflight(self.clients)

    @patch("deploy_ci.verify_https")
    @patch("deploy_ci.wait_for_version")
    def test_success_only_updates_the_existing_application_version(self, wait, verify):
        deploy.deploy_version(self.clients, "new", "previous")
        self.clients["elasticbeanstalk"].update_environment.assert_called_once_with(
            EnvironmentName=deploy.ENVIRONMENT, VersionLabel="new")
        wait.assert_called_once_with(self.clients, "new")
        verify.assert_called_once()

    @patch("deploy_ci.verify_https", side_effect=OSError("unavailable"))
    @patch("deploy_ci.wait_for_version")
    def test_failed_verification_restores_previous_version(self, wait, verify):
        updated = {**self.environment, "VersionLabel": "new"}
        self.clients["elasticbeanstalk"].describe_environments.side_effect = [
            {"Environments": [self.environment]}, {"Environments": [updated]}]
        with self.assertRaisesRegex(OSError, "unavailable"):
            deploy.deploy_version(self.clients, "new", "previous")
        self.assertEqual(self.clients["elasticbeanstalk"].update_environment.call_args_list, [
            call(EnvironmentName=deploy.ENVIRONMENT, VersionLabel="new"),
            call(EnvironmentName=deploy.ENVIRONMENT, VersionLabel="previous")])

    @patch("deploy_ci.verify_https", side_effect=OSError("unavailable"))
    @patch("deploy_ci.wait_for_version")
    def test_failed_release_restores_unhealthy_environment_even_if_label_did_not_change(self, wait, verify):
        unhealthy = {**self.environment, "Health": "Red"}
        self.clients["elasticbeanstalk"].describe_environments.side_effect = [
            {"Environments": [self.environment]}, {"Environments": [unhealthy]}]
        with self.assertRaisesRegex(OSError, "unavailable"):
            deploy.deploy_version(self.clients, "new", "previous")
        self.assertEqual(self.clients["elasticbeanstalk"].update_environment.call_args_list, [
            call(EnvironmentName=deploy.ENVIRONMENT, VersionLabel="new"),
            call(EnvironmentName=deploy.ENVIRONMENT, VersionLabel="previous")])

    @patch("deploy_ci.verify_https", side_effect=OSError("unavailable"))
    @patch("deploy_ci.wait_for_version")
    def test_failed_release_does_not_bypass_new_budget_stop(self, wait, verify):
        exhausted = copy.deepcopy(self.budget)
        exhausted["CalculatedSpend"]["ActualSpend"]["Amount"] = "15"
        self.clients["budgets"].describe_budget.side_effect = [{"Budget": self.budget}, {"Budget": exhausted}]
        with self.assertRaisesRegex(RuntimeError, "budget was reached"):
            deploy.deploy_version(self.clients, "new", "previous")
        self.assertEqual(self.clients["elasticbeanstalk"].update_environment.call_count, 1)

    def test_other_repositories_and_pull_requests_cannot_deploy(self):
        for repository, ref, event in [
            ("someone/fork", "refs/heads/develop", "push"),
            (deploy.REPOSITORY, "refs/heads/main", "push"),
                (deploy.REPOSITORY, "refs/heads/develop", "pull_request")]:
            with self.subTest(repository=repository, ref=ref, event=event), patch.dict(os.environ, {
                    "GITHUB_REPOSITORY": repository, "GITHUB_REF": ref, "GITHUB_EVENT_NAME": event}):
                with self.assertRaisesRegex(RuntimeError, "central develop"):
                    deploy.main()

    @patch("deploy_ci.subprocess.check_output", side_effect=["a" * 40, "b" * 40 + "\trefs/heads/develop"])
    def test_outdated_commit_is_skipped_before_any_aws_access(self, output):
        with patch.dict(os.environ, {"GITHUB_REPOSITORY": deploy.REPOSITORY, "GITHUB_REF": "refs/heads/develop",
                                     "GITHUB_EVENT_NAME": "push", "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "123"}):
            deploy.main()


if __name__ == "__main__":
    unittest.main()
