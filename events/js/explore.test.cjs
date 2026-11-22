/* Run the shipped browser controller against a tiny DOM/Leaflet adapter.
 * No npm packages or test-only hooks in the production script are needed. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/explore.js', 'utf8');

function element() {
  return {hidden: false, textContent: '', children: [], listeners: {},
    addEventListener(name, handler) { this.listeners[name] = handler; },
    append(child) { this.children.push(child); },
    replaceChildren() { this.children = []; }};
}

// #123: what /events/results/ returns for a URL: its pieces, labelled with the URL so tests can tell them apart.
function resultsFor(url) {
  const params = new URL(url).searchParams;
  const panel = params.get('panel') || '';
  params.delete('panel');
  const canonical = new URLSearchParams(params);
  if (panel) canonical.set('panel', panel);
  const pins = new URLSearchParams(params);
  for (const name of ['view', 'tab', 'page']) pins.delete(name);
  return {url: `/events/?${canonical}`, filters_html: `fields ${params}`, counts_html: `counts ${params}`,
    list_html: `list ${params}`, map_empty_html: '', map_url: `/events/api/map/?${pins}`,
    panel_id: panel, panel_html: panel ? `<section>Panel ${params}</section>` : ''};
}

// Stands in for the browser's FormData: the form's current fields, as name/value pairs.
class FormData {
  constructor(form) { this.entries = form.formEntries || []; }
  [Symbol.iterator]() { return this.entries[Symbol.iterator](); }
}

async function controller({data, reject = false, leaflet = true, selected = false, mapEmpty = false,
  noParkData = false, initialView = 'map', inView = false, resultsFail = false, storage} = {}) {
  const nodes = {};
  const state = {reject, clears: 0, fetches: 0, maps: 0, saves: [], previews: [], results: []};
  for (const key of ['.explore-filters', '[data-park-dialog]', '[data-close-preview]',
    '[data-park-preview-content]', '[name="csrfmiddlewaretoken"]', '[data-explore-counts]', '[data-map-overlay]',
    '[data-map-shell]', '#explore-map', '[data-list-fallback]', '[data-map-error]',
    '[data-map-status]', '[data-unmapped]', '[data-map-retry]']) nodes[key] = element();
  if (mapEmpty) nodes['[data-map-empty]'] = element();  // server-rendered US-2.5 empty state
  const checkbox = element();
  let submits = 0;
  const viewInput = {value: initialView};
  nodes['.explore-filters'].querySelector = key => key === 'input[name="view"]' ? viewInput : checkbox;
  // US-4.9 (#118): the Phase checkboxes, with the default Upcoming and Live checked.
  const phaseBoxes = ['upcoming', 'live', 'ended'].map(value => ({...element(), name: 'phase', value,
    checked: value !== 'ended'}));
  const autosubmitFields = [element(), element(), ...phaseBoxes];  // borough select, zip select (US-2.3)
  nodes['.explore-filters'].querySelectorAll = (key) => key === '[data-autosubmit]' ? autosubmitFields : [];
  nodes['.explore-filters'].requestSubmit = () => submits++;
  const dialog = nodes['[data-park-dialog]'];
  dialog.open = selected;
  dialog.dataset = {panelId: selected ? '7' : ''};
  dialog.close = () => { dialog.open = false; dialog.listeners.close?.(); };
  dialog.showModal = () => { dialog.open = true; };
  dialog.removeAttribute = () => { dialog.open = false; };
  nodes['#explore-map'].dataset = {mapUrl: '/events/api/map/?zip=11211&zip=11222'};
  if (noParkData) {
    // US-2.5 no_park_data: the template replaces the map shell, error box and list with one message.
    for (const key of ['[data-map-shell]', '#explore-map', '[data-list-fallback]', '[data-map-error]',
      '[data-map-status]', '[data-unmapped]', '[data-map-retry]', '[data-map-overlay]']) delete nodes[key];
  }
  // Like the real DOM, a missing element is null.
  const quickView = {href: '?view=map&zip=11211&zip=11222&page=2&panel=7', dataset: {parkPreviewUrl: '/events/parks/7/preview/'},
    matches: selector => selector.includes('[data-park-preview-url]')};
  const viewLinks = ['map', 'list'].map(view => ({
    href: `?view=${view}&zip=11211&zip=11222&page=2`, dataset: {exploreView: view},
    matches: selector => selector === '[data-explore-view]',
    setAttribute(name, value) {this[name] = value;}, removeAttribute(name) {delete this[name];},
  }));
  const root = {...element(), dataset: {view: initialView, focusPark: selected ? '7' : '', stateUrl: '/events/explore/state/',
    resultsUrl: '/events/results/'},
    querySelector: key => nodes[key] ?? null,
    querySelectorAll: key => key === '[data-explore-view]' ? viewLinks : [quickView]};
  const navigations = [];
  const markers = [];
  let clusterOptions;
  let fits = 0;
  let tileError;
  const map = {listeners: {}, center: {lat: 40.73, lng: -73.94}, zoom: 11,
    on(name, handler) {this.listeners[name] = handler; return this;},
    getCenter() {return this.center;}, getZoom() {return this.zoom;},
    setView(position, zoom) {state.mapView = {position, zoom}; return this;},
    fitBounds() {fits++;}, eachLayer() {}, invalidateSize(options) {state.invalidated = options;},
    getBounds: () => ({contains: () => inView})};
  const group = {addTo() {return this;}, clearLayers() {state.clears++;}, addLayers() {}, getBounds() {return {};}};
  const L = {
    map: () => { state.maps++; return map; },
    tileLayer: () => ({on(name, handler) {tileError = handler; return this;}, addTo() {return this;}}),
    markerClusterGroup(options) {clusterOptions = options; return group;},
    divIcon: options => options,
    marker(position, options) {
      const marker = {position, options, listeners: {}, getLatLng: () => position,
        on(name, handler) {this.listeners[name] = handler; return this;},
        bindTooltip() {}, getElement() {return {setAttribute() {}};}};
      markers.push(marker);
      return marker;
    }
  };
  const window = {...element(), L: leaflet ? L : undefined, location: {
    href: `http://localhost/events/?view=${initialView}&zip=11211&zip=11222&page=2` + (selected ? '&park=7&panel=1' : ''),
    assign(url) {navigations.push(new URL(url));}, reload() {navigations.push(new URL(this.href));}}};
  // #122: `storage` returns the sessionStorage object, or throws like a browser that blocks it.
  if (storage) Object.defineProperty(window, 'sessionStorage', {get: storage});
  const entries = [window.location.href];
  let index = 0;
  window.history = {
    replaceState(state, title, url) {entries[index] = window.location.href = String(url);},
    pushState(state, title, url) {entries.splice(++index, entries.length, String(url)); window.location.href = entries[index];},
  };
  function historyStep(delta) {
    index += delta;
    window.location.href = entries[index];
    return window.listeners.popstate();
  }
  vm.runInNewContext(source, {window, L, URL, URLSearchParams, FormData, AbortController,
    document: {querySelector: () => root, createElement: element, createTextNode: text => ({textContent: text})},
    fetch: async (url, options) => {
      if (options.method === 'POST') {
        state.saves.push(new URLSearchParams(options.body));
        return {ok: true};
      }
      if (String(url).includes('/events/results/')) {
        state.results.push(new URL(url));
        if (resultsFail) return {ok: false, redirected: false};
        return {ok: true, redirected: false, json: async () => resultsFor(url)};
      }
      if (String(url).includes('/preview/')) {
        state.previews.push(new URL(url));
        return {ok: true, text: async () => '<section>Park preview</section>'};
      }
      state.fetches++;
      if (state.reject) throw new Error('Offline');
      return {ok: true, redirected: false, json: async () => data || {parks: [], unmapped_event_count: 0}};
    }});
  await new Promise(resolve => setImmediate(resolve));
  const clickView = view => root.listeners.click({button: 0, target: {
    closest: () => viewLinks.find(link => link.dataset.exploreView === view),
  }, preventDefault() {}});
  // #123: click any link inside Explore, like a tab, page, pill or "See all"; returns whether it was intercepted.
  const clickLink = async (href) => {
    let prevented = false;
    await root.listeners.click({button: 0, preventDefault() { prevented = true; },
      target: {closest: () => ({href, target: '', matches: () => false})}});
    await new Promise(resolve => setImmediate(resolve));
    return prevented;
  };
  const submitFilters = async (entries) => {
    let prevented = false;
    nodes['.explore-filters'].formEntries = entries;
    await nodes['.explore-filters'].listeners.submit({preventDefault() { prevented = true; }});
    await new Promise(resolve => setImmediate(resolve));
    return prevented;
  };
  return {nodes, markers, clusterOptions, fits, navigations, checkbox, dialog, autosubmitFields, phaseBoxes,
    submits: () => submits, tileError, state, fitCount: () => fits, quickView, root, window, historyStep,
    viewInput, viewLinks, clickView, clickLink, submitFilters, map};
}

const parks = [
  {id: 7, name: 'McCarren', borough: 'Brooklyn', lat: 40.7, lng: -73.9, event_count: 3},
  {id: 8, name: 'Central', borough: 'Manhattan', lat: 40.8, lng: -73.9, event_count: 4},
  {id: 9, name: 'Empty park', borough: 'Queens', lat: 40.7, lng: -73.8, event_count: 0},
];

test('clusters sum events rather than parks; empty pins/clusters are muted', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  const sum = ui.clusterOptions.iconCreateFunction({getAllChildMarkers: () => ui.markers});
  assert.equal(sum.html.textContent, '7');
  assert.match(sum.html.className, /is-active/);
  const empty = ui.clusterOptions.iconCreateFunction({getAllChildMarkers: () => [ui.markers[2]]});
  assert.doesNotMatch(empty.html.className, /is-active/);
  assert.equal(ui.markers[0].options.icon.html.textContent, '3');
  assert.equal(ui.markers[2].options.icon.html.textContent, '');
  assert.equal(ui.fits, 1);
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, true);
});

test('selected live games color pins and clusters without changing their event totals', async () => {
  const data = {parks: parks.map((park, index) => ({...park, live_count: index === 0 ? 2 : 0}))};
  const ui = await controller({data});
  assert.match(ui.markers[0].options.icon.html.className, /is-live/);
  assert.match(ui.markers[0].options.alt, /2 live/);
  assert.equal(ui.markers[0].options.icon.html.textContent, '3');
  assert.match(ui.markers[1].options.icon.html.className, /is-active/);
  assert.doesNotMatch(ui.markers[1].options.icon.html.className, /is-live/);
  assert.doesNotMatch(ui.markers[2].options.icon.html.className, /is-active|is-live/);
  const cluster = markers => ui.clusterOptions.iconCreateFunction({getAllChildMarkers: () => markers});
  assert.equal(cluster(ui.markers).html.textContent, '7');
  assert.match(cluster(ui.markers).html.className, /is-live/);
  assert.doesNotMatch(cluster(ui.markers.slice(1)).html.className, /is-live/);
  const upcomingOnly = await controller({data: {parks: [{...parks[0], event_count: 1, live_count: 0}]}});
  assert.equal(upcomingOnly.markers[0].options.icon.html.textContent, '1');
  assert.doesNotMatch(upcomingOnly.markers[0].options.icon.html.className, /is-live/);
});

test('pin selection, Back, Forward and close preserve filters, page and the map', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  await ui.markers[0].listeners.click();
  assert.equal(ui.dialog.open, true);
  const selected = new URL(ui.window.location.href).searchParams;
  assert.equal(selected.has('park'), false);
  assert.equal(selected.get('panel'), '7');
  assert.deepEqual(selected.getAll('zip'), ['11211', '11222']);
  assert.equal(selected.get('page'), '2');
  assert.equal(ui.state.previews[0].searchParams.has('panel'), false);
  assert.equal(ui.state.saves[0].get('panel'), '7');
  await ui.historyStep(-1);
  assert.equal(ui.dialog.open, false);
  await ui.historyStep(1);
  assert.equal(ui.dialog.open, true);
  await ui.nodes['[data-close-preview]'].listeners.click();
  assert.equal(ui.dialog.open, false);
  assert.equal(new URL(ui.window.location.href).searchParams.has('panel'), false);
  assert.equal(ui.state.saves.at(-1).has('panel'), false);
  assert.equal(ui.fitCount(), 1);
  assert.equal(ui.state.fetches, 1);
  assert.deepEqual(ui.navigations, []);
});

test('an open park panel centers the map on that park at street-level zoom', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}, selected: true});
  assert.deepEqual(Array.from(ui.state.mapView.position), [40.7, -73.9]);
  assert.equal(ui.state.mapView.zoom, 15);
  assert.equal(ui.fits, 0, 'the selected park is not zoomed out to the full result set');
});

test('Quick view intercepts its real link and fetches the preview', async () => {
  const ui = await controller();
  let prevented = false;
  ui.root.listeners.click({button: 0, target: {closest: () => ui.quickView}, preventDefault() { prevented = true; }});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, true);
  assert.equal(ui.dialog.open, true);
  assert.equal(ui.state.previews[0].pathname, '/events/parks/7/preview/');
  assert.deepEqual(ui.navigations, []);
});

test('Map/List and Back/Forward reuse the map and preserve the filter and page', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  await ui.clickView('list');
  assert.equal(ui.nodes['[data-map-shell]'].hidden, true);
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, false);
  assert.equal(ui.viewInput.value, 'list');
  assert.equal(ui.viewLinks[1]['aria-current'], 'page');
  assert.equal(ui.state.saves.at(-1).get('view'), 'list');
  assert.equal(ui.state.saves.at(-1).get('page'), '2');
  assert.deepEqual(ui.state.saves.at(-1).getAll('zip'), ['11211', '11222']);
  await ui.historyStep(-1);
  assert.equal(ui.nodes['[data-map-shell]'].hidden, false);
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, true);
  assert.equal(ui.state.invalidated.pan, false);
  await ui.historyStep(1);
  assert.equal(ui.viewInput.value, 'list');
  await ui.clickView('map');
  assert.equal(ui.viewInput.value, 'map');
  assert.equal(ui.state.fetches, 1);
  assert.equal(ui.state.maps, 1);
  assert.equal(ui.fitCount(), 1);
  assert.deepEqual(ui.navigations, []);
});

test('a direct List link starts the map only when Map is first shown', async () => {
  const ui = await controller({initialView: 'list', data: {parks, unmapped_event_count: 0}});
  assert.equal(ui.state.maps, 0);
  assert.equal(ui.nodes['[data-map-shell]'].hidden, true);
  await ui.clickView('map');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.state.maps, 1);
  assert.equal(ui.nodes['[data-map-shell]'].hidden, false);
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, true);
});

test('unmapped notice links to Events list without losing filters', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 2}});
  const notice = ui.nodes['[data-unmapped]'];
  assert.equal(notice.hidden, false);
  assert.match(notice.children[0].textContent, /2 matching events/);
  const url = notice.children[1].href;
  assert.equal(url.searchParams.get('view'), 'list');
  assert.equal(url.searchParams.get('tab'), 'events');
  assert.deepEqual(url.searchParams.getAll('zip'), ['11211', '11222']);
});

test('missing Leaflet, failed data and failed tiles keep the server list available', async () => {
  for (const options of [{leaflet: false}, {reject: true}]) {
    const ui = await controller(options);
    assert.equal(ui.nodes['[data-map-error]'].hidden, false);
    assert.equal(ui.nodes['[data-list-fallback]'].hidden, false);
  }
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  ui.tileError();
  assert.equal(ui.nodes['[data-map-error]'].hidden, false);
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, false);
});

test('events-only checkbox submits the existing shared filter form', async () => {
  const ui = await controller();
  ui.checkbox.listeners.change();
  assert.equal(ui.submits(), 1);
});

test('borough and zip-code changes submit the shared filter form (US-2.3)', async () => {
  const ui = await controller();
  const [borough, zip] = ui.autosubmitFields;
  borough.listeners.change();
  zip.listeners.change();
  assert.equal(ui.submits(), 2);
});

test('phase checkboxes submit, but the last checked phase stays checked (US-4.9)', async () => {
  const ui = await controller();
  const [upcoming, live, ended] = ui.phaseBoxes;
  ended.checked = true;
  ended.listeners.change();
  upcoming.checked = false;
  upcoming.listeners.change();
  live.checked = false;
  live.listeners.change();
  assert.equal(ui.submits(), 3);

  ended.checked = false;
  ended.listeners.change();
  assert.equal(ui.submits(), 3, 'unchecking the last phase does not submit');
  assert.equal(ended.checked, true, 'the last phase is checked again');
});

// US-2.5 (#20): empty and error states around the map.
test('no matching parks clears pins and leaves the server-rendered empty state in charge', async () => {
  const ui = await controller({data: {parks: [], unmapped_event_count: 0}, mapEmpty: true});
  assert.ok(ui.state.clears >= 1, 'previous pins are cleared');
  assert.equal(ui.fitCount(), 0);
  assert.equal(ui.nodes['[data-map-status]'].hidden, true, 'no generic status on top of the empty state');
  assert.equal(ui.nodes['[data-map-empty]'].hidden, false);
  assert.equal(ui.nodes['[data-map-error]'].hidden, true);
});

test('parks that match but have no coordinates get a location notice instead of an empty state', async () => {
  const ui = await controller({data: {parks: [], unmapped_event_count: 2}});
  assert.equal(ui.nodes['[data-map-status]'].hidden, false);
  assert.equal(ui.nodes['[data-map-status]'].textContent, 'None of the matching parks have a map location yet.');
  assert.equal(ui.nodes['[data-unmapped]'].hidden, false);
});

test('a failed map request shows the error with a working Try again', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}, reject: true});
  assert.equal(ui.nodes['[data-map-error]'].hidden, false);
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, false, 'List view still works');
  assert.equal(ui.nodes['[data-map-shell]'].hidden, true, 'the error replaces the blank map');
  ui.state.reject = false;
  ui.nodes['[data-map-retry]'].listeners.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.nodes['[data-map-error]'].hidden, true);
  assert.equal(ui.nodes['[data-map-shell]'].hidden, false);
  assert.equal(ui.markers.length, 3);
  assert.equal(ui.fitCount(), 1);
});

test('without Leaflet, Try again reloads the page', async () => {
  const ui = await controller({leaflet: false});
  assert.equal(ui.nodes['[data-map-error]'].hidden, false);
  assert.equal(typeof ui.nodes['[data-map-retry]'].listeners.click, 'function');
});

test('clearing the keyword search box resets results; typing alone does not submit (US-2.7)', async () => {
  const ui = await controller();
  const search = ui.checkbox;  // the fake form returns one node for every selector
  search.value = 'astoria';
  search.listeners.search();
  assert.equal(ui.submits(), 0, 'a non-empty search waits for Enter or the Search button');
  search.value = '  ';
  search.listeners.search();
  assert.equal(ui.submits(), 1, 'the × cleared it');
  search.listeners.keydown({key: 'Enter'});
  search.listeners.search();
  assert.equal(ui.submits(), 1, 'Enter submits natively; no second submit');
});

test('no park data: map markup is absent, so initialization exits without throwing or loading the map', async () => {
  for (const leaflet of [true, false]) {
    const ui = await controller({noParkData: true, leaflet});  // a TypeError here fails the test
    assert.equal(ui.state.fetches, 0, 'no map data request');
    assert.equal(ui.state.maps, 0, 'Leaflet is never initialized');
    assert.equal(ui.markers.length, 0);
    // The filter form still works on that page.
    ui.checkbox.listeners.change();
    assert.equal(ui.submits(), 1);
  }
});

// #123: tabs, pages, filters and "See all" update the results without a page reload.
test('a new page or tab swaps the list in place and leaves the map alone', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  assert.equal(await ui.clickLink('?view=map&zip=11211&zip=11222&page=3'), true);
  assert.equal(ui.state.results.length, 1);
  assert.equal(ui.state.results[0].pathname, '/events/results/');
  assert.equal(ui.nodes['[data-list-fallback]'].innerHTML, 'list view=map&zip=11211&zip=11222&page=3');
  assert.equal(ui.nodes['[data-explore-counts]'].innerHTML, 'counts view=map&zip=11211&zip=11222&page=3');
  assert.equal(new URL(ui.window.location.href).searchParams.get('page'), '3');
  assert.equal(ui.state.saves.length, 0, 'the results request saves the state for Back to results');

  assert.equal(await ui.clickLink('?view=list&zip=11211&zip=11222&tab=parks'), true);
  assert.equal(ui.root.dataset.view, 'list');
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, false);
  assert.equal(ui.viewLinks[0].href, 'http://localhost/events/?view=map&zip=11211&zip=11222&tab=parks');
  assert.equal(ui.state.fetches, 1, 'pins only depend on the filters, so they are not reloaded');
  assert.equal(ui.fitCount(), 1);
  assert.deepEqual(ui.navigations, []);
});

test('Map/List and panels never refetch the results', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  await ui.clickView('list');
  await ui.markers[0].listeners.click();
  await ui.nodes['[data-close-preview]'].listeners.click();
  assert.equal(ui.state.results.length, 0);
});

test('filter changes update the fields, results and pins, and keep the map if pins are still in view', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}, inView: true});
  const prevented = await ui.submitFilters([['view', 'map'], ['tab', 'events'], ['q', ''], ['borough', 'B']]);
  assert.equal(prevented, true);
  assert.equal(ui.state.results[0].searchParams.get('borough'), 'B');
  assert.equal(ui.nodes['.explore-filters'].innerHTML, 'fields view=map&tab=events&q=&borough=B');
  assert.equal(new URL(ui.window.location.href).searchParams.get('borough'), 'B');
  assert.equal(ui.nodes['#explore-map'].dataset.mapUrl, '/events/api/map/?q=&borough=B');
  assert.equal(ui.state.fetches, 2, 'the pins reload for the new filters');
  assert.equal(ui.fitCount(), 1, 'the map stays where it is while new pins are in view');
  ui.autosubmitFields[0].listeners.change();
  assert.equal(ui.submits(), 1, 'the new filter fields submit as they change');
});

test('filter changes re-fit the map when none of the new pins are in view', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}, inView: false});
  await ui.submitFilters([['view', 'map'], ['borough', 'Q']]);
  assert.equal(ui.state.fetches, 2);
  assert.equal(ui.fitCount(), 2);
});

test('Back and Forward bring back the earlier results', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}});
  await ui.clickLink('?view=map&zip=11211&zip=11222&page=3');
  await ui.historyStep(-1);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.nodes['[data-list-fallback]'].innerHTML, 'list view=map&zip=11211&zip=11222&page=2');
  await ui.historyStep(1);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.nodes['[data-list-fallback]'].innerHTML, 'list view=map&zip=11211&zip=11222&page=3');
  assert.equal(ui.state.results.length, 3);
  assert.deepEqual(ui.navigations, []);
});

test('an open panel follows new filters, and "See all" closes it', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}, selected: true});
  await ui.clickLink('?view=map&zip=11211&zip=11222&page=2&panel=7&phase=upcoming&phase=live&phase=ended');
  assert.equal(ui.dialog.open, true);
  assert.match(ui.nodes['[data-park-preview-content]'].innerHTML, /^<section>Panel .*phase=ended/);
  assert.equal(new URL(ui.window.location.href).searchParams.get('panel'), '7');
  assert.equal(ui.root.dataset.focusPark, '', 'only the first load centers on a legacy link');

  await ui.clickLink('/events/?view=list&tab=events&park=7&zip=11211&zip=11222');
  assert.equal(ui.dialog.open, false);
  assert.equal(ui.root.dataset.view, 'list');
  assert.equal(new URL(ui.window.location.href).searchParams.has('panel'), false);
});

test('links to other pages, and failed results requests, use normal navigation', async () => {
  const ui = await controller({resultsFail: true});
  assert.equal(await ui.clickLink('/events/5/'), false);
  assert.equal(await ui.clickLink('/parks/7/'), false);
  assert.equal(ui.state.results.length, 0);
  await ui.clickLink('?view=map&zip=11211&zip=11222&page=3');
  assert.equal(ui.navigations.length, 1);
  assert.equal(ui.navigations[0].searchParams.get('page'), '3');
});

test('without park data the filter form submits normally', async () => {
  const ui = await controller({noParkData: true});
  assert.equal(await ui.submitFilters([['borough', 'B']]), false);
  assert.equal(await ui.clickLink('?view=list&tab=parks'), false);
  assert.equal(ui.state.results.length, 0);
});

// #122: Back to results returns to the same map center and zoom.
function memoryStorage(items = {}) {
  const data = {...items};
  return {data, getItem: key => (key in data ? data[key] : null), setItem(key, value) {data[key] = String(value);},
    removeItem(key) {delete data[key];}};
}
const RESULTS = 'zip=11211&zip=11222';  // the map feed's filters, without view/tab/page/panel
const saved = (results = RESULTS) => JSON.stringify({results, lat: 40.75, lng: -73.99, zoom: 14});

test('Back to results restores the saved center and zoom instead of re-fitting', async () => {
  const storage = memoryStorage({'nycactive.exploreMap': saved(), 'nycactive.exploreReturn': String(Date.now())});
  const ui = await controller({data: {parks, unmapped_event_count: 0}, storage: () => storage});
  assert.deepEqual(Array.from(ui.state.mapView.position), [40.75, -73.99]);
  assert.equal(ui.state.mapView.zoom, 14);
  assert.equal(ui.fits, 0);
  assert.equal(storage.data['nycactive.exploreReturn'], undefined, 'the return flag is used once');
});

test('moving the map saves its position for these results, ignoring view, tab, page and panel', async () => {
  const storage = memoryStorage();
  const ui = await controller({data: {parks, unmapped_event_count: 0}, storage: () => storage});
  ui.map.center = {lat: 40.68, lng: -73.97};
  ui.map.zoom = 13;
  ui.map.listeners.moveend();
  assert.deepEqual(JSON.parse(storage.data['nycactive.exploreMap']), {results: RESULTS, lat: 40.68, lng: -73.97, zoom: 13});
});

test('shared links and Show on map ignore a saved position', async () => {
  // No return flag: someone opened a link, so the map fits the results as usual.
  const shared = memoryStorage({'nycactive.exploreMap': saved()});
  let ui = await controller({data: {parks, unmapped_event_count: 0}, storage: () => shared});
  assert.equal(ui.fits, 1);
  // Show on map (park=7&panel=1) still focuses on the chosen park.
  const showOnMap = memoryStorage({'nycactive.exploreMap': saved()});
  ui = await controller({data: {parks, unmapped_event_count: 0}, selected: true, storage: () => showOnMap});
  assert.deepEqual(Array.from(ui.state.mapView.position), [40.7, -73.9]);
  assert.equal(ui.state.mapView.zoom, 15);
});

test('a stale return flag or a position saved for other results is not restored', async () => {
  for (const items of [
    {'nycactive.exploreMap': saved(), 'nycactive.exploreReturn': String(Date.now() - 5 * 60 * 1000)},
    {'nycactive.exploreMap': saved('zip=10001'), 'nycactive.exploreReturn': String(Date.now())},
    {'nycactive.exploreMap': '{not json', 'nycactive.exploreReturn': String(Date.now())},
  ]) {
    const storage = memoryStorage(items);
    const ui = await controller({data: {parks, unmapped_event_count: 0}, storage: () => storage});
    assert.equal(ui.fits, 1);
    assert.equal(storage.data['nycactive.exploreReturn'], undefined);
  }
});

test('blocked sessionStorage does not break the map', async () => {
  const blocked = () => { throw new Error('SecurityError'); };
  const ui = await controller({data: {parks, unmapped_event_count: 0}, storage: blocked});
  assert.equal(ui.fits, 1);
  assert.doesNotThrow(() => ui.map.listeners.moveend());
  assert.equal(ui.nodes['[data-list-fallback]'].hidden, true);
});

test('Back to results leaves a return flag, and a blocked sessionStorage is ignored', () => {
  const returnSource = fs.readFileSync('static/js/explore-return.js', 'utf8');
  function run(sessionStorage) {
    let onClick;
    const window = {};
    Object.defineProperty(window, 'sessionStorage', {get: sessionStorage});
    vm.runInNewContext(returnSource, {window, String, Date,
      document: {addEventListener: (name, handler) => { onClick = handler; }}});
    return target => onClick({target: {closest: selector => (target && selector === '[data-explore-return]' ? {} : null)}});
  }
  const storage = memoryStorage();
  const click = run(() => storage);
  click(false);
  assert.equal(storage.data['nycactive.exploreReturn'], undefined, 'other links leave no flag');
  click(true);
  assert.ok(Date.now() - Number(storage.data['nycactive.exploreReturn']) < 1000);
  assert.doesNotThrow(() => run(() => { throw new Error('SecurityError'); })(true));
});
