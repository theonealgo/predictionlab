import { useRef } from 'react';

// Lets a sideways strip be dragged with the mouse. A drag does not
// count as a click on the item it ends on.
export function useDragScroll() {
  const ref = useRef(null);
  const drag = useRef({ down: false, startX: 0, startLeft: 0, moved: false });

  function onPointerDown(event) {
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    drag.current = { down: true, startX: event.clientX, startLeft: ref.current.scrollLeft, moved: false };
  }

  function onPointerMove(event) {
    const state = drag.current;
    if (!state.down) return;
    const dx = event.clientX - state.startX;
    if (Math.abs(dx) > 4) state.moved = true;
    ref.current.scrollLeft = state.startLeft - dx;
  }

  function endDrag() {
    drag.current.down = false;
  }

  function onClickCapture(event) {
    if (!drag.current.moved) return;
    event.preventDefault();
    event.stopPropagation();
    drag.current.moved = false;
  }

  return {
    ref,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: endDrag,
      onPointerLeave: endDrag,
      onClickCapture,
      onDragStart: (event) => event.preventDefault(),
    },
  };
}
