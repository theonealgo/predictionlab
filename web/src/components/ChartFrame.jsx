import { useRef, useState } from 'react';
import { routeLinks, trimFrame } from '../frameTools.js';

// A sport's results chart view (the results page with ?view=chart), shown inline on the main page. The frame
// loads when it scrolls near the screen, and it sizes itself to the page it shows.
export default function ChartFrame({ src, title }) {
  const frame = useRef(null);
  const [height, setHeight] = useState(480);
  return (
    <iframe
      ref={frame}
      className="pl-chart-frame"
      title={title}
      src={src}
      loading="lazy"
      style={{ height }}
      onLoad={() => {
        trimFrame(frame.current);
        routeLinks(frame.current, null, null);
        try {
          setHeight(frame.current.contentDocument.documentElement.scrollHeight);
        } catch (err) {
          // Keep the default height if the page cannot be measured.
        }
      }}
    />
  );
}
