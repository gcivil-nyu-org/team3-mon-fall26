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

async function controller({data, reject = false, leaflet = true, selected = false, mapEmpty = false,
  noParkData = false} = {}) {
  const nodes = {};
  const state = {reject, clears: 0, fetches: 0, maps: 0};
  for (const key of ['.explore-filters', '[data-park-dialog]', '[data-close-preview]',
    '[data-map-shell]', '#explore-map', '[data-list-fallback]', '[data-map-error]',
    '[data-map-status]', '[data-unmapped]', '[data-map-retry]']) nodes[key] = element();
  if (mapEmpty) nodes['[data-map-empty]'] = element();  // server-rendered US-2.5 empty state
  const checkbox = element();
  let submits = 0;
  nodes['.explore-filters'].querySelector = () => checkbox;
  const autosubmitFields = [element(), element()];  // borough select, zip select (US-2.3)
  nodes['.explore-filters'].querySelectorAll = (key) => key === '[data-autosubmit]' ? autosubmitFields : [];
  nodes['.explore-filters'].requestSubmit = () => submits++;
  const dialog = nodes['[data-park-dialog]'];
  dialog.open = selected;
  dialog.close = () => { dialog.open = false; dialog.listeners.close?.(); };
  dialog.showModal = () => { dialog.open = true; };
  dialog.removeAttribute = () => { dialog.open = false; };
  nodes['#explore-map'].dataset = {mapUrl: '/events/api/map/?zip=11211&zip=11222'};
  if (noParkData) {
    // US-2.5 no_park_data: the template replaces the map shell, error box and list with one message.
    for (const key of ['[data-map-shell]', '#explore-map', '[data-list-fallback]', '[data-map-error]',
      '[data-map-status]', '[data-unmapped]', '[data-map-retry]']) delete nodes[key];
  }
  // Like the real DOM, a missing element is null.
  const root = {dataset: {view: 'map'}, querySelector: key => nodes[key] ?? null};
  const navigations = [];
  const markers = [];
  let clusterOptions;
  let fits = 0;
  let tileError;
  const map = {setView() {return this;}, fitBounds() {fits++;}, eachLayer() {}};
  const group = {addTo() {return this;}, clearLayers() {state.clears++;}, addLayers() {}, getBounds() {return {};}};
  const L = {
    map: () => { state.maps++; return map; },
    tileLayer: () => ({on(name, handler) {tileError = handler; return this;}, addTo() {return this;}}),
    markerClusterGroup(options) {clusterOptions = options; return group;},
    divIcon: options => options,
    marker(position, options) {
      const marker = {position, options, listeners: {},
        on(name, handler) {this.listeners[name] = handler; return this;},
        bindTooltip() {}, getElement() {return {setAttribute() {}};}};
      markers.push(marker);
      return marker;
    }
  };
  const window = {L: leaflet ? L : undefined, location: {
    href: 'http://localhost/events/?view=map&zip=11211&zip=11222&page=2' + (selected ? '&park=7&panel=1' : ''),
    assign(url) {navigations.push(new URL(url));}}};
  vm.runInNewContext(source, {window, L, URL, AbortController,
    document: {querySelector: () => root, createElement: element, createTextNode: text => ({textContent: text})},
    fetch: async () => {
      state.fetches++;
      if (state.reject) throw new Error('Offline');
      return {ok: true, redirected: false, json: async () => data || {parks: [], unmapped_event_count: 0}};
    }});
  await new Promise(resolve => setImmediate(resolve));
  return {nodes, markers, clusterOptions, fits, navigations, checkbox, dialog, autosubmitFields,
    submits: () => submits, tileError, state, fitCount: () => fits};
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

test('pin selection and closing preserve repeated filters in shareable URL state', async () => {
  const ui = await controller({data: {parks, unmapped_event_count: 0}, selected: true});
  assert.equal(ui.dialog.open, true);
  ui.markers[0].listeners.click();
  const selected = ui.navigations[0].searchParams;
  assert.equal(selected.get('park'), '7');
  assert.equal(selected.get('panel'), '1');
  assert.deepEqual(selected.getAll('zip'), ['11211', '11222']);
  assert.equal(selected.has('page'), false);
  ui.nodes['[data-close-preview]'].listeners.click();
  assert.equal(ui.navigations[1].searchParams.has('park'), false);
  assert.equal(ui.navigations[1].searchParams.has('panel'), false);
  assert.deepEqual(ui.navigations[1].searchParams.getAll('zip'), ['11211', '11222']);
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
