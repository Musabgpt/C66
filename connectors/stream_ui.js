/* A failed model draft is separate from committed replies and executed tools. */
(() => {
  'use strict';
  window.MusabStreamView = Object.freeze({
    begin(view) {
      // Older nodes stay visible, but can no longer be removed by this request.
      view.text = null;
      view.reasoning = null;
    },
    reset(view) {
      if (view.text) view.text.remove();
      if (view.reasoning) view.reasoning.remove();
      view.text = null;
      view.reasoning = null;
    },
  });
})();
