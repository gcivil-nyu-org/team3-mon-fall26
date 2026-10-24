"""Keep uploaded media private, prefix-scoped and free of always-on resources."""
import json
from pathlib import Path
import unittest


INFRA = Path(__file__).resolve().parent
BUCKET = "arn:aws:s3:::nyc-active-media-287872994760-us-east-1"


def load(name):
    return json.loads((INFRA / name).read_text())


class MediaStorageTests(unittest.TestCase):
    def test_instance_role_can_only_read_write_and_delete_media_objects(self):
        [statement] = load("media-storage-policy.json")["Statement"]
        self.assertEqual(statement["Effect"], "Allow")
        self.assertEqual(sorted(statement["Action"]), ["s3:DeleteObject", "s3:GetObject", "s3:PutObject"])
        self.assertEqual(statement["Resource"], BUCKET + "/media/*")
        self.assertNotIn("Condition", statement)

    def test_bucket_policy_grants_nothing_and_requires_https(self):
        statements = load("media-bucket-policy.json")["Statement"]
        self.assertEqual({statement["Effect"] for statement in statements}, {"Deny"})
        [https] = statements
        self.assertEqual(https["Condition"], {"Bool": {"aws:SecureTransport": "false"}})
        self.assertEqual(sorted(https["Resource"]), [BUCKET, BUCKET + "/*"])

    def test_lifecycle_cleans_up_without_expiring_current_photos(self):
        [rule] = load("media-bucket-lifecycle.json")["Rules"]
        self.assertEqual(rule["Status"], "Enabled")
        self.assertEqual(rule["Filter"], {"Prefix": "media/"})
        self.assertNotIn("Expiration", rule)
        self.assertNotIn("Transitions", rule)
        self.assertEqual(rule["AbortIncompleteMultipartUpload"], {"DaysAfterInitiation": 1})

    def test_media_is_not_served_through_another_cloudfront_origin(self):
        # The budget guard and deploy script refuse to act if the distribution gains an origin.
        origins = load("cloudfront.json")["Origins"]
        self.assertEqual(origins["Quantity"], 1)
        self.assertEqual([origin["Id"] for origin in origins["Items"]], ["nyc-active-beanstalk"])


if __name__ == "__main__":
    unittest.main()
