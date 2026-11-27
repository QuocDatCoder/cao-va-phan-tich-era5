(() => {
  if (window.era5EraLauncherLinesActive) return;
  window.era5EraLauncherLinesActive = true;

  const lines = {
    home: [
      "Bạn muốn bắt đầu từ đâu? Mình giúp nhé.",
      "Mình có thể hướng dẫn bạn lấy dữ liệu ERA5.",
    ],
    collect: [
      "Bạn muốn lấy dữ liệu ở đâu? Mình giúp nhé.",
      "Bạn chọn địa điểm, mình giúp chuẩn bị yêu cầu.",
      "Mình có thể giúp chọn khoảng ngày cần lấy.",
    ],
    library: [
      "Bạn nhớ tên tỉnh thôi cũng được, mình tìm giúp.",
      "Bạn muốn xem lại bộ nào? Mình tìm cùng nhé.",
      "Mình có thể giúp bạn kiểm tra bộ nào đủ dữ liệu.",
    ],
    data: [
      "Bạn muốn xem ngày nào mưa nhiều nhất không?",
      "Bạn muốn biết giờ nào nóng nhất? Mình xem cùng nhé.",
      "Mình có thể giúp bạn kiểm tra những giờ còn thiếu.",
    ],
    charts: [
      "Bạn muốn vẽ bộ nào? Mình mở giúp nhé.",
      "Mình giúp bạn chọn biểu đồ phù hợp nhé.",
      "Bạn muốn xem theo ngày hay theo tháng?",
    ],
    chart: [
      "Có đoạn nào bạn thấy lạ không? Mình xem cùng nhé.",
      "Bạn muốn biết đỉnh biểu đồ rơi vào ngày nào không?",
      "Mình có thể giúp bạn đọc xu hướng của biểu đồ.",
    ],
  };
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const editable = "input:not([type=checkbox]):not([type=radio]), textarea, [contenteditable=true], [role=spinbutton], [role=combobox]";
  const busy = [
    ".st-key-ai_chat_shell", ".era-notice", ".era5-shell[data-drawer=true]",
    "[data-testid=stDateInputCalendar]", "[role=listbox]", "[role=dialog]",
    '[class*="st-key-stored_filters_"] details[open]',
    '[class*="st-key-chart_filters_"] details[open]',
  ].join(",");
  let page = null;
  let queue = [];
  let greeted = false;
  let nextLineAt = 0;
  let hideAt = 0;

  function currentPage() {
    if (document.querySelector(".st-key-chart_collection_detail")) return "chart";
    if (document.querySelector(".st-key-stored_collection_detail")) return "data";
    const active = document.querySelector('.era5-shell [aria-current="page"]');
    return { "Trang chủ": "home", "Thu thập": "collect", "Kho dữ liệu": "library", "Biểu đồ": "charts" }[active?.getAttribute("aria-label")];
  }

  setInterval(() => {
    const bubble = document.querySelector(".pet-launch-lines span");
    if (!bubble) return;
    const route = currentPage();
    const now = performance.now();
    if (route !== page) {
      page = route;
      queue = [...(lines[page] ?? [])];
      if (!greeted && queue.length) queue.unshift("Chào bạn, mình là ERA ☁️");
      queue.length = Math.min(queue.length, 3);
      nextLineAt = now + 1000;
      bubble.textContent = "";
    }

    const paused = document.hidden || reducedMotion.matches || document.activeElement?.matches(editable)
      || document.querySelector(busy);
    if (paused || now >= hideAt) {
      if (bubble.textContent) bubble.textContent = "";
    }
    if (paused || now < nextLineAt || !queue.length) return;

    bubble.textContent = queue.shift();
    greeted = true;
    hideAt = now + 4500;
    nextLineAt = now + 10000;
  }, 200);
})();
