# Shared Explore filter contract (first part of US-2.2 / #17)

Map, Events list, Parks list and the park panel must use the same membership
rules and URL state. This PR extracts that common backend before the map/UI
changes so bundle 3 can build on a stable interface.

**Depends on PR #77. Merge #77 before this PR.** No models, migrations, map
libraries, AWS resources or hub/navigation changes are added by this step.
This does not finish or close #17, #16, #78, or another person's filter story.

## Entry points

```python
from events.explore import parse_filters, matching_events, displayed_parks

form, filters = parse_filters(request.GET)
events = matching_events(filters, request.user)  # chronological, no duplicate events
parks = displayed_parks(filters, request.user)  # annotated .event_count
```

Anonymous personal scopes are rejected by `personal_scope_redirect` at each HTTP
entry point. Direct helper calls without an authenticated user return no personal
events. Never accept a user ID from a query parameter.

The existing feed view and JSON feed already use this engine. The following map
PR and park panel should call these functions instead of copying filter logic.
The existing feed presentation stays in place until the map/list PR lands.

## Parameters and membership

| Parameter | Contract |
|---|---|
| `q` | Trimmed, at most 100 chars; park name/address/location or event title/description/meeting spot |
| `borough` | `parks.Borough` code |
| repeated `zip` | Exact five-digit comma-separated park zip tokens; OR |
| repeated `sport` | `Activity` values; OR. Events match sport; independently matching parks use active mapped facilities. A sport without a facility mapping does not narrow parks. |
| repeated `facility` | Exact `Facility.SPORT_FIELDS` tokens on active facilities; AND |
| `when=today` | New York calendar date |
| `date_from`, `date_to` | Inclusive ISO date range; invalid/reversed ranges show a message and ignore both endpoints |
| `skill` | Specific level also includes all-skill events |
| `scope` | `all`, `mine`, `attending`, `hosting`; personal scopes force `events_only` and include future cancelled/removed events |
| `events_only=1` | Only parks with a positive matching-event count |
| `park` | Active park ID; invalid, unknown or retired IDs ignored |
| `view`, `tab` | Preserved state: map/list, events/parks; defaults map/events. Presentation follows in the next PR. |

Public events are upcoming scheduled events at active parks. Displayed parks
are active parks independently matching park filters **plus** parks hosting a
matching event. Date/skill affect event counts, not independent park membership.
All counts come from this exact matching-event queryset. EXISTS and a correlated
count avoid multiplying events when a park has several matching facilities.

## Bundle 3 handoff (Rae)

- Basic predicates and validating fields are centralized in `events/explore.py`.
  Extend these rather than writing a second map/list implementation.
- Shared controls: `templates/events/includes/explore_filters.html`. The first
  step uses ordinary form controls; replace/extend those widgets for the zip,
  sport and facility lookahead selectors in #18/#19.
- The selected values are repeated GET parameters, e.g.
  `?zip=11211&zip=11222&sport=basketball&sport=tennis`.
- `filter_params(filters)` returns a canonical QueryDict; `filter_pills(filters)`
  removes exactly one selected value. `explore_url` keeps repeated parameters
  on pagination and resets the page on a view/tab/filter change.
- Empty/error state variants belong to #20. The map code will provide error and
  empty-state integration points in the follow-up PR.
- Keep presentation out of the query functions. Choose eager loading in each
  view so a map request does not load facilities or profiles it doesn't use.

## Remaining Yueli work

- #17 presentation: Map/List toggle, Events/Parks list tabs and park cards.
- #16: Leaflet, event-count clustering, unpaginated map endpoint, missing-location
  notice and no-JavaScript fallback.
- #78: shared Events hub header, Explore/Create main toggle, Events navbar label,
  My Events menu link, and session restoration of the last results query.

The local `feature/explore-map-list` branch retains the map prototype; it will be
rebased onto this shared base. Coordinate park detail links (#21) and the full
side/bottom panel (#79) with their owners rather than closing those stories here.

## Verification

```sh
python manage.py test
python -m unittest discover -s infra -p 'test_*.py'
python manage.py makemigrations --check --dry-run
```

`events/test_explore_filters.py` exercises event/park/count agreement, input
validation, multi-value state and pagination, personal-scope isolation, NY date
boundaries, whole-token zip/facility matching, retirement, unknown park handling,
and constant query count. Existing feed tests now assert that the retired
`include_past` parameter cannot expose past events.
