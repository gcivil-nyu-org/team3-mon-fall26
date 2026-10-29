# Costs and the requested monthly stop protection

The user authorized automatic stopping of the **team test environment only**
after AWS reports US$15 monthly usage before promotional credit deductions.
The separate `swe1-app` individual assignment is outside this control.

## Estimate

For one continuously running instance in us-east-1, using a 730-hour month:

| Component | Rate | Approximate monthly cost before credits/free allowances |
| --- | --- | --- |
| Linux t3.micro | $0.0104/hour | $7.59 |
| 8 GiB gp3 disk, baseline IOPS/throughput | $0.08/GB-month | $0.64 |
| One public IPv4 address | $0.005/hour | $3.65 |
| Total baseline | | $11.88 |

CloudFront requests and traffic, S3 source bundles, logs and management tasks
are separate usage items. Applicable free allowances/credits can cover them.
Do not assume unlimited free traffic. The hourly guard uses Lambda (128 MiB)
and EventBridge; it does not run chargeable Cost Explorer usage queries.
Certificate issuance/renewal uses the existing instance with no new EC2 or ALB.
The separate individual assignment consumes the same account credit balance.

Online account testing runs PostgreSQL on this same instance and existing disk;
there is no separate RDS, EC2 or load-balancer hourly fee. Hourly compressed
backups add small S3 storage/request usage in the project-tagged
`nyc-active-db-backups-287872994760-us-east-1` bucket. Timestamped backups expire
after seven days; one latest dump remains. Size and request charges grow with
data/traffic, so this is not an unlimited free database. Review disk space and
backup timestamps as the team adds data.

Uploaded media such as profile photos use the project-tagged
`nyc-active-media-287872994760-us-east-1` bucket. There is no always-on resource:
only S3 Standard storage ($0.023/GB-month), PUT requests ($0.005 per 1,000) and
GET requests ($0.0004 per 1,000), plus transfer beyond AWS's monthly free
allowance. Photos are 512px JPEGs, so a thousand profiles take well under 100 MB.
Pages use presigned URLs rather than CloudFront, so the distribution keeps its
single Beanstalk origin and the guard's origin check stays valid. A lifecycle rule
aborts incomplete uploads and removes noncurrent versions after a day.

Automatic develop deployment uses standard GitHub-hosted Linux runners for this
public repository. It adds no AWS build service or server. Source bundles use a
dedicated prefix in the existing S3 bucket; storage/request usage can still accrue.
No GitHub cache or artifact uploads are configured. The deployment script checks
the same budget and refuses to publish while the team group/distribution is
paused; it does not restart resources after the budget guard stops them.

Rates were checked against the AWS Price List API and official VPC pricing.
Examples are estimates, not a bill or guaranteed credit deduction rate:

- https://aws.amazon.com/vpc/pricing/
- https://aws.amazon.com/elasticbeanstalk/pricing/
- https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/

## Installed protection

- Budget: `nyc-active-monthly-15-usd`, MONTHLY COST, USD 15
- Filter: activated cost allocation tag `Project=NYCActive`
- Promotional credits and refunds are excluded from the usage measurement
- Lambda: `nyc-active-budget-stop`, source `infra/budget_stop.py`
- EventBridge: `nyc-active-budget-check-hourly`, every hour
- Log retention: seven days

The guard always reads AWS Budgets itself; event payloads cannot supply a fake
spend, a different instance, or a different threshold. It verifies the budget
scope and validates the environment tags and CloudFront origin before changing
anything. Its IAM role can stop only instances tagged with this team's
environment ID, suspend only this team's Auto Scaling group, and update only
this team's CloudFront distribution. It has no terminate/start permissions.

Once AWS reports spend >= $15, it:

1. Sets `nyc-active:budget-stopped=true` on the team's Auto Scaling group, then
   suspends launch, replacement and related processes. The deployment role is
   explicitly denied resuming a group carrying that tag and cannot clear it.
   Otherwise
   stopping an instance could cause Auto Scaling to launch a replacement.
2. Disables the team's CloudFront distribution.
3. Stops the team's running EC2 instance, preserving its disk and data.

It does not delete resources or modify the personal assignment. Resuming is
manual: review the budget first, clear the budget-stop tag with owner permissions,
start the team instance, resume the suspended
Auto Scaling processes, and re-enable CloudFront using its latest ETag. If the
same monthly spend remains >= $15, the next check will stop it again. It never
automatically resumes at the start of a new month.

## Limits of this protection

This is **not an exact or instantaneous hard cap**. AWS Budgets data can lag
8–12 hours or longer, plus up to an hour for the scheduled check. Cost Explorer
was initialized and the project cost tag activated during setup; initial data
and historical tag backfill can take time. A zero or missing calculated spend
while data is preparing does not mean the environment used no resources.

Stopping compute also stops the colocated PostgreSQL and its backup timer.
Existing encrypted S3 backups and uploaded photos remain available and their
storage can still accrue charges. With the instance stopped, no new photo links
are signed and issued links expire within an hour. The guard does not delete
data, backups or photos. Stopping compute does not
remove the EBS disk or Elastic IP; these can continue
to consume roughly $4.29/month at the above baseline. Shared S3 source storage
is not included in the project-tag budget because its bucket also serves the
personal assignment. Future untagged resources, new services or database
instances are not automatically covered by this guard. Review the scope when
adding infrastructure.

The AWS account remains on its Free plan. Under that plan, exhaustion of
credits or plan expiration suspends the account unless the owner upgrades;
this is separate from the $15 team guard. Never silently upgrade the account.

- https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html
- https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/
- https://aws.amazon.com/free/free-tier-faqs/
