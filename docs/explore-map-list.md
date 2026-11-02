# Explore map and list integration (US-2.1 / US-2.2)

## Updated sprint scope and split

Yueli owns #16 (US-2.1), #17 (US-2.2), and the newly assigned #78 (US-2.8).

1. Foundation #77 is merged into `develop`.
2. **PR #83** (`feature/explore-filter-base`) supplies the early shared-filter
   step of #17. It is ready for review and must merge first. See
   `explore-filter-contract.md` for Rae's stable interface. It does not close #17.
3. This follow-up reuses #83's filter engine, filter partial and stylesheet
   without replacing them. It adds the map, clustering, map/list and Events/Parks
   presentations and the #78 Events hub navigation.
4. Shared hub header: `{% include "events/_hub_header.html" with active="explore" back=True %}`.
   It is included on Explore, event creation and event detail. The owner of #21
   can include it on the future park-detail page without a view mixin. The
   creation page uses `active="create"`. Only navigation is added to the existing
   creation form; its field/schema work remains with bundle 5.
5. The Events navbar points to `/events/` (default map), the existing Create event
   shortcut remains, and the account menu includes My Events (`?scope=mine`).
   Anonymous users choosing Create Event log in and return to that form.

Merge order: #83, then this follow-up after review. Its diff against `develop`
includes #83 until that PR merges; compare against `feature/explore-filter-base`
to review only the map/list/hub addition. It does not close #18/#19/#20/#70,
#21/#23/#79 or any other teammate's stories.

No models, migrations, credentials, paid map SDK or AWS resources are added.
No live deployment is performed while these changes remain in feature PRs.

## Local preview

```sh
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py sync_parks --if-empty
python manage.py seed_events
python manage.py runserver
```

The sync downloads public NYC Open Data. `seed_events` is optional, local sample
data; it is not run by a deployment hook. Open `/events/` for the default map,
`/events/?view=list&tab=events` for events, or `?view=list&tab=parks` for parks.
Local SQLite contains only local data, separate from the AWS database.

## Shared contract for parallel filter stories

`events/explore.py` is the single query boundary:

- `ExploreFilterForm` validates URL state, preserving valid members of repeated
  parameters and ignoring unknown values. Invalid date ranges are shown as
  validation messages and do not silently turn into partial ranges.
- `matching_events(filters, user=None)` defines event membership.
- `displayed_parks(filters, user=None)` defines park membership and annotates
  `event_count`. It includes parks that independently match the park filters,
  plus parks hosting matching events. Date and skill affect counts, not park
  visibility. My Events scopes force `events_only`.
- `filter_params(filters)` serializes canonical state with `QueryDict.setlist`;
  `explore_url` changes view/tab/page or removes a pill without losing repeated
  values. A filter or view change resets pagination.

Parameters: `q`, `borough`, repeated `zip`, repeated `sport`, repeated `facility`,
`when=today`, `date_from`, `date_to`, `skill`, `scope`, `events_only`, `park`,
`view=map|list` and `tab=events|parks`. Default view is map and default tab events;
both list tabs have 20 cards per page. The old `include_past` option is retired.

The hub stores only validated Explore state in the user's Django session,
including the current page. APIs, previews and invalid pages never replace it.
Explore Events / Back to results / Cancel restore that state; without a saved
search they fall back to `/events/`. Arbitrary `next` or external return URLs
are never stored. Opening a pin or Quick view navigates to the same Explore
URL with `park=<id>&panel=1`; the selected park and preview survive refresh,
sharing and a round trip to event detail. `panel` is presentation-only and does
not change filter-engine membership. Closing removes the selected park/panel;
See events at this park retains the park filter with no panel so the list is
readable. This uses normal navigation and avoids async session-write races.

The basic server-rendered controls and backend predicates establish the shared
interface. Coordinate with the owners of #18/#19/#70/#75/#76 before replacing
their UI: lookahead/pill selectors and richer empty-state/capacity/host controls
remain in their respective stories. Add their widgets to
`templates/events/includes/explore_filters.html` and extend this engine, rather
than making a second set of map/list queries. This branch does not close those
other stories.

`GET /events/api/map/` returns all displayed parks with coordinates (not a page):
`parks: [{id, name, borough, lat, lng, event_count, preview_url}]` and
`unmapped_event_count`. The endpoint uses two data queries regardless of the
number of pins, plus active-park validation when a `park` parameter is supplied.
Personal scopes require login and use the authenticated user;
caller-supplied user IDs are never accepted.

Cluster icons sum matching events rather than park counts. Zero-event pins and
clusters are muted. Data failures leave the server-rendered list available.
JavaScript-disabled browsers see a notice and the list immediately.

## Park preview and future park detail integration

Pins and Quick view open a small server-rendered read-only dialog using the
shared filter engine. `/events/parks/<pk>/preview/` also exposes the same partial
for the future panel. This is an integration point for US-2.9, not the full park
panel/detail story. Without JS, Quick view still navigates to a readable preview
and a close link alongside the server-rendered results. If #21 provides a named
`park_detail` route accepting `pk`, Park details links appear automatically;
until then no broken `/parks/<id>/` link is emitted. Ratings show Not yet rated
until #42 exists. Confirm those route/panel contracts with their story owners.

## Map assets and external tiles

Leaflet 1.9.4 and Leaflet.markercluster 1.5.3 are vendored under `static/vendor/`
with their original licenses and source maps. No CDN JavaScript is required.
The visible base map uses OpenStreetMap standard raster tiles, with visible
attribution and an origin-only cross-origin Referer (required by the tile usage
policy). Do not prefetch/offline-download tiles or bypass browser caching.

References:
- https://leafletjs.com/examples/quick-start/
- https://github.com/Leaflet/Leaflet.markercluster
- https://operations.osmfoundation.org/policies/tiles/

## Verification

```sh
python manage.py test
python -m unittest discover -s infra -p 'test_*.py'
node --test events/js/explore.test.cjs
python manage.py makemigrations --check --dry-run
```

`events/test_explore.py` checks map/list membership and count agreement,
retired/unmapped parks, events-only, repeated URL values and pagination, public
exclusions and personal scope isolation, whole-token facilities and zip codes,
NY date boundaries, invalid ranges, escaping, preview filtering, and constant
map query count. Browser checks should include clustering, pin preview,
Map/List/Events/Parks navigation, a repeated filter selection, and map failure.

Hub regressions in `events/test_hub.py` cover the actual login/next round trip,
active navigation, repeated filter/page/panel restoration, session isolation,
invalid and unrelated URL parameters, and the reusable park-page header.
`events/js/explore.test.cjs` runs the shipped map controller with small DOM and
Leaflet adapters to check cluster event sums, muted pins, pin/close navigation,
unmapped notices, checkbox submission and data/tile/library failures. GitHub CI
runs it with Node's built-in runner; there are no npm dependencies.

Validation on 2026-10-24: 169 Django tests, 30 infrastructure safeguard tests and
5 Node interaction tests passed; migration drift, dependency consistency,
Django checks, production compressed static manifest and whitespace checks
passed. Edge verified default clustering, pin selection, event-detail round
trip with the selected park restored, viewing park events without reopening
the preview, Events/Parks and Map/List switches with two zip values, anonymous
Create Event login/return, and the authenticated My Events shortcut. Local
preview uses public NYC park data and optional sample events, not AWS data.
