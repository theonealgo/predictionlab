import { useEffect, useState } from 'react';

export default function useVisibleOnce(ref, enabled) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (!enabled || visible || !ref.current) return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setVisible(true);
        observer.disconnect();
      }
    });
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [enabled, visible, ref]);
  return enabled && visible;
}
