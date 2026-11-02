// Adds a show/hide button to every password input on the page.
document.querySelectorAll('input[type="password"]').forEach((input) => {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'password-toggle';
  button.setAttribute('aria-label', 'Show password');
  button.setAttribute('aria-pressed', 'false');

  button.addEventListener('click', () => {
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(show));
    button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
  });

  input.after(button);
});
