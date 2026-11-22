const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('static/js/event-discussion.js', 'utf8');

function setup({open = true, hidden = false, initialIds = []} = {}) {
  const state = {fetchCalls: [], intervalDelay: null, intervalCallback: null, postHandler: null};
  const list = {
    dataset: {
      latestMessageId: initialIds.length ? String(Math.max(...initialIds)) : '0',
      updatesUrl: '/updates/',
      discussionOpen: String(open),
    },
    children: initialIds.map((id) => ({dataset: {messageId: String(id)}, announcement: false})),
    querySelector(selector) {
      const idMatch = selector.match(/^\[data-message-id="(\d+)"\]$/);
      if (idMatch) return this.children.find((item) => item.dataset.messageId === idMatch[1]) || null;
      if (selector === '[data-message-id]') return this.children[0] || null;
      if (selector === '.event-discussion-message--announcement') {
        return this.children.find((item) => item.announcement) || null;
      }
      if (selector === '.event-discussion-message:not(.event-discussion-message--announcement)') {
        return this.children.find((item) => !item.announcement) || null;
      }
      return null;
    },
    closest() {
      return discussion;
    },
    append(item) {
      this.children.push(item);
    },
    insertBefore(item, reference) {
      const index = this.children.indexOf(reference);
      this.children.splice(index < 0 ? this.children.length : index, 0, item);
    },
    after(item) {
      state.afterMessage = item;
    },
  };
  const status = {textContent: ''};
  const button = {disabled: false};
  const textarea = {value: 'New message'};
  const announcementCheckbox = {checked: false};
  const form = {
    action: '/post/',
    listeners: {},
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    },
    querySelector(selector) {
      if (selector === 'button[type="submit"]') return button;
      if (selector === 'textarea[name="body"]') return textarea;
      if (selector === 'input[name="is_announcement"]') return announcementCheckbox;
      if (selector === '[data-event-discussion-status]') return status;
      return null;
    },
    closest() {
      return discussion;
    },
  };
  const empty = {remove() {discussion.empty = null;}};
  const discussion = {
    empty: empty,
    listeners: {},
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    },
    querySelector(selector) {
      if (selector === '[data-event-discussion-form]') return open ? form : null;
      if (selector === '.event-discussion-empty') return this.empty;
      return null;
    },
  };
  const document = {
    hidden,
    querySelectorAll(selector) {
      return selector === '[data-event-discussion-list]' ? [list] : [];
    },
    createElement() {
      const template = {content: {firstElementChild: null}};
      Object.defineProperty(template, 'innerHTML', {
        set(html) {
          template.content.firstElementChild = {
            html,
            dataset: {},
            announcement: html.includes('announcement'),
          };
        },
      });
      return template;
    },
  };
  const window = {
    location: {href: 'https://example.test/events/1/'},
    setInterval(callback, delay) {
      state.intervalCallback = callback;
      state.intervalDelay = delay;
      return 1;
    },
    clearInterval() { state.intervalCleared = true; },
  };
  const fetch = async (url, options) => {
    state.fetchCalls.push({url: String(url), options});
    return state.fetchResponse();
  };
  const context = {
    document,
    window,
    URL,
    FormData: function FormData() {},
    fetch,
  };

  vm.runInNewContext(source, context);

  return {state, list, form, button, textarea, status, announcementCheckbox, document, discussion};
}

test('polls every 30 seconds only for an open discussion', () => {
  assert.equal(setup({open: true}).state.intervalDelay, 30000);
  assert.equal(setup({open: false}).state.intervalDelay, null);
});

test('does not request updates while the document is hidden', async () => {
  const {state} = setup({hidden: true});
  await state.intervalCallback();
  assert.equal(state.fetchCalls.length, 0);
});

test('stops polling when the event discussion closes', async () => {
  const {state} = setup();
  state.fetchResponse = async () => ({
    ok: true,
    json: async () => ({discussion_open: false, messages: [], latest_id: 0}),
  });

  await state.intervalCallback();

  assert.equal(state.intervalCleared, true);
});

test('keeps the poll cursor unchanged after local posting and skips duplicate IDs', async () => {
  const {state, list, form, textarea} = setup({initialIds: [1]});
  let fetchCount = 0;
  state.fetchResponse = async () => {
    fetchCount += 1;
    if (fetchCount === 1) {
      return {
        ok: true,
        json: async () => ({html: '<li>local-3</li>', id: 3}),
      };
    }
    return {
      ok: true,
      json: async () => ({
        discussion_open: true,
        latest_id: 3,
        messages: [
          {id: 2, html: '<li>missed-2</li>'},
          {id: 3, html: '<li>local-3</li>'},
        ],
      }),
    };
  };
  const submitEvent = {preventDefault() {}};

  await form.listeners.submit(submitEvent);
  assert.deepEqual(list.children.map((item) => item.dataset.messageId), ['1', '3']);
  assert.equal(textarea.value, '');
  await state.intervalCallback();

  assert.match(state.fetchCalls[1].url, /after=1$/);
  assert.deepEqual(list.children.map((item) => item.dataset.messageId), ['1', '3', '2']);
  assert.equal(list.dataset.latestMessageId, '3');
});

test('switches a message into inline edit mode and restores it on cancel', () => {
  const {discussion} = setup();
  let reset = false;
  let focused = false;
  const editButton = {
    hidden: false,
    expanded: 'false',
    setAttribute(name, value) {
      if (name === 'aria-expanded') this.expanded = value;
    },
    closest() {
      return message;
    },
  };
  const cancelButton = {
    closest() {
      return editForm;
    },
  };
  const textarea = {focus() { focused = true; }};
  const actions = {
    hidden: false,
    querySelector() {
      return editButton;
    },
  };
  const body = {hidden: false};
  const editForm = {
    hidden: true,
    closest() {
      return message;
    },
    querySelector() {
      return textarea;
    },
    reset() {
      reset = true;
    },
  };
  const message = {
    querySelector(selector) {
      if (selector === '[data-message-edit-form]') return editForm;
      if (selector === '[data-message-body]') return body;
      if (selector === '[data-message-actions]') return actions;
      return null;
    },
  };
  const click = (button) => discussion.listeners.click({
    target: {
      closest(selector) {
        if (selector === '[data-message-edit-toggle]') return button === editButton ? editButton : null;
        if (selector === '[data-message-edit-cancel]') return button === cancelButton ? cancelButton : null;
        return null;
      },
    },
  });

  click(editButton);
  assert.equal(editForm.hidden, false);
  assert.equal(body.hidden, true);
  assert.equal(actions.hidden, true);
  assert.equal(editButton.expanded, 'true');
  assert.equal(focused, true);

  click(cancelButton);
  assert.equal(reset, true);
  assert.equal(editForm.hidden, true);
  assert.equal(body.hidden, false);
  assert.equal(actions.hidden, false);
  assert.equal(editButton.expanded, 'false');
});

test('saves an edit over AJAX and replaces the message without navigating', async () => {
  const {state, discussion} = setup();
  const submitButton = {disabled: false};
  const status = {textContent: ''};
  let replacement;
  const message = {
    replaceWith(item) {
      replacement = item;
    },
  };
  const editForm = {
    action: '/edit/',
    querySelector(selector) {
      if (selector === 'button[type="submit"]') return submitButton;
      if (selector === '[data-message-edit-status]') return status;
      return null;
    },
    closest() {
      return message;
    },
  };
  state.fetchResponse = async () => ({
    ok: true,
    json: async () => ({html: '<li>Updated text</li>', id: 8}),
  });
  const submitEvent = {
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
    target: {
      closest(selector) {
        return selector === '[data-message-edit-form]' ? editForm : null;
      },
    },
  };

  await discussion.listeners.submit(submitEvent);

  assert.equal(submitEvent.prevented, true);
  assert.equal(state.fetchCalls[0].url, '/edit/');
  assert.equal(state.fetchCalls[0].options.method, 'POST');
  assert.equal(state.fetchCalls[0].options.headers['X-Requested-With'], 'XMLHttpRequest');
  assert.equal(replacement.html, '<li>Updated text</li>');
  assert.equal(replacement.dataset.messageId, '8');
  assert.equal(submitButton.disabled, false);
  assert.equal(status.textContent, '');
});

test('deletes a message over AJAX without navigating and shows the empty state', async () => {
  const {state, discussion} = setup();
  const submitButton = {disabled: false};
  const status = {textContent: ''};
  let removed = false;
  const message = {
    dataset: {messageId: '4'},
    remove() {
      removed = true;
    },
  };
  const deleteForm = {
    action: '/delete/',
    querySelector(selector) {
      if (selector === 'button[type="submit"]') return submitButton;
      if (selector === '[data-message-delete-status]') return status;
      return null;
    },
    closest() {
      return message;
    },
  };
  const createSubmitEvent = (defaultPrevented = false) => ({
    defaultPrevented,
    prevented: false,
    preventDefault() {
      this.prevented = true;
    },
    target: {
      closest(selector) {
        return selector === '[data-message-delete-form]' ? deleteForm : null;
      },
    },
  });
  state.fetchResponse = async () => ({
    ok: true,
    json: async () => ({deleted: true, id: 4}),
  });

  await discussion.listeners.submit(createSubmitEvent(true));

  assert.equal(state.fetchCalls.length, 0);
  assert.equal(removed, false);

  const submitEvent = createSubmitEvent();
  await discussion.listeners.submit(submitEvent);

  assert.equal(submitEvent.prevented, true);
  assert.equal(state.fetchCalls[0].url, '/delete/');
  assert.equal(state.fetchCalls[0].options.method, 'POST');
  assert.equal(state.fetchCalls[0].options.headers['X-Requested-With'], 'XMLHttpRequest');
  assert.equal(removed, true);
  assert.equal(state.afterMessage.textContent, 'No messages yet. Ask a question below!');
  assert.equal(submitButton.disabled, false);
  assert.equal(status.textContent, '');
});

test('unchecks the announcement option after a successful announcement post', async () => {
  const {state, form, announcementCheckbox} = setup();
  announcementCheckbox.checked = true;
  state.fetchResponse = async () => ({
    ok: true,
    json: async () => ({
      html: '<li class="event-discussion-message--announcement">Announcement</li>',
      id: 1,
      is_announcement: true,
    }),
  });

  await form.listeners.submit({preventDefault() {}});

  assert.equal(announcementCheckbox.checked, false);
});

test('inserts an AJAX-posted announcement before announcements and regular messages', async () => {
  const {state, list, form} = setup({initialIds: [1, 2]});
  list.children[0].announcement = true;
  list.children[0].html = '<li class="event-discussion-message--announcement">Old announcement</li>';
  list.children[1].html = '<li class="event-discussion-message">Regular message</li>';
  state.fetchResponse = async () => ({
    ok: true,
    json: async () => ({
      html: '<li class="event-discussion-message--announcement">New announcement</li>',
      id: 3,
      is_announcement: true,
    }),
  });

  await form.listeners.submit({preventDefault() {}});

  assert.deepEqual(list.children.map((item) => item.dataset.messageId), ['3', '1', '2']);
  assert.equal(list.children[0].announcement, true);
});
