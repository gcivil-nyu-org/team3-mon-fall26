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
    const nextPanel = panelIn(url);
    let html;
    try {
      if (nextPanel && nextPanel !== panelId) {
        const endpoint = new URL(previewURL || `parks/${nextPanel}/preview/`, url);
        endpoint.search = url.search;
        endpoint.searchParams.delete('panel');
        pendingPreview = new AbortController();
        const response = await fetch(endpoint, {signal: pendingPreview.signal, credentials: 'same-origin'});
        if (!response.ok || response.redirected) throw new Error('Park preview unavailable');
        html = await response.text();
      }
      if (current !== transition) return;
      await saveExplore(url, current);
      if (current !== transition) return;
      if (!fromHistory) history.pushState(null, '', url);
      renderView(url);
      if (nextPanel === panelId) return;
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
    }
  });
  window.addEventListener('popstate', () => navigateExplore(new URL(window.location.href), {fromHistory: true}));
  const shell = root.querySelector('[data-map-shell]');
  const container = root.querySelector('#explore-map');
  const fallback = root.querySelector('[data-list-fallback]');
  const errorBox = root.querySelector('[data-map-error]');
  const status = root.querySelector('[data-map-status]');
  const viewLinks = root.querySelectorAll('[data-explore-view]');
  const viewInput = filtersForm.querySelector('input[name="view"]');
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
    viewInput.value = view;
    viewLinks.forEach((link) => {
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
  function icon(count, cluster = false) {
    const span = document.createElement('span');
    span.className = (cluster ? 'explore-cluster' : 'explore-pin') + (count > 0 ? ' is-active' : '');
    span.textContent = count > 0 ? String(count) : (cluster ? '○' : '');
    return L.divIcon({html: span, className: 'explore-marker', iconSize: cluster ? [46,46] : [30,30]});
  }
  function initMap() {
    mapStarted = true;
    if (!window.L || !L.markerClusterGroup) { failHard(); return; }
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
  async function load() {
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
        const title = `${park.name} · ${park.event_count} matching events`;
        const marker = L.marker([park.lat,park.lng], {icon: icon(park.event_count), eventCount: park.event_count,
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
        if (selectedPark) map.setView([selectedPark.lat, selectedPark.lng], 15);
        else if (markers.length) map.fitBounds(group.getBounds(), {padding:[36,36], maxZoom:15});
      };
      if (root.dataset.view === 'map' && !mapFailed) fit();
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
