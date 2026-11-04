import { useChrome } from '../chrome.js';

// The site footer: the shared template, rendered by the server (templates/partials/site_directory_footer.html).
export default function Footer() {
  const chrome = useChrome();
  if (!chrome) return null;
  return <div dangerouslySetInnerHTML={{ __html: chrome.footer }} />;
}
