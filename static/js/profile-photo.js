(() => {
  const input = document.getElementById('id_avatar');
  const preview = document.getElementById('profile-photo-preview');
  const button = document.getElementById('profile-photo-button');
  const removeButton = document.getElementById('profile-photo-remove');
  const removeInput = document.getElementById('id_remove_avatar');

  if (!input || !preview || !button || !removeButton || !removeInput) return;

  const showPlaceholder = () => {
    const placeholder = document.createElement('span');
    placeholder.className = 'profile-photo-placeholder';
    placeholder.setAttribute('aria-hidden', 'true');
    preview.replaceChildren(placeholder);
    button.textContent = 'Upload photo';
    removeButton.hidden = true;
  };

  input.addEventListener('change', () => {
    const [file] = input.files;
    if (!file) return;

    let image = preview.querySelector('img');
    if (!image) {
      image = document.createElement('img');
      preview.replaceChildren(image);
    }
    image.alt = 'Your new profile photo';
    image.src = URL.createObjectURL(file);
    button.textContent = 'Change photo';
    removeButton.hidden = false;
    removeInput.value = 'False';
  });

  // The photo is removed when the form is saved.
  removeButton.addEventListener('click', () => {
    input.value = '';
    removeInput.value = 'True';
    showPlaceholder();
    button.focus();
  });

  // Keep the photo cleared if the form comes back with errors after clicking the X.
  if (removeInput.value === 'True') showPlaceholder();
})();
