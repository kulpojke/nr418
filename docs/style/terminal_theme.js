(function () {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  if (reduceMotion.matches) {
    return;
  }

  const leftCount = 61;
  const rightCount = 62;
  const normalBoundary = 106;
  const excursionBoundary = normalBoundary * 1.5;
  const excursionProbability = 0.015;
  const attractionPasses = 2;
  const attractionRadius = 18;
  const attractionStrength = 0.035;
  const sizes = [7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 22];
  const colors = ["#7ddfff", "#ff4fd8", "#bdefff", "#7ddfff", "#ff4fd8"];
  const starSvg = [
    '<svg viewBox="0 0 100 100" aria-hidden="true" focusable="false">',
    '<polygon points="50,3 61,36 96,36 68,56 79,90 50,70 21,90 32,56 4,36 39,36"></polygon>',
    '</svg>',
  ].join("");
  const witchSvg = [
    '<svg viewBox="0 0 150 90" aria-hidden="true" focusable="false">',
    '<path d="M12 66 C43 55 91 55 138 68"></path>',
    '<path d="M103 65 L142 78"></path>',
    '<path d="M118 69 L145 61"></path>',
    '<path d="M121 71 L148 69"></path>',
    '<path d="M124 73 L146 78"></path>',
    '<path d="M66 38 L53 65 L92 65 L80 38"></path>',
    '<circle cx="73" cy="34" r="7"></circle>',
    '<path d="M46 31 L103 31"></path>',
    '<path d="M58 30 L78 5 L94 31"></path>',
    '<path d="M83 44 C94 42 103 45 110 52"></path>',
    '<path d="M56 45 C48 48 42 53 38 60"></path>',
    '</svg>',
  ].join("");
  let witchMode = false;
  const leftClusters = [
    [16, 9, 9, 3.5, 7],
    [64, 18, 18, 4.8, 8],
    [24, 31, 12, 5.4, 7],
    [82, 45, 17, 6.8, 9],
    [36, 58, 15, 5.0, 8],
    [92, 73, 11, 4.6, 7],
    [20, 87, 13, 5.8, 8],
  ];
  const rightClusters = [
    [18, 7, 10, 4.0, 7],
    [78, 20, 16, 5.2, 8],
    [34, 35, 13, 5.0, 8],
    [92, 48, 12, 7.0, 8],
    [48, 62, 16, 4.8, 8],
    [82, 79, 14, 5.6, 8],
    [24, 91, 12, 4.8, 7],
  ];

  function mulberry32(seed) {
    return function () {
      let t = seed += 0x6d2b79f5;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function gaussian(rand) {
    const u = Math.max(rand(), Number.EPSILON);
    const v = Math.max(rand(), Number.EPSILON);
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function choose(rand, items) {
    return items[Math.floor(rand() * items.length)];
  }

  function edgeDistance(value, rand) {
    if (rand() < excursionProbability) {
      const kick = Math.pow(rand(), 0.65);
      return normalBoundary + kick * (excursionBoundary - normalBoundary);
    }

    return clamp(value, 3, normalBoundary);
  }

  function applyAttraction(points) {
    for (let pass = 0; pass < attractionPasses; pass += 1) {
      const nudges = points.map(() => ({ x: 0, y: 0 }));

      for (let i = 0; i < points.length; i += 1) {
        for (let j = i + 1; j < points.length; j += 1) {
          const dx = points[j].x - points[i].x;
          const dy = points[j].y - points[i].y;
          const distance = Math.hypot(dx, dy);

          if (distance <= 0.01 || distance > attractionRadius) {
            continue;
          }

          const pull = (1 - distance / attractionRadius) * attractionStrength;
          nudges[i].x += dx * pull;
          nudges[i].y += dy * pull;
          nudges[j].x -= dx * pull;
          nudges[j].y -= dy * pull;
        }
      }

      points.forEach((point, index) => {
        point.x = clamp(point.x + nudges[index].x, 3, excursionBoundary);
        point.y = clamp(point.y + nudges[index].y, 3, 97);
      });
    }
  }

  function generateSide(seed, side, clusters, targetCount) {
    const rand = mulberry32(seed);
    const points = [];

    clusters.forEach(([cx, cy, sx, sy, n], index) => {
      const driftX = gaussian(rand) * 8;
      const driftY = gaussian(rand) * 2.5;
      const spread = 0.75 + rand() * 0.85;

      for (let i = 0; i < n; i += 1) {
        points.push({
          x: edgeDistance(cx + driftX + gaussian(rand) * sx * spread, rand),
          y: clamp(cy + driftY + gaussian(rand) * sy * spread, 3, 97),
          size: choose(rand, sizes),
          order: index + rand(),
        });
      }
    });

    while (points.length < targetCount) {
      points.push({
        x: edgeDistance(4 + rand() * 100, rand),
        y: clamp(3 + rand() * 94, 3, 97),
        size: choose(rand, [7, 8, 9, 10, 12, 14, 17]),
        order: rand() * clusters.length,
      });
    }

    applyAttraction(points);
    points.sort((a, b) => a.y + a.order * 0.7 - (b.y + b.order * 0.7));

    return points;
  }

  function makeStar(rand) {
    const star = document.createElement("span");
    const duration = 0.8 + rand() * 2.8;
    const glow = 2 + rand() * 9;

    star.className = "terminal-star";
    star.innerHTML = starSvg;
    star.style.setProperty("--star-color", choose(rand, colors));
    star.style.setProperty("--star-duration", `${duration.toFixed(2)}s`);
    star.style.setProperty("--star-delay", `${(-rand() * duration).toFixed(2)}s`);
    star.style.setProperty("--star-glow-soft", `${(glow * 0.55).toFixed(1)}px`);
    star.style.setProperty("--star-glow-mid", `${(glow * 0.8).toFixed(1)}px`);
    star.style.setProperty("--star-glow", `${glow.toFixed(1)}px`);
    star.style.setProperty("--star-rot", `${Math.floor(rand() * 360)}deg`);

    return star;
  }

  function scrollProgress() {
    const scrollable = document.documentElement.scrollHeight - window.innerHeight;
    return scrollable <= 0 ? 0 : window.scrollY / scrollable;
  }

  function setWitchMode(enabled, stars) {
    if (enabled === witchMode) {
      return;
    }

    witchMode = enabled;
    document.documentElement.classList.toggle("terminal-witch-mode", enabled);
    stars.forEach((star) => {
      star.innerHTML = enabled ? witchSvg : starSvg;
    });
  }

  function makeStars(field, count, seed) {
    const rand = mulberry32(seed);
    const stars = [];

    for (let i = 0; i < count; i += 1) {
      const star = makeStar(rand);
      field.appendChild(star);
      stars.push(star);
    }

    return stars;
  }

  function applyPoints(stars, points, side) {
    const viewportWidth = window.innerWidth;
    const viewportHeight = window.innerHeight;

    stars.forEach((star, index) => {
      const point = points[index % points.length];
      const x = side === "left" ? point.x : viewportWidth - point.x;
      const y = viewportHeight * (point.y / 100);

      star.style.setProperty("--star-x", `${x.toFixed(1)}px`);
      star.style.setProperty("--star-y", `${y.toFixed(1)}px`);
      star.style.setProperty("--star-size", `${point.size}px`);
    });
  }

  const field = document.createElement("div");
  field.className = "terminal-star-field";
  field.setAttribute("aria-hidden", "true");
  document.body.appendChild(field);
  document.documentElement.classList.add("terminal-stars-active");

  const leftStars = makeStars(field, leftCount, 2180);
  const rightStars = makeStars(field, rightCount, 42180);
  const allStars = leftStars.concat(rightStars);

  function recluster() {
    const scrollSeed = Math.floor(window.scrollY / 140);
    const left = generateSide(218 + scrollSeed * 17, "left", leftClusters, leftCount);
    const right = generateSide(4218 + scrollSeed * 23, "right", rightClusters, rightCount);

    setWitchMode(scrollProgress() >= 0.5, allStars);
    applyPoints(leftStars, left, "left");
    applyPoints(rightStars, right, "right");
  }

  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) {
      return;
    }

    ticking = true;
    window.requestAnimationFrame(() => {
      recluster();
      ticking = false;
    });
  }, { passive: true });

  window.addEventListener("resize", recluster, { passive: true });

  recluster();
}());
