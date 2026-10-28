(() => {
  'use strict';
  if (globalThis.__pdtTranslator) return;
  const chinese = /[\u3400-\u9fff]/;
  const blocked = 'script,style,noscript,textarea,input,select,option,[contenteditable],svg,canvas,iframe,[translate="no"]';
  const records = new Map();
  const identities = new WeakMap();
  const documentID = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  let serial = 0;
  let revision = 0;
  let lastScroll = 0;
  let notificationPending = false;

  function notifyLayout() {
    revision++;
    if (notificationPending) return;
    notificationPending = true;
    requestAnimationFrame(() => {
      notificationPending = false;
      try { window.webkit?.messageHandlers?.pdtLayout?.postMessage('layout'); } catch (_) {}
    });
  }
  addEventListener('scroll', () => { lastScroll = Date.now(); notifyLayout(); }, true);
  addEventListener('resize', notifyLayout);

  function rectangle(node) {
    const range = document.createRange();
    range.selectNodeContents(node);
    return range.getBoundingClientRect();
  }
  function visible(rect) {
    return rect.width > 0 && rect.height > 0 && rect.right > 0 && rect.bottom > 0 &&
      rect.left < innerWidth && rect.top < innerHeight;
  }
  function inRegion(rect, roi) {
    const x = (rect.left + rect.width / 2) / innerWidth;
    const y = (rect.top + rect.height / 2) / innerHeight;
    return x >= roi.x && x <= roi.x + roi.width && y >= roi.y && y <= roi.y + roi.height;
  }
  function recordFor(node) {
    let id = identities.get(node);
    if (!id || !records.has(id)) {
      id = String(++serial);
      identities.set(node, id);
      records.set(id, { node, original: node.nodeValue, applied: null });
    }
    const record = records.get(id);
    // Sites may reuse the same text node for another product or button state.
    if (node.nodeValue !== record.original && node.nodeValue !== record.applied) {
      record.original = node.nodeValue;
      record.applied = null;
    }
    return [id, record];
  }
  function normalized(rect) {
    return { x: rect.left / innerWidth, y: rect.top / innerHeight,
      width: rect.width / innerWidth, height: rect.height / innerHeight };
  }
  function metadata() {
    return { documentID, revision, scrolling: Date.now() - lastScroll < 350,
      width: innerWidth, height: innerHeight };
  }
  const observer = new MutationObserver(mutations => {
    if (mutations.some(change => {
      if (change.type !== 'characterData') return true;
      const id = identities.get(change.target);
      const record = id && records.get(id);
      return !record || change.target.nodeValue !== record.applied;
    })) notifyLayout();
  });
  if (document.documentElement) observer.observe(document.documentElement,
    { subtree: true, childList: true, characterData: true });

  globalThis.__pdtTranslator = {
    collect(roi, limit = 48) {
      for (const [id, record] of records) if (!record.node.isConnected) records.delete(id);
      const items = [];
      if (!document.body) return { ...metadata(), items };
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      let visited = 0;
      let node;
      while ((node = walker.nextNode()) && items.length < limit && visited++ < 6000) {
        const parent = node.parentElement;
        if (!parent || parent.closest(blocked)) continue;
        const [id, record] = recordFor(node);
        if (record.applied === node.nodeValue || !chinese.test(record.original)) continue;
        const text = record.original.trim();
        if (!text || text.length > 320) continue;
        const style = getComputedStyle(parent);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) continue;
        const rect = rectangle(node);
        if (!visible(rect) || !inRegion(rect, roi)) continue;
        items.push({ id, text });
      }
      return { ...metadata(), items };
    },
    apply(expectedDocument, translations) {
      if (expectedDocument !== documentID) return 0;
      let count = 0;
      for (const item of translations) {
        const record = records.get(item.id);
        if (!record || !record.node.isConnected || record.original.trim() !== item.source ||
            (record.node.nodeValue !== record.original && record.node.nodeValue !== record.applied) ||
            typeof item.target !== 'string') continue;
        const leading = record.original.match(/^\s*/)[0];
        const trailing = record.original.match(/\s*$/)[0];
        record.applied = leading + item.target + trailing;
        record.node.nodeValue = record.applied;
        count++;
      }
      if (count) notifyLayout();
      return count;
    },
    restore() {
      for (const record of records.values()) {
        if (record.node.isConnected && record.applied !== null && record.node.nodeValue === record.applied)
          record.node.nodeValue = record.original;
        record.applied = null;
      }
      notifyLayout();
    },
    snapshotInfo() {
      // Mask form controls before OCR: do not extract typed account/address data.
      const privateRects = Array.from(document.querySelectorAll('input,textarea,select,[contenteditable],iframe'))
        .map(element => element.getBoundingClientRect()).filter(visible).map(normalized).slice(0,100);
      return { ...metadata(), privateRects };
    }
  };
})();
