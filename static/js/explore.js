/* US-2.1 / US-2.2: progressive enhancement; all list/filter links work without JS. */
(() => {
  'use strict';
  const root = document.querySelector('[data-explore]');
  if (!root) return;
  const filtersForm = root.querySelector('.explore-filters');
  // The filter fields are replaced after each results update (#123), so their listeners are attached here.
  function bindFilterFields() {
    filtersForm.querySelector('[name="events_only"]').addEventListener('change', () => filtersForm.requestSubmit());
    // US-2.3: borough and zip-code picks apply immediately.
    const autosubmitFields = Array.from(filtersForm.querySelectorAll('[data-autosubmit]'));
    const phaseBoxes = autosubmitFields.filter((field) => field.name === 'phase');
    autosubmitFields.forEach((field) => field.addEventListener('change', () => {
      // US-4.9 (#118): at least one phase stays checked, because none would match nothing. Unchecking the
      // last one checks it again instead of submitting. (Without JavaScript, the server shows the default.)
      if (field.name === 'phase' && !phaseBoxes.some((box) => box.checked)) {
        field.checked = true;
        return;
      }
      filtersForm.requestSubmit();
    }));
    // US-2.7: clearing the search box with its built-in × resets results. Enter already submits the
    // form natively (and also fires 'search'), so skip that case to avoid a double submit.
    const search = filtersForm.querySelector('input[name="q"]');
    let searchByEnter = false;
    search?.addEventListener('keydown', (event) => { searchByEnter = event.key === 'Enter'; });
    search?.addEventListener('search', () => {
      if (searchByEnter) { searchByEnter = false; return; }
      if (!search.value.trim()) filtersForm.requestSubmit();
    });
  }
  bindFilterFields();
  // #123: filter changes update the results in place. Without JavaScript (or without park data, where
  // there's no list to update) the form submits normally.
  filtersForm.addEventListener('submit', (event) => {
    if (!fallback) return;
    event.preventDefault();
    const url = new URL(filtersForm.action || window.location.href, window.location.href);
    url.search = new URLSearchParams(new FormData(filtersForm)).toString();
    return navigateExplore(url);
  });
  const dialog = root.querySelector('[data-park-dialog]');
  const preview = root.querySelector('[data-park-preview-content]');
  const closeButton = root.querySelector('[data-close-preview]');
  const csrf = root.querySelector('[name="csrfmiddlewaretoken"]').value;
  const history = window.history;
  let panelId = dialog.dataset.panelId;
  let panelTrigger;
  let transition = 0;
  let pendingPreview;
  let saveQueue = Promise.resolve();

  function parkURL(id, url = new URL(window.location.href)) {
    if (id) url.searchParams.set('panel', String(id) === '1' ? '01' : id);
    else url.searchParams.delete('panel');
    return url;
  }
  function panelIn(url) {
    const id = url.searchParams.get('panel') || '';
    return /^[0-9]{1,10}$/.test(id) && Number(id) > 0 ? String(Number(id)) : '';
  }
  // Normalize the old park=<id>&panel=1 form before editing this history entry.
  const initialURL = new URL(window.location.href);
  if (initialURL.searchParams.get('panel') === '1' && initialURL.searchParams.has('park')) {
    initialURL.searchParams.delete('park');
    history.replaceState(history.state, '', parkURL(panelId, initialURL));
  }
  if (dialog.open) {
    // Removing the server-rendered open attribute avoids queuing a close event.
    dialog.removeAttribute('open');
    dialog.showModal();
  }

  // #123: two addresses show the same results when they differ only in the view, the open panel, empty
  // values or defaults, so switching Map/List or opening a panel never refetches the results.
  const DEFAULTS = {page: '1', scope: 'all', tab: 'events'};
  function stateKey(url, ignore) {
    return Array.from(url.searchParams)
      .filter(([name, value]) => value !== '' && !ignore.includes(name) && DEFAULTS[name] !== value)
      .map(([name, value]) => `${name}=${value}`).sort().join('&');
  }
  const resultsKey = (url) => stateKey(url, ['view', 'panel']);
  // Map pins don't depend on the list tab or page.
  const pinsKey = (url) => stateKey(url, ['view', 'panel', 'tab', 'page']);
  let renderedKey = resultsKey(new URL(window.location.href));

  function saveExplore(url, current) {
    const save = saveQueue.then(async () => {
      if (current !== transition) return;
      const response = await fetch(root.dataset.stateUrl, {
        method: 'POST', credentials: 'same-origin',
        headers: {'X-CSRFToken': csrf}, body: url.searchParams,
      });
      if (!response.ok || response.redirected) throw new Error('Could not save Explore state');
    });
    saveQueue = save.catch(() => {});
    return save;
  }

  async function navigateExplore(url, {previewURL, trigger, fromHistory = false} = {}) {
    const current = ++transition;
    pendingPreview?.abort();
    const before = new URL(window.location.href);
    // #123: new filters, tab or page fetch the changed results (and the open panel, which follows the filters).
    const changesResults = Boolean(fallback) && resultsKey(url) !== renderedKey;
    let nextPanel = panelIn(url);
    let html;
    let results;
    try {
      if (changesResults) {
        const endpoint = new URL(root.dataset.resultsUrl, url);
        endpoint.search = url.search;
        pendingPreview = new AbortController();
        const response = await fetch(endpoint, {signal: pendingPreview.signal, credentials: 'same-origin'});
        if (!response.ok || response.redirected) throw new Error('Explore results unavailable');
        results = await response.json();
        if (results.reload) throw new Error('Explore needs a full page');
        nextPanel = results.panel_id;
        html = results.panel_html;
      } else if (nextPanel && nextPanel !== panelId) {
        const endpoint = new URL(previewURL || `parks/${nextPanel}/preview/`, url);
        endpoint.search = url.search;
        endpoint.searchParams.delete('panel');
        pendingPreview = new AbortController();
        const response = await fetch(endpoint, {signal: pendingPreview.signal, credentials: 'same-origin'});
        if (!response.ok || response.redirected) throw new Error('Park preview unavailable');
        html = await response.text();
      }
      if (current !== transition) return;
      // The results request already saved the state for Back to results on the server.
      if (!changesResults) await saveExplore(url, current);
      if (current !== transition) return;
      if (results) url = new URL(results.url, url);
      if (!fromHistory) history.pushState(null, '', url);
      if (results) applyResults(results, url, before);
      renderView(url);
      // A panel that stays open still gets its new contents, since its events follow the filters.
      if (nextPanel === panelId && !(results && nextPanel)) return;
      panelId = nextPanel;
      if (nextPanel) {
        preview.innerHTML = html;
        if (!dialog.open) {
          panelTrigger = trigger;
          dialog.showModal();
        }
      } else {
        if (dialog.open) dialog.close();
        if (panelTrigger?.isConnected) panelTrigger.focus({preventScroll: true});
        panelTrigger = undefined;
      }
    } catch (error) {
      if (current !== transition || error.name === 'AbortError') return;
      if (fromHistory) window.location.reload();
      else window.location.assign(url);
    }
  }

  // #123: put the server's new pieces in place, set the filter fields up again and refresh the pins.
  function applyResults(results, url, before) {
    const active = document.activeElement;
    const focusedId = active && filtersForm.contains?.(active) ? active.id : '';
    filtersForm.innerHTML = results.filters_html;
    window.LookaheadSelect?.enhanceAll(filtersForm);
    bindFilterFields();
    if (focusedId) filtersForm.querySelector(`#${focusedId}`)?.focus({preventScroll: true});
    if (counts) counts.innerHTML = results.counts_html;
    fallback.innerHTML = results.list_html;
    if (overlay) overlay.innerHTML = results.map_empty_html;
    renderedKey = resultsKey(url);
    root.dataset.focusPark = '';
    root.querySelectorAll('[data-explore-view]').forEach((link) => {
      const target = new URL(url);
      target.searchParams.set('view', link.dataset.exploreView);
      target.searchParams.delete('panel');
      link.href = target.href;
    });
    if (container && pinsKey(new URL(results.map_url, url)) !== pinsKey(new URL(container.dataset.mapUrl, url))) {
      container.dataset.mapUrl = results.map_url;
      if (mapStarted && !mapFailed) load(true);
    }
    // A new page or tab brings the top of the list back into view if it had scrolled away.
    const listMoved = ['page', 'tab'].some((name) => url.searchParams.get(name) !== before.searchParams.get(name));
    if (listMoved && fallback.getBoundingClientRect?.().top < 0) fallback.scrollIntoView({block: 'start'});
  }

  function closePreview() {
    return navigateExplore(parkURL(null));
  }
  closeButton.addEventListener('click', closePreview);
  dialog.addEventListener('cancel', (event) => { event.preventDefault(); closePreview(); });
  root.addEventListener('click', (event) => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a');
    if (link?.matches('[data-explore-view]')) {
      event.preventDefault();
      if (link.dataset.exploreView === root.dataset.view) return;
      return navigateExplore(new URL(link.href, window.location.href));
    }
    if (link?.matches('[data-park-preview-url], .park-panel-close-link')) {
      event.preventDefault();
      if (link.matches('.park-panel-close-link')) closePreview();
      else navigateExplore(new URL(link.href, window.location.href), {previewURL: link.dataset.parkPreviewUrl, trigger: link});
      return;
    }
    // #123: tabs, pages, pills, Clear filters, "See all" and every other link back to Explore update in place.
    if (link && fallback && !link.target) {
      const target = new URL(link.href, window.location.href);
      const explorePath = new URL(filtersForm.action || window.location.href, window.location.href).pathname;
      if (target.origin === new URL(window.location.href).origin && target.pathname === explorePath) {
        event.preventDefault();
        return navigateExplore(target);
      }
    }
  });
  window.addEventListener('popstate', () => navigateExplore(new URL(window.location.href), {fromHistory: true}));
  const shell = root.querySelector('[data-map-shell]');
  const container = root.querySelector('#explore-map');
  const fallback = root.querySelector('[data-list-fallback]');
  const counts = root.querySelector('[data-explore-counts]');
  const overlay = root.querySelector('[data-map-overlay]');
  const errorBox = root.querySelector('[data-map-error]');
  const status = root.querySelector('[data-map-status]');
  // #122: "Back to results" returns to the same map center and zoom. The position is saved per set of
  // results in sessionStorage and restored only when the page was reached through Back to results, which
  // leaves a short-lived flag (static/js/explore-return.js). Shared links and Show on map keep their own
  // view. Blocked or full storage is ignored, so the map still works without it.
  const POSITION_KEY = 'nycactive.exploreMap';
  const RETURN_KEY = 'nycactive.exploreReturn';
  const RETURN_WINDOW_MS = 60 * 1000;
  function store() {
    try { return window.sessionStorage || null; } catch (_) { return null; }
  }
  function mapPositionKey() {
    // The map feed's URL holds the server's canonical filters, so /events/?view=map and the
    // remembered results URL for the same search give the same key. View, tab, page and the open
    // panel don't change which parks the map shows.
    if (!container?.dataset.mapUrl) return '';
    const params = new URL(container.dataset.mapUrl, window.location.href).searchParams;
    ['view', 'tab', 'page', 'panel', 'park'].forEach((name) => params.delete(name));
    params.sort();
    return params.toString();
  }
  function takeSavedPosition() {
    try {
      const storage = store();
      if (!storage) return null;
      const returnedAt = Number(storage.getItem(RETURN_KEY));
      storage.removeItem(RETURN_KEY);
      if (!returnedAt || Date.now() - returnedAt > RETURN_WINDOW_MS) return null;
      const saved = JSON.parse(storage.getItem(POSITION_KEY) || 'null');
      const valid = saved && saved.results === mapPositionKey()
        && [saved.lat, saved.lng, saved.zoom].every(Number.isFinite);
      return valid ? saved : null;
    } catch (_) { return null; }
  }
  function savePosition() {
    try {
      const center = map.getCenter();
      store()?.setItem(POSITION_KEY, JSON.stringify({
        results: mapPositionKey(), lat: center.lat, lng: center.lng, zoom: map.getZoom(),
      }));
    } catch (_) { /* storage unavailable: nothing to restore later */ }
  }
  let savedPosition = takeSavedPosition();

  let map;
  let group;
  let mapFailed = false;
  let mapReady = false;
  let mapStarted = false;
  let fitPending;

  function updateMapVisibility() {
    if (!shell) return;
    const isMap = root.dataset.view === 'map';
    shell.hidden = !isMap || mapFailed;
    errorBox.hidden = !isMap || !mapFailed;
    fallback.hidden = isMap && mapReady && !mapFailed;
  }

  function renderView(url) {
    const view = url.searchParams.getAll('view').at(-1) === 'list' ? 'list' : 'map';
    const changed = view !== root.dataset.view;
    root.dataset.view = view;
    // Looked up each time, since results updates replace the filter fields and their links (#123).
    filtersForm.querySelector('input[name="view"]').value = view;
    root.querySelectorAll('[data-explore-view]').forEach((link) => {
      if (link.dataset.exploreView === view) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    });
    if (changed) {
      root.querySelectorAll('.explore-pill, .explore-clear, .discover-pages a, [data-keep-explore-view], [data-park-preview-url]')
        .forEach((link) => {
          const target = new URL(link.href, url);
          target.searchParams.set('view', view);
          link.href = target.href;
        });
    }
    updateMapVisibility();
    if (view === 'map' && shell && container) {
      if (!mapStarted) initMap();
      else if (changed && !mapFailed) {
        map.invalidateSize({pan: false});
        fitPending?.();
        fitPending = undefined;
      }
    }
  }

  function fail() {
    mapFailed = true;
    status.hidden = true;
    updateMapVisibility();
  }

  // Without Leaflet there is no map to retry in place, so Try again reloads the page.
  function failHard() { fail(); root.querySelector('[data-map-retry]').addEventListener('click', () => window.location.reload()); }
  function icon(count, cluster = false, liveCount = 0) {
    const span = document.createElement('span');
    span.className = (cluster ? 'explore-cluster' : 'explore-pin') +
      (count > 0 ? (liveCount > 0 ? ' is-active is-live' : ' is-active') : '');
    span.textContent = count > 0 ? String(count) : (cluster ? '○' : '');
    return L.divIcon({html: span, className: 'explore-marker', iconSize: cluster ? [46,46] : [30,30]});
  }
  function initMap() {
    mapStarted = true;
    if (!window.L || !L.markerClusterGroup) { failHard(); return; }
    try {
      map = L.map(container, {scrollWheelZoom: false}).setView([40.73, -73.94], 11);
      map.on('moveend', savePosition);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, referrerPolicy: 'strict-origin-when-cross-origin',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).on('tileerror', fail).addTo(map);
      group = L.markerClusterGroup({showCoverageOnHover: false, maxClusterRadius: 48,
        iconCreateFunction: (cluster) => {
          const markers = cluster.getAllChildMarkers();
          return icon(markers.reduce((sum, marker) => sum + marker.options.eventCount, 0), true,
            markers.reduce((sum, marker) => sum + marker.options.liveCount, 0));
        }
      }).addTo(map);
    } catch (_) { failHard(); return; }
    root.querySelector('[data-map-retry]').addEventListener('click', () => {
      mapFailed = false;
      updateMapVisibility();
      map.invalidateSize({pan: false});
      map.eachLayer(layer => { if (layer instanceof L.TileLayer) layer.redraw(); });
      load();
    });
    load();
  }
  let mapRequest;
  // keepView (#123): after new filters, keep the map where it is if any new pin is still in view.
  async function load(keepView = false) {
    mapRequest?.abort();
    mapRequest = new AbortController();
    status.hidden = false;
    status.textContent = 'Loading parks…';
    try {
      const response = await fetch(container.dataset.mapUrl, {signal: mapRequest.signal, credentials: 'same-origin'});
      if (!response.ok || response.redirected) throw new Error('Map unavailable');
      const data = await response.json();
      group.clearLayers();
      const markers = data.parks.map((park) => {
        const title = `${park.name} · ${park.event_count} matching events` +
          (park.live_count > 0 ? ` · ${park.live_count} live` : '');
        const marker = L.marker([park.lat,park.lng], {icon: icon(park.event_count, false, park.live_count || 0),
          eventCount: park.event_count, liveCount: park.live_count || 0,
          title, alt: title, keyboard: true});
        marker.on('add', () => marker.getElement()?.setAttribute('aria-label', title));
        const tooltip = document.createElement('span');
        tooltip.textContent = `${park.name} · ${park.borough}`;
        marker.bindTooltip(tooltip);
        marker.on('click', () => navigateExplore(parkURL(park.id), {
          previewURL: park.preview_url, trigger: marker.getElement(),
        }));
        return marker;
      });
      group.addLayers(markers);
      const selectedPark = data.parks.find((park) => String(park.id) === root.dataset.focusPark);
      const fit = () => {
        if (savedPosition) {
          map.setView([savedPosition.lat, savedPosition.lng], savedPosition.zoom);
          savedPosition = null;
        } else if (selectedPark) map.setView([selectedPark.lat, selectedPark.lng], 15);
        else if (markers.length) map.fitBounds(group.getBounds(), {padding:[36,36], maxZoom:15});
      };
      const stillInView = keepView && markers.some((marker) => map.getBounds().contains(marker.getLatLng()));
      if (stillInView) fitPending = undefined;
      else if (root.dataset.view === 'map' && !mapFailed) fit();
      else fitPending = fit;
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
      mapReady = true;
      updateMapVisibility();
    } catch (error) { if (error.name !== 'AbortError') fail(); }
  }
  renderView(new URL(window.location.href));
})();
