document.querySelectorAll('[data-event-discussion-list]').forEach((list) => {
  const discussion = list.closest('.event-discussion');
  const form = discussion.querySelector('[data-event-discussion-form]');
  discussion.addEventListener('click', (event) => {
    const editButton = event.target.closest('[data-message-edit-toggle]');
    if (editButton) {
      const message = editButton.closest('[data-message-id]');
      const editForm = message.querySelector('[data-message-edit-form]');
      editForm.hidden = false;
      message.querySelector('[data-message-body]').hidden = true;
      message.querySelector('[data-message-actions]').hidden = true;
      editButton.setAttribute('aria-expanded', 'true');
      editForm.querySelector('textarea[name="body"]').focus();
      return;
    }

    const cancelButton = event.target.closest('[data-message-edit-cancel]');
    if (cancelButton) {
      const editForm = cancelButton.closest('[data-message-edit-form]');
      const message = editForm.closest('[data-message-id]');
      editForm.reset();
      editForm.hidden = true;
      message.querySelector('[data-message-body]').hidden = false;
      const actions = message.querySelector('[data-message-actions]');
      actions.hidden = false;
      actions.querySelector('[data-message-edit-toggle]').setAttribute('aria-expanded', 'false');
    }
  });
  discussion.addEventListener('submit', async (event) => {
    if (event.defaultPrevented) return;

    const deleteForm = event.target.closest('[data-message-delete-form]');
    if (deleteForm) {
      event.preventDefault();
      const button = deleteForm.querySelector('button[type="submit"]');
      const status = deleteForm.querySelector('[data-message-delete-status]');
      button.disabled = true;
      status.textContent = '';

      try {
        const response = await fetch(deleteForm.action, {
          method: 'POST',
          body: new FormData(deleteForm),
          credentials: 'same-origin',
          headers: {'X-Requested-With': 'XMLHttpRequest'},
        });
        const result = await response.json();
        const message = deleteForm.closest('[data-message-id]');
        if (!response.ok || !result.deleted || result.id !== Number(message.dataset.messageId)) {
          status.textContent = result.error || 'Could not delete your message. Please try again.';
          return;
        }

        message.remove();
        if (!list.querySelector('[data-message-id]')) {
          const empty = document.createElement('p');
          empty.className = 'event-discussion-empty';
          empty.textContent = discussionOpen ? 'No messages yet. Ask a question below!' : 'No messages were posted.';
          list.after(empty);
        }
      } catch {
        status.textContent = 'Could not delete your message. Please try again.';
      } finally {
        button.disabled = false;
      }
      return;
    }

    const editForm = event.target.closest('[data-message-edit-form]');
    if (!editForm) return;

    event.preventDefault();
    const button = editForm.querySelector('button[type="submit"]');
    const status = editForm.querySelector('[data-message-edit-status]');
    button.disabled = true;
    status.textContent = '';

    try {
      const response = await fetch(editForm.action, {
        method: 'POST',
        body: new FormData(editForm),
        credentials: 'same-origin',
        headers: {'X-Requested-With': 'XMLHttpRequest'},
      });
      const result = await response.json();
      if (!response.ok) {
        status.textContent = result.error || 'Could not save your changes. Please try again.';
        return;
      }

      const updatedMessage = document.createElement('template');
      updatedMessage.innerHTML = result.html.trim();
      const item = updatedMessage.content.firstElementChild;
      if (!item) {
        status.textContent = 'Could not save your changes. Please try again.';
        return;
      }
      item.dataset.messageId = String(result.id);
      editForm.closest('[data-message-id]').replaceWith(item);
    } catch {
      status.textContent = 'Could not save your changes. Please try again.';
    } finally {
      button.disabled = false;
    }
  });
  let latestId = Number(list.dataset.latestMessageId) || 0;
  const discussionOpen = list.dataset.discussionOpen === 'true';
  let polling = false;
  let pollTimer = null;

  const addMessage = (html, id, isAnnouncement = false) => {
    const messageId = Number(id);
    if (!Number.isInteger(messageId) || list.querySelector(`[data-message-id="${messageId}"]`)) return;

    const message = document.createElement('template');
    message.innerHTML = html.trim();
    const item = message.content.firstElementChild;
    if (!item) return;
    item.dataset.messageId = String(messageId);

    if (isAnnouncement) {
      const firstAnnouncement = list.querySelector('.event-discussion-message--announcement');
      const firstRegular = list.querySelector('.event-discussion-message:not(.event-discussion-message--announcement)');
      list.insertBefore(item, firstAnnouncement || firstRegular);
    } else {
      list.append(item);
    }

    discussion.querySelector('.event-discussion-empty')?.remove();
  };

  if (form) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();

      const button = form.querySelector('button[type="submit"]');
      const textarea = form.querySelector('textarea[name="body"]');
      const status = form.querySelector('[data-event-discussion-status]');
      button.disabled = true;
      status.textContent = '';

      try {
        const response = await fetch(form.action, {
          method: 'POST',
          body: new FormData(form),
          credentials: 'same-origin',
          headers: {'X-Requested-With': 'XMLHttpRequest'},
        });
        const result = await response.json();
        if (!response.ok) {
          status.textContent = result.error || 'Could not post your message. Please try again.';
          return;
        }

        addMessage(result.html, result.id, result.is_announcement);
        textarea.value = '';
        const announcementCheckbox = form.querySelector('input[name="is_announcement"]');
        if (announcementCheckbox) announcementCheckbox.checked = false;
      } catch {
        status.textContent = 'Could not post your message. Please try again.';
      } finally {
        button.disabled = false;
      }
    });
  }

  const updateDiscussion = async () => {
    if (polling || document.hidden || !discussionOpen) return;
    polling = true;
    try {
      const url = new URL(list.dataset.updatesUrl, window.location.href);
      url.searchParams.set('after', String(latestId));
      const response = await fetch(url, {credentials: 'same-origin'});
      if (!response.ok) return;
      const result = await response.json();
      if (!result.discussion_open) {
        window.clearInterval(pollTimer);
        return;
      }
      result.messages.forEach((message) => {
        addMessage(message.html, message.id, message.is_announcement);
      });
      latestId = Math.max(latestId, Number(result.latest_id) || 0);
      list.dataset.latestMessageId = String(latestId);
    } catch {
      // A later poll retries transient network errors without interrupting posting.
    } finally {
      polling = false;
    }
  };

  if (discussionOpen) pollTimer = window.setInterval(updateDiscussion, 30000);
});
