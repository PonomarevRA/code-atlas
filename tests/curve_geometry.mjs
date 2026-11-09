/** Независимая проверка SVG-кривых для регрессий разных renderer. */
import assert from 'node:assert/strict';

/** Независимо семплирует SVG M/L/Q/C через многочлены Бернштейна. */
export function samplePath(path) {
  assert.ok(path, 'Each retained edge has a visible route');
  const samples = [];
  let from;
  for (const command of path.match(/[MLQC][^MLQC]*/g)) {
    const values = command.slice(1).match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi).map(Number);
    const points = [];
    if (from) points.push(from);
    for (let index = 0; index < values.length; index += 2) points.push({ x: values[index], y: values[index + 1] });
    if (command[0] === 'M') samples.push(points.at(-1));
    else {
      for (let step = 1; step <= 60; step++) {
        const t = step / 60, u = 1 - t;
        const weights = command[0] === 'C' ? [u ** 3, 3 * u * u * t, 3 * u * t * t, t ** 3]
          : command[0] === 'Q' ? [u * u, 2 * u * t, t * t] : [u, t];
        const sample = { x: 0, y: 0 };
        for (let index = 0; index < points.length; index++) {
          sample.x += points[index].x * weights[index]; sample.y += points[index].y * weights[index];
        }
        const previous = samples.at(-1);
        if (Math.hypot(sample.x - previous.x, sample.y - previous.y) > 1e-7) samples.push(sample);
      }
    }
    from = points.at(-1);
  }
  return samples;
}

/** Проверяет пересечение несоседних сегментов и попадание точек внутрь любого заголовка. */
export function assertClear(path, cards) {
  assert.ok(!/[LQ]/.test(path), 'Visible Flow connections remain smooth cubic curves');
  const points = samplePath(path);
  const turn = (a, b, c) => (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
  for (const point of points) {
    for (const card of cards) {
      const inside = point.x > card.x - card.w / 2 + 1e-6 && point.x < card.x + card.w / 2 - 1e-6
        && point.y > card.y - card.h / 2 + 1e-6 && point.y < card.y + card.h / 2 - 1e-6;
      assert.equal(inside, false, `Route crosses card ${card.id}`);
    }
  }
  for (let first = 1; first < points.length; first++) {
    for (let second = first + 2; second < points.length; second++) {
      const a = points[first - 1], b = points[first], c = points[second - 1], d = points[second];
      const crosses = turn(a, b, c) * turn(a, b, d) < -1e-8 && turn(c, d, a) * turn(c, d, b) < -1e-8;
      assert.equal(crosses, false, 'Route intersects itself');
    }
  }
  return points;
}
