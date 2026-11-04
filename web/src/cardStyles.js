// The card markup comes from the server with its own CSS. Put that CSS in the page once.
export function applyCardStyles(css) {
  let tag = document.getElementById('pl-card-css');
  if (!tag) {
    tag = document.createElement('style');
    tag.id = 'pl-card-css';
    document.head.appendChild(tag);
  }
  tag.textContent = css || '';
}
