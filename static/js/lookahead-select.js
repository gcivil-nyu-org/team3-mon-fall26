/* Lookahead multi-select with removable pills (US-2.3 zip codes; reused by US-2.4 sports/facilities).
 *
 * Progressive enhancement: mark any <select multiple> with data-lookahead. Without JavaScript the
 * plain multi-select still submits repeated ?name= values. With JavaScript the select is hidden and
 * stays the single source of truth, so the form submits exactly the same parameters.
 *
 * Optional attributes on the <select>:
 *   data-placeholder  text for the search box (default "Type to search")
 *   data-empty        message when nothing matches (default "No matches")
 */
(() => {
  'use strict';

  /** True when `query` matches the whole label or the start of any word in it ("112" -> "11211", "fris" -> "Ultimate frisbee"). */
  function matches(label, query) {
    const q = String(query).trim().toLowerCase();
    if (!q) return true;
    const text = String(label).toLowerCase();
    return text.startsWith(q) || text.split(/[^a-z0-9]+/).some((word) => word.startsWith(q));
  }

  /** Unselected options that match the query, in their original order. */
  function suggestions(options, query) {
    return options.filter((option) => !option.selected && option.value && matches(option.label, query));
  }

  let uid = 0;

  function enhance(select) {
    if (select.dataset.lookaheadReady) return;
    select.dataset.lookaheadReady = '1';
    const doc = select.ownerDocument;
    const id = select.id || `lookahead-${++uid}`;
    const listId = `${id}-listbox`;

    const wrapper = doc.createElement('div');
    wrapper.className = 'lookahead';
    const control = doc.createElement('div');
    control.className = 'lookahead-control';
    const pills = doc.createElement('span');
    pills.className = 'lookahead-pills';
    const input = doc.createElement('input');
    input.type = 'text';
    input.id = `${id}-search`;
    input.className = 'lookahead-input';
    input.autocomplete = 'off';
    input.placeholder = select.dataset.placeholder || 'Type to search';
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', listId);
    if (select.inputMode) input.inputMode = select.inputMode;
    if (select.dataset.inputmode) input.inputMode = select.dataset.inputmode;
    const list = doc.createElement('ul');
    list.id = listId;
    list.className = 'lookahead-list';
    list.setAttribute('role', 'listbox');
    list.hidden = true;

    control.append(pills, input);
    wrapper.append(control, list);
    select.after(wrapper);
    select.hidden = true;
    select.tabIndex = -1;

    // Point the field's <label> at the search box so it stays announced and clickable.
    const label = select.id ? doc.querySelector(`label[for="${select.id}"]`) : null;
    if (label) label.htmlFor = input.id;

    let active = -1;
    let shown = [];

    function options() { return Array.from(select.options); }

    function setActive(index) {
      const items = list.querySelectorAll('[role="option"]');
      items.forEach((item, i) => item.setAttribute('aria-selected', String(i === index)));
      active = index;
      if (index >= 0 && items[index]) {
        input.setAttribute('aria-activedescendant', items[index].id);
        items[index].scrollIntoView?.({block: 'nearest'});
      } else {
        input.removeAttribute('aria-activedescendant');
      }
    }

    function close() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      setActive(-1);
    }

    function renderList() {
      shown = suggestions(options(), input.value);
      list.replaceChildren();
      if (!shown.length) {
        const empty = doc.createElement('li');
        empty.className = 'lookahead-empty';
        empty.textContent = select.dataset.empty || 'No matches';
        list.append(empty);
      }
      shown.forEach((option, index) => {
        const item = doc.createElement('li');
        item.id = `${listId}-${index}`;
        item.className = 'lookahead-option';
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', 'false');
        item.dataset.value = option.value;
        item.textContent = option.label;
        // Keep focus in the input so the list doesn't close before the click lands.
        item.addEventListener('mousedown', (event) => event.preventDefault());
        item.addEventListener('click', () => choose(option));
        list.append(item);
      });
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      setActive(shown.length && input.value.trim() ? 0 : -1);
    }

    function renderPills() {
      pills.replaceChildren();
      options().filter((option) => option.selected).forEach((option) => {
        const pill = doc.createElement('button');
        pill.type = 'button';
        pill.className = 'lookahead-pill';
        pill.dataset.value = option.value;
        pill.setAttribute('aria-label', `Remove ${option.label}`);
        pill.textContent = option.label;
        const x = doc.createElement('span');
        x.setAttribute('aria-hidden', 'true');
        x.textContent = '×';
        pill.append(x);
        pill.addEventListener('click', () => remove(option));
        pills.append(pill);
      });
    }

    function changed() {
      renderPills();
      select.dispatchEvent(new Event('change', {bubbles: true}));
    }

    function choose(option) {
      option.selected = true;
      input.value = '';
      close();
      changed();
    }

    function remove(option) {
      option.selected = false;
      changed();
      input.focus();
    }

    input.addEventListener('focus', renderList);
    input.addEventListener('click', renderList);
    input.addEventListener('input', renderList);
    input.addEventListener('blur', close);
    input.addEventListener('keydown', (event) => {
      const count = shown.length;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (list.hidden) renderList();
        else if (count) setActive((active + 1) % count);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (count) setActive(active <= 0 ? count - 1 : active - 1);
      } else if (event.key === 'Enter') {
        // Never submit the whole form from the lookahead box.
        event.preventDefault();
        if (!list.hidden && active >= 0 && shown[active]) choose(shown[active]);
      } else if (event.key === 'Escape') {
        if (!list.hidden) { event.preventDefault(); close(); }
      } else if (event.key === 'Backspace' && !input.value) {
        const selected = options().filter((option) => option.selected);
        if (selected.length) remove(selected[selected.length - 1]);
      }
    });

    renderPills();
    return {input, list, pills, wrapper};
  }

  function enhanceAll(root) {
    (root || document).querySelectorAll('select[multiple][data-lookahead]').forEach(enhance);
  }

  const api = {matches, suggestions, enhance, enhanceAll};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof window !== 'undefined') {
    window.LookaheadSelect = api;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => enhanceAll());
    else enhanceAll();
  }
})();
