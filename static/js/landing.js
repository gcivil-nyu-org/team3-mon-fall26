// Landing page: solid nav once scrolled, and reveal elements as they enter the viewport.
const nav = document.querySelector('[data-nav]');
const updateNav = () => nav.classList.toggle('is-scrolled', window.scrollY > 8);
updateNav();
window.addEventListener('scroll', updateNav, { passive: true });

const targets = document.querySelectorAll('.reveal');
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

if (reduceMotion || !('IntersectionObserver' in window)) {
  targets.forEach((el) => el.classList.add('is-visible'));
} else {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible');
        observer.unobserve(entry.target);
      }
    });
  }, { threshold: 0.15, rootMargin: '0px 0px -40px 0px' });

  targets.forEach((el) => observer.observe(el));
}
