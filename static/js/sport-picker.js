(() => {
  const sportList = document.getElementById('profile-sport-list');
  const dropdown = document.getElementById('profile-sport-dropdown');
  const summary = document.getElementById('profile-sport-summary');
  const customInput = document.getElementById('id_custom_sport');
  const addButton = document.getElementById('add-custom-sport');

  if (!sportList || !dropdown || !summary || !customInput || !addButton) return;

  const updateSummary = () => {
    const selectedSports = [...sportList.querySelectorAll('input[name="favorite_sports"]:checked')]
      .map((input) => input.value);
    summary.textContent = selectedSports.length ? selectedSports.join(', ') : 'Choose sports';
    summary.title = selectedSports.join(', ');
  };

  document.addEventListener('pointerdown', (event) => {
    if (dropdown.open && !dropdown.contains(event.target)) dropdown.open = false;
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && dropdown.open) {
      dropdown.open = false;
      dropdown.querySelector('summary').focus();
    }
  });

  sportList.addEventListener('change', updateSummary);

  const addSport = () => {
    const name = customInput.value.trim();
    if (!name) {
      customInput.focus();
      return;
    }

    const existing = [...sportList.querySelectorAll('input[name="favorite_sports"]')]
      .find((input) => input.value.toLocaleLowerCase() === name.toLocaleLowerCase());

    if (existing) {
      existing.checked = true;
    } else {
      const label = document.createElement('label');
      label.className = 'profile-sport-option';
      label.dataset.sportOption = '';

      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.name = 'favorite_sports';
      checkbox.value = name;
      checkbox.checked = true;

      const text = document.createElement('span');
      text.textContent = name;

      label.append(checkbox, text);
      sportList.append(label);
    }

    customInput.value = '';
    customInput.focus();
    updateSummary();
  };

  updateSummary();
  addButton.addEventListener('click', addSport);
  customInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      addSport();
    }
  });
})();
