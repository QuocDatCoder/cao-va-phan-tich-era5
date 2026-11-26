(() => {
  if (window.era5MultiselectLabels) return;
  const dropdown = '[data-testid="stMultiSelectDropdown"]';
  const translate = menu => {
    const walker = document.createTreeWalker(menu, NodeFilter.SHOW_TEXT);
    for (let text = walker.nextNode(); text; text = walker.nextNode()) {
      if (text.data === 'Select all') text.data = 'Chọn tất cả';
      const matches = /^Select ([0-9]+) matches$/.exec(text.data);
      if (matches) text.data = 'Chọn ' + matches[1] + ' kết quả phù hợp';
    }
  };
  window.era5MultiselectLabels = new MutationObserver(records => {
    const menus = new Set();
    for (const record of records) {
      const target = record.target.nodeType === Node.ELEMENT_NODE
        ? record.target : record.target.parentElement;
      const menu = target && target.closest(dropdown);
      if (menu) menus.add(menu);
      for (const node of record.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        if (node.matches(dropdown)) menus.add(node);
        node.querySelectorAll(dropdown).forEach(item => menus.add(item));
      }
    }
    menus.forEach(translate);
  });
  document.querySelectorAll(dropdown).forEach(translate);
  window.era5MultiselectLabels.observe(document.body, {subtree: true, childList: true, characterData: true});
})();
