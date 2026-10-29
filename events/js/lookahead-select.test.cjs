/* US-2.3: lookahead multi-select (static/js/lookahead-select.js), run on Node's built-in test runner
 * against a tiny fake DOM, so CI needs no npm packages. */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/js/lookahead-select.js', 'utf8');

class Node {
  constructor(tag, doc) {
    this.tagName = tag.toUpperCase();
    this.ownerDocument = doc;
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.listeners = {};
    this.hidden = false;
    this.textContent = '';
    this.parent = null;
  }
  append(...nodes) { nodes.forEach((n) => { n.parent = this; this.children.push(n); }); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  after(node) { const kids = this.parent.children; node.parent = this.parent; kids.splice(kids.indexOf(this) + 1, 0, node); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatchEvent(event) { (this.listeners[event.type] || []).forEach((fn) => fn(event)); return true; }
  fire(type, extra = {}) {
    let prevented = false;
    this.dispatchEvent({type, preventDefault() { prevented = true; }, ...extra});
    return prevented;
  }
  focus() { this.fire('focus'); }
  descendants() { return this.children.flatMap((c) => [c, ...c.descendants()]); }
  querySelectorAll(selector) {
    if (selector === '[role="option"]') return this.descendants().filter((n) => n.attributes.role === 'option');
    return [];
  }
}

function setup(values, selected = []) {
  const doc = {createElement: (tag) => new Node(tag, doc), readyState: 'complete'};
  const form = new Node('form', doc);
  const label = new Node('label', doc);
  label.htmlFor = 'id_zip';
  const select = new Node('select', doc);
  select.id = 'id_zip';
  select.multiple = true;
  select.dataset = {lookahead: '', placeholder: 'Type a zip code', empty: 'No matching NYC zip codes'};
  // Values are plain strings (zip codes) or [value, label] pairs (sports, facilities).
  select.options = values.map((v) => {
    const [value, label] = Array.isArray(v) ? v : [v, v];
    return {value, label, selected: selected.includes(value)};
  });
  form.append(label, select);
  const changes = [];
  select.addEventListener('change', () => changes.push(select.options.filter((o) => o.selected).map((o) => o.value)));
  doc.querySelector = (q) => (q === 'label[for="id_zip"]' ? label : null);
  doc.querySelectorAll = (q) => (q === 'select[multiple][data-lookahead]' ? [select] : []);
  const context = {
    document: doc, module: {exports: {}},  // no window: the test calls enhance() itself
    Event: class { constructor(type, init) { this.type = type; this.bubbles = init?.bubbles; } },
  };
  vm.runInNewContext(source, context);
  const ui = context.module.exports.enhance(select) || null;
  return {api: context.module.exports, select, label, changes, ui, doc};
}

const ZIPS = ['10029', '10128', '11201', '11211', '11215', '11222', '10112'];
const optionTexts = (list) => list.querySelectorAll('[role="option"]').map((li) => li.textContent);
const pillValues = (pills) => pills.children.map((p) => p.dataset.value);

test('matching is prefix-of-label or prefix-of-word, never a mid-word substring', () => {
  const {api} = setup([]);
  assert.ok(api.matches('11211', '112'));
  assert.ok(!api.matches('10112', '112'), 'zip 10112 must not match "112"');
  assert.ok(api.matches('Ultimate frisbee', 'fris'));
  assert.ok(api.matches('Ultimate frisbee', 'ULT'));
  assert.ok(api.matches('anything', '  '));
});

test('typing "112" narrows the dropdown to matching zip codes', () => {
  const {ui} = setup(ZIPS);
  ui.input.value = '112';
  ui.input.fire('input');
  assert.equal(ui.list.hidden, false);
  assert.equal(ui.input.getAttribute('aria-expanded'), 'true');
  assert.deepEqual(optionTexts(ui.list), ['11201', '11211', '11215', '11222']);
});

test('clicking a zip selects it, adds a removable pill and fires change for the form', () => {
  const {ui, select, changes} = setup(ZIPS);
  ui.input.value = '112';
  ui.input.fire('input');
  const option = ui.list.querySelectorAll('[role="option"]').find((li) => li.textContent === '11211');
  assert.equal(option.fire('mousedown'), true, 'mousedown keeps focus in the input');
  option.fire('click');
  assert.ok(select.options.find((o) => o.value === '11211').selected);
  assert.deepEqual(pillValues(ui.pills), ['11211']);
  assert.equal(ui.pills.children[0].getAttribute('aria-label'), 'Remove 11211');
  assert.deepEqual(changes, [['11211']]);
  assert.equal(ui.list.hidden, true);
  assert.equal(ui.input.value, '');
});

test('selected values leave the suggestions and multiple picks accumulate (OR)', () => {
  const {ui, changes} = setup(ZIPS, ['11201']);
  assert.deepEqual(pillValues(ui.pills), ['11201'], 'server-selected values render as pills');
  ui.input.value = '112';
  ui.input.fire('input');
  assert.ok(!optionTexts(ui.list).includes('11201'));
  ui.list.querySelectorAll('[role="option"]').find((li) => li.textContent === '11211').fire('click');
  assert.deepEqual(changes, [['11201', '11211']]);
});

test('a pill × removes just that value and fires change', () => {
  const {ui, select, changes} = setup(ZIPS, ['11201', '11211']);
  ui.pills.children.find((p) => p.dataset.value === '11201').fire('click');
  assert.deepEqual(changes, [['11211']]);
  assert.equal(select.options.find((o) => o.value === '11201').selected, false);
  assert.deepEqual(pillValues(ui.pills), ['11211']);
});

test('keyboard: arrows move, Enter picks without submitting, Escape closes, Backspace removes last pill', () => {
  const {ui, changes} = setup(ZIPS, ['10029']);
  ui.input.value = '112';
  ui.input.fire('input');
  assert.equal(ui.input.getAttribute('aria-activedescendant'), 'id_zip-listbox-0');
  ui.input.fire('keydown', {key: 'ArrowDown'});
  assert.equal(ui.input.getAttribute('aria-activedescendant'), 'id_zip-listbox-1');
  assert.equal(ui.input.fire('keydown', {key: 'Enter'}), true, 'Enter must not submit the filter form');
  assert.deepEqual(changes, [['10029', '11211']]);

  ui.input.fire('focus');
  assert.equal(ui.list.hidden, false);
  ui.input.fire('keydown', {key: 'Escape'});
  assert.equal(ui.list.hidden, true);

  ui.input.value = '';
  ui.input.fire('keydown', {key: 'Backspace'});
  assert.deepEqual(changes.at(-1), ['10029']);
});

test('no matches shows the empty message; the select is hidden and the label points at the search box', () => {
  const {ui, select, label} = setup(ZIPS);
  ui.input.value = '999';
  ui.input.fire('input');
  assert.deepEqual(optionTexts(ui.list), []);
  assert.equal(ui.list.children[0].textContent, 'No matching NYC zip codes');
  assert.equal(select.hidden, true);
  assert.equal(label.htmlFor, 'id_zip-search');
  assert.equal(ui.input.placeholder, 'Type a zip code');
  assert.equal(ui.input.getAttribute('role'), 'combobox');
});

test('enhancing twice is a no-op', () => {
  const {api, select} = setup(ZIPS);
  assert.equal(api.enhance(select), undefined);
});

// US-2.4 (#19): the same widget drives the Sport and Facilities dropdowns.
const SPORTS = [['basketball', 'Basketball'], ['soccer', 'Soccer'], ['running', 'Running'],
  ['pickleball', 'Pickleball'], ['ultimate_frisbee', 'Ultimate frisbee']];
const FACILITIES = [['tennis', 'Tennis'], ['regulation_soccer', 'Regulation Soccer'],
  ['nonregulation_soccer', 'Non-Regulation Soccer'], ['track_and_field', 'Track & Field']];

test('typing "bask" and selecting Basketball adds a Basketball pill and submits the sport value', () => {
  const {ui, changes} = setup(SPORTS);
  ui.input.value = 'bask';
  ui.input.fire('input');
  assert.deepEqual(optionTexts(ui.list), ['Basketball'], '"bask" must not match Pickleball');
  ui.input.fire('keydown', {key: 'Enter'});
  assert.deepEqual(changes, [['basketball']]);
  assert.equal(ui.pills.children[0].textContent, 'Basketball');
  assert.equal(ui.pills.children[0].dataset.value, 'basketball');
});

test('sports accumulate for OR and match on any word of the label', () => {
  const {ui, changes} = setup(SPORTS, ['basketball']);
  ui.input.value = 'fris';
  ui.input.fire('input');
  assert.deepEqual(optionTexts(ui.list), ['Ultimate frisbee']);
  ui.input.fire('keydown', {key: 'Enter'});
  assert.deepEqual(changes, [['basketball', 'ultimate_frisbee']]);
});

test('facility lookahead finds Track & Field and both soccer field types by word', () => {
  const {ui} = setup(FACILITIES);
  ui.input.value = 'track';
  ui.input.fire('input');
  assert.deepEqual(optionTexts(ui.list), ['Track & Field']);
  ui.input.value = 'regulation';
  ui.input.fire('input');
  assert.deepEqual(optionTexts(ui.list), ['Regulation Soccer', 'Non-Regulation Soccer']);
  ui.input.value = 'non';
  ui.input.fire('input');
  assert.deepEqual(optionTexts(ui.list), ['Non-Regulation Soccer']);
});
