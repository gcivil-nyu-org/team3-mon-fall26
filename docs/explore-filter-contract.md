# Shared Explore filter contract (first part of US-2.2 / #17)

Map, Events list, Parks list and the park panel must use the same membership
rules and URL state. This PR extracts that common backend before the map/UI
changes so bundle 3 can build on a stable interface.

Built on the shared schema from merged PR #77. No models, migrations, map
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

## Lookahead selector (US-2.3 / #18, reused by #19)

Borough and zip codes sit in the main filter row and apply as soon as they change
(`data-autosubmit`); the reload re-fits the map to the matching parks. Zip codes use
`static/js/lookahead-select.js` + `static/css/lookahead-select.css`, a dependency-free
progressive enhancement of any `<select multiple data-lookahead>`:

- typing narrows options by label prefix or word prefix ("112" -> 11201, 11211; never 10112);
- click or Enter adds a removable pill, `×` or Backspace removes one; arrows/Escape work;
- the hidden `<select>` stays the source of truth, so the form still submits repeated
  `?zip=` values and works unchanged without JavaScript.

To reuse it for sports/facilities (#19), give the field a `forms.SelectMultiple` widget with
`data-lookahead`, `data-autosubmit`, `data-placeholder` and `data-empty` attrs (see the `zip`
field in `ExploreFilterForm`) and move it out of the "More filters" loop. Zip options come
from `zip_code_choices()`: distinct five-digit zips of active parks.
Tests: `events/test_location_filters.py`, `events/js/lookahead-select.test.cjs`.

## Sport and facility filters (US-2.4 / #19)

Sport sits in the main row and Facilities under "More filters"; both use the same lookahead
widget and apply on change. "More filters" stays open while a facility is selected.

- `?sport=` (repeated, OR): events match on `activity`. A park is shown if it has an *active*
  facility for any selected sport (`ACTIVITY_FACILITY_KEYS`) or hosts a matching event. A sport
  with no facility type (e.g. Running) can be played at any park, so it does not narrow parks;
  a mixed pick such as Basketball + Running therefore keeps every park (OR).
- `?facility=` (repeated, AND): parks must have every selected active facility, matched as exact
  comma-separated tokens (`regulation_soccer` never matches `nonregulation_soccer`). Every event
  at those parks counts, whatever its sport; combine with `?sport=` to narrow events too.
- One EXISTS per facility key keeps parks and events unduplicated when a park has several courts.
Tests: `events/test_sport_facility_filters.py`, `events/js/lookahead-select.test.cjs`.

## Empty and error states (US-2.5 / #20)

`templates/events/includes/empty_state.html` renders every Explore empty/error message from one
`state` variable; `EventListView` picks it with `explore_empty_state()` (`empty_state` for the
list, `map_empty_state` for the map overlay):

| State | When | Actions |
|---|---|---|
| `no_events` | Events tab, no matching events | Clear filters, Show parks instead, Create Event |
| `no_matches` | Parks tab or map, no parks and no events | Clear filters |
| `events_only_empty` | "Only parks with events" on (All events scope), no events | Turn it off, Clear filters |
| `no_park_data` | No active parks at all (sync not run) - replaces map and lists | Try again |
| `map_error` | Map data request, tiles or Leaflet fail (client-side) | Try again, Switch to List |

Clear filters removes every filter but keeps view, tab and the Show scope. Personal scopes
force "Only parks with events", so they get `no_matches` instead of `events_only_empty`.
With zero pins the map shows the server-rendered overlay; on failure the error replaces the
map and the list stays usable. My Events empty states belong to #76.
Tests: `events/test_empty_states.py`, `events/js/explore.test.cjs`.

## Keyword search (US-2.7 / #70)

One search bar sits on its own row above the other filters (`?q=`, trimmed, max 100 characters,
case-insensitive). Parks match on `name`, `address` or `location` (cross-streets), and every
event at a matching park counts. Events also match on their own `title`, `description` or
`meeting_spot`, and an event hit brings its park onto the map and Parks tab even when the park's
own text doesn't match. The keyword is AND'd with every other filter. Enter or the Search button
applies it; the box's built-in × clears it and resets results; the "Search: …" pill and Clear
filters remove it. No matches fall through to the US-2.5 empty states.
Tests: `events/test_keyword_search.py`.

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
