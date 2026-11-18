const missing = Symbol('missing');

function preview(value) {
  if (value === missing) return 'Missing';
  const text = JSON.stringify(value);
  return text.length > 120 ? `${text.slice(0, 119)}…` : text;
}

export function changedFields(recorded, test) {
  const changes = [];
  const pending = [{ path: '$', recorded, test }];
  while (pending.length) {
    const item = pending.pop();
    if (Object.is(item.recorded, item.test)) continue;
    const pair = [item.recorded, item.test];
    const bothObjects = pair.every(value => value !== missing && value !== null && typeof value === 'object');
    if (bothObjects && Array.isArray(item.recorded) === Array.isArray(item.test)) {
      const keys = Array.isArray(item.recorded)
        ? Array.from({ length: Math.max(item.recorded.length, item.test.length) }, (_, index) => index)
        : [...new Set([...Object.keys(item.recorded), ...Object.keys(item.test)])].sort();
      for (const key of keys.reverse()) {
        const path = Array.isArray(item.recorded) ? `${item.path}[${key}]` : `${item.path}.${key}`;
        pending.push({ path, recorded: Object.hasOwn(item.recorded, key) ? item.recorded[key] : missing,
          test: Object.hasOwn(item.test, key) ? item.test[key] : missing });
      }
      continue;
    }
    changes.push({ path: item.path, recorded: preview(item.recorded), test: preview(item.test) });
    if (changes.length > 5) return { changes: changes.slice(0, 5), more: true };
  }
  return { changes, more: false };
}
