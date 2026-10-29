(() => {
  const fieldset = document.getElementById('profile-fieldset');
  const editButton = document.getElementById('profile-edit-button');
  const actions = document.getElementById('profile-settings-actions');

  if (!fieldset || !editButton || !actions) return;

  // Unlock the fields in place. Without JavaScript the link reloads the page with ?edit=1.
  editButton.addEventListener('click', (event) => {
    event.preventDefault();
    fieldset.disabled = false;
    editButton.hidden = true;
    actions.hidden = false;
    window.history.replaceState(null, '', editButton.href);
    document.getElementById('id_display_username')?.focus();
  });
})();
