document.querySelectorAll('[data-auto-dismiss]').forEach((message) => {
  const delay = Number(message.dataset.autoDismiss);
  if (!Number.isFinite(delay) || delay <= 0) return;

  window.setTimeout(() => {
    message.remove();
  }, delay);
});
