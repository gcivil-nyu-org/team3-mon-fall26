/* #122: "Back to results" asks Explore to put the map back where it was (see explore.js).
 * The flag only lasts a minute and is cleared on arrival, so a link opened later starts fresh. */
document.addEventListener('click', (event) => {
  if (!event.target.closest?.('[data-explore-return]')) return;
  try { window.sessionStorage.setItem('nycactive.exploreReturn', String(Date.now())); } catch (_) { /* blocked */ }
});
