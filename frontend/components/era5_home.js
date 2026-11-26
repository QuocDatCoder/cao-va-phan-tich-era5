(() => {
  if (window.era5PosterBound) return;
  window.era5PosterBound = true;
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const posters = new Map();
  const duration = 30000;
  let layer;
  let canvas;
  let posterPixels;
  let tap;
  let lastFrame = 0;
  let activePoster;
  let dark = false;

  function buffer(width, height) {
    const surface = document.createElement("canvas");
    surface.width = width;
    surface.height = height;
    return surface;
  }

  function feather(ctx, x, y, radius) {
    const edge = ctx.createRadialGradient(x, y, radius * .36, x, y, radius);
    edge.addColorStop(0, "#000");
    edge.addColorStop(1, "transparent");
    ctx.fillStyle = edge;
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  }

  function prepare() {
    const current = document.querySelector(".era5-home-clouds");
    if (!current) return false;
    const theme = document.querySelector(".era5-shell")?.dataset.theme === "dark" ? "night" : "day";
    if (!posters.has(theme)) {
      const image = new Image();
      image.src = getComputedStyle(current).getPropertyValue("--poster-" + theme).trim().slice(5, -2);
      posters.set(theme, image);
    }
    const poster = posters.get(theme);
    if (!poster.naturalWidth) {
      canvas?.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
      return false;
    }
    const width = Math.min(1440, Math.round(current.clientWidth));
    const height = Math.round(current.clientHeight * width / current.clientWidth);
    if (!width || !height) return false;
    if (current === layer && activePoster === poster && canvas.width === width && canvas.height === height) return true;
    layer = current;
    activePoster = poster;
    dark = theme === "night";
    canvas = layer.querySelector("canvas");
    canvas.width = width;
    canvas.height = height;
    canvas.dataset.poster = theme;
    delete canvas.dataset.effect;
    posterPixels = buffer(width, height);
    posterPixels.getContext("2d", {willReadFrequently: true}).drawImage(poster, 0, 0, width, height);
    tap = null;
    return true;
  }

  function animate(now) {
    requestAnimationFrame(animate);
    if (motion.matches || document.hidden || now - lastFrame < 34 || !prepare()) return;
    lastFrame = now;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (!tap) return;

    const elapsed = now - tap.start;
    if (elapsed >= duration) {
      tap = null;
      delete canvas.dataset.effect;
      return;
    }
    const opening = Math.min(1, elapsed / 1200);
    const closing = Math.max(0, (elapsed - 1500) / (duration - 1500));
    const strength = (1 - (1 - opening) ** 3) * (1 - closing * closing * (3 - 2 * closing));
    ctx.save();
    ctx.globalAlpha = strength;
    ctx.drawImage(tap.clear, tap.x, tap.y);
    ctx.restore();
  }
  requestAnimationFrame(animate);

  document.addEventListener("click", event => {
    const hero = event.target.closest(".st-key-era5_home_hero");
    if (!hero || motion.matches || event.target.closest("button, a") || !prepare()) return;
    const bounds = canvas.getBoundingClientRect();
    const x = Math.round((event.clientX - bounds.left) * canvas.width / bounds.width);
    const y = Math.round((event.clientY - bounds.top) * canvas.height / bounds.height);
    if (x < canvas.width * .53 || x >= canvas.width || y < 0 || y >= canvas.height) return;
    const original = posterPixels.getContext("2d");
    const [red, green, blue] = original.getImageData(x, y, 1, 1).data;
    if (red < (dark ? 49 : 86) || Math.max(red, green, blue) - Math.min(red, green, blue) > 86 ||
        red > blue * 1.18) return;

    const radius = Math.max(24, Math.round(canvas.height * .12));
    const size = radius * 2;
    const clear = buffer(size, size);
    const context = clear.getContext("2d");
    const patch = original.getImageData(x - radius, y - radius, size, size);
    const colors = patch.data;
    for (let i = 0; i < colors.length; i += 4) {
      const white = Math.max(0, Math.min(1, (Math.min(colors[i], colors[i+1], colors[i+2]) - 55) / 135));
      const softness = white * .34;
      colors[i] *= 1 - softness * .85;
      colors[i+1] = colors[i+1] * (1 - softness) + softness * (dark ? 28 : 59);
      colors[i+2] = colors[i+2] * (1 - softness) + softness * (dark ? 54 : 92);
    }
    context.putImageData(patch, 0, 0);
    context.globalCompositeOperation = "destination-in";
    feather(context, radius, radius, radius);
    tap = {clear, x: x - radius, y: y - radius, start: performance.now()};
    canvas.dataset.effect = "dissolve";
  });
})();
