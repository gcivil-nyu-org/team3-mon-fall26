/* US-2.1 / US-2.2: progressive enhancement; all list/filter links work without JS. */
(() => {
  'use strict';
  const root = document.querySelector('[data-explore]');
  if (!root) return;
  const filtersForm = root.querySelector('.explore-filters');
  filtersForm.querySelector('[name="events_only"]').addEventListener('change', () => filtersForm.requestSubmit());
  // US-2.3: borough and zip-code picks apply immediately; the reload re-fits the map to the results.
  filtersForm.querySelectorAll('[data-autosubmit]').forEach((field) =>
    field.addEventListener('change', () => filtersForm.requestSubmit()));
  // US-2.7: clearing the search box with its built-in × resets results. Enter already submits the
  // form natively (and also fires 'search'), so skip that case to avoid a double submit.
  const search = filtersForm.querySelector('input[name="q"]');
  let searchByEnter = false;
  search?.addEventListener('keydown', (event) => { searchByEnter = event.key === 'Enter'; });
  search?.addEventListener('search', () => {
    if (searchByEnter) { searchByEnter = false; return; }
    if (!search.value.trim()) filtersForm.requestSubmit();
  });
  const dialog = root.querySelector('[data-park-dialog]');
  // A selected park is ordinary URL state. Navigation persists it server-side,
  // so sharing, refresh, no-JS and returning from another hub page agree.
  function parkURL(id) {
    const url = new URL(window.location.href);
    url.searchParams.delete('page');
    if (id) {
      url.searchParams.set('park', id);
      url.searchParams.set('panel', '1');
    } else {
      url.searchParams.delete('park');
      url.searchParams.delete('panel');
    }
    return url;
  }
  if (dialog.open) {
    // Removing the server-rendered open attribute avoids queuing a close event.
    dialog.removeAttribute('open');
    dialog.showModal();
  }
  root.querySelector('[data-close-preview]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => window.location.assign(parkURL(null)));
  if (root.dataset.view !== 'map') return;
  const shell = root.querySelector('[data-map-shell]');
  const container = root.querySelector('#explore-map');
  const fallback = root.querySelector('[data-list-fallback]');
  const errorBox = root.querySelector('[data-map-error]');
  const status = root.querySelector('[data-map-status]');
  // US-2.5 no_park_data: the page renders a message instead of the map, so there is nothing to
  // initialize. Exit before the Leaflet check, the error handlers or any map data request.
  if (!shell || !container) return;
  // US-2.5: the error replaces the (blank) map so it's the first thing seen; the list stays usable.
  function fail() { errorBox.hidden = false; fallback.hidden = false; status.hidden = true; shell.hidden = true; }
  // Without Leaflet there is no map to retry in place, so Try again reloads the page.
  function failHard() { fail(); root.querySelector('[data-map-retry]').addEventListener('click', () => window.location.reload()); }
  if (!window.L || !L.markerClusterGroup) { failHard(); return; }
  shell.hidden = false;
  let map;
  let group;
  function icon(count, cluster = false) {
    const span = document.createElement('span');
    span.className = (cluster ? 'explore-cluster' : 'explore-pin') + (count > 0 ? ' is-active' : '');
    span.textContent = count > 0 ? String(count) : (cluster ? '○' : '');
    return L.divIcon({html: span, className: 'explore-marker', iconSize: cluster ? [46,46] : [30,30]});
  }
  try {
    map = L.map(container, {scrollWheelZoom: false}).setView([40.73, -73.94], 11);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, referrerPolicy: 'strict-origin-when-cross-origin',
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).on('tileerror', fail).addTo(map);
    group = L.markerClusterGroup({showCoverageOnHover: false, maxClusterRadius: 48,
      iconCreateFunction: (cluster) => icon(cluster.getAllChildMarkers().reduce(
        (sum, marker) => sum + marker.options.eventCount, 0), true)
    }).addTo(map);
  } catch (_) { failHard(); return; }
  let mapRequest;
  async function load() {
    mapRequest?.abort();
    mapRequest = new AbortController();
    status.hidden = false;
    status.textContent = 'Loading parks…';
    errorBox.hidden = true;
    try {
      const response = await fetch(container.dataset.mapUrl, {signal: mapRequest.signal, credentials: 'same-origin'});
      if (!response.ok || response.redirected) throw new Error('Map unavailable');
      const data = await response.json();
      group.clearLayers();
      const markers = data.parks.map((park) => {
        const title = `${park.name} · ${park.event_count} matching events`;
        const marker = L.marker([park.lat,park.lng], {icon: icon(park.event_count), eventCount: park.event_count,
          title, alt: title, keyboard: true});
        marker.on('add', () => marker.getElement()?.setAttribute('aria-label', title));
        const tooltip = document.createElement('span');
        tooltip.textContent = `${park.name} · ${park.borough}`;
        marker.bindTooltip(tooltip);
        marker.on('click', () => {
          window.location.assign(parkURL(park.id));
        });
        return marker;
      });
      group.addLayers(markers);
      if (markers.length) map.fitBounds(group.getBounds(), {padding:[36,36], maxZoom:15});
      // US-2.5: zero pins shows the server-rendered empty state (pins already cleared above).
      // Parks may still match but lack coordinates; the unmapped notice below links to List view.
      const emptyState = root.querySelector('[data-map-empty]');
      status.hidden = markers.length > 0 || Boolean(emptyState);
      if (!markers.length && !emptyState) status.textContent = 'None of the matching parks have a map location yet.';
      const unmapped = root.querySelector('[data-unmapped]');
      unmapped.hidden = !data.unmapped_event_count;
      unmapped.replaceChildren();
      if (data.unmapped_event_count) {
        unmapped.append(document.createTextNode(`${data.unmapped_event_count} matching events are at parks without a map location. `));
        const link = document.createElement('a');
        const listURL = new URL(window.location.href);
        listURL.searchParams.set('view','list');
        listURL.searchParams.set('tab','events');
        listURL.searchParams.delete('page');
        link.href = listURL;
        link.textContent = 'See them in List view.';
        unmapped.append(link);
      }
      if (errorBox.hidden) fallback.hidden = true;
    } catch (error) { if (error.name !== 'AbortError') fail(); }
  }
  root.querySelector('[data-map-retry]').addEventListener('click', () => { shell.hidden = false; map.invalidateSize?.(); map.eachLayer(layer => {
    if (layer instanceof L.TileLayer) layer.redraw();
  }); load(); });
  load();
})();
