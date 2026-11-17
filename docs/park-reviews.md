# Park reviews (#40, #39, #42)

After pulling this branch, run `python manage.py migrate`. The migration adds only
the `parks_review` table; it does not rewrite seeded parks, events or accounts.
No new AWS service or paid dependency is needed.

## User flow

1. Open Events, switch to List / Parks, then open a park's details.
2. The Reviews section is public. Members without a review can choose 1–5 stars
   and enter text, then select Post review. Anonymous users get a login link
   that returns to this park's Reviews section.
3. A successful submission redirects back to Reviews. The author sees their
   own review instead of another create form. A duplicate POST is refused.
4. Public community reviews are newest first, ten per page. Author links open
   public profiles. Review text is escaped and preserves line breaks.
5. The full page, Parks cards, and both AJAX and initial Explore panels share
   `parks/includes/rating_badge.html`. Summaries are calculated in SQL on each
   request, so edits and deletions are reflected without cache invalidation.

## Integration boundaries

- Owners edit and delete their own review (#41). Edit opens the review as a form in
  place (`?edit_review=1`), which POSTs to `park_review_edit(pk)`; Delete asks for
  confirmation and POSTs to `park_review_delete(pk)`. Both are POST-only and use
  `ReviewForm`; anyone but the author gets 403 and the review is unchanged.
  Administrator removal is #43.
- Standard Django admin registration permits existing staff with the appropriate
  model permissions to manage Review records. Custom inline administrator
  removal, moderation permissions, and its audit snapshot remain #43/#52.
- #42's freshness tests exercise model editing, deletion and real Django-admin
  deletion; `parks.test_review_owner` covers #41's owner edit and delete.
- Rating summaries use `ParkQuerySet.with_review_summary()` at presentation
  boundaries. Shared event/filter membership, map clustering, event totals,
  event management and account features are unchanged.

## Verification

`python manage.py test parks.test_reviews` covers rating validators and database
constraints, public scoping/order/pagination, CSRF and ownership, duplicate
submission races, escaped content, fresh summaries on every surface and constant
query counts. Run the full Django, infra and Node suites and the existing
Black/flake8/migration checks before merging.
