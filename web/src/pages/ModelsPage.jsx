import { useEffect, useRef } from 'react';
import { trimFrame, whenFrameParsed } from '../frameTools.js';

/**
 * Models-menu pages: classic page + independent Sports News / Latest blog box.
 * No sports left rail — only the page and the blog box.
 * Ladder stays a quiet iframe: no blog column, no remount churn.
 */
export default function ModelsPage({ classicPath, title }) {
  const frame = useRef(null);
  const qs = typeof window !== 'undefined' ? window.location.search : '';
  const src = `/classic${classicPath}${qs}`;
  const isLadder = classicPath === '/ladder-picks' || classicPath === '/ladder';

  useEffect(() => {
    const node = frame.current;
    if (!node) return undefined;
    const prepare = () => trimFrame(node);
    return whenFrameParsed(node, src, prepare);
  }, [src]);

  return (
    <div className={`pl-models-layout${isLadder ? ' is-ladder' : ''}`}>
      <main className="pl-models-main">
        <iframe
          ref={frame}
          className="pl-models-frame"
          title={title || 'Models'}
          src={src}
          onLoad={() => trimFrame(frame.current)}
        />
      </main>
    </div>
  );
}
