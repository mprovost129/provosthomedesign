/* Geometry and reversible edits use the existing normalized annotation schema. */
const copy = value => structuredClone(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const round = value => Math.round(value * 10000) / 10000;
const clamp = value => Math.max(0, Math.min(1, value));

export function validMark(mark) {
  return !mark.points || Math.max(...mark.points.map(point =>
    Math.abs(point[0] - mark.x) + Math.abs(point[1] - mark.y))) >= .001;
}

export function adjustMark(original, dx, dy, handle = '') {
  const mark = copy(original);
  const points = mark.points || [[mark.x, mark.y]];
  if (handle) {
    const index = Number(handle);
    points[index] = [round(clamp(points[index][0] + dx)), round(clamp(points[index][1] + dy))];
  } else {
    dx = Math.max(-Math.min(...points.map(p => p[0])), Math.min(dx, 1 - Math.max(...points.map(p => p[0]))));
    dy = Math.max(-Math.min(...points.map(p => p[1])), Math.min(dy, 1 - Math.max(...points.map(p => p[1]))));
    points.forEach((p, index) => { points[index] = [round(p[0] + dx), round(p[1] + dy)]; });
  }
  [mark.x, mark.y] = points[0];
  return mark;
}

// Store only changed marks, including their positions, rather than whole drafts.
// Geometry, optimistic save versions and selection are never rolled back.
export class EditHistory {
  undo = [];
  redo = [];
  record(before, after) {
    const ids = new Set([...before.annotations, ...after.annotations].map(mark => mark.id));
    const changes = [];
    for (const id of ids) {
      const a = before.annotations.findIndex(mark => mark.id === id);
      const b = after.annotations.findIndex(mark => mark.id === id);
      if (!equal(before.annotations[a], after.annotations[b])) changes.push({
        before: a < 0 ? null : {index: a, mark: copy(before.annotations[a])},
        after: b < 0 ? null : {index: b, mark: copy(after.annotations[b])}, id,
      });
    }
    const summary = before.summary === after.summary ? null : {before: before.summary, after: after.summary};
    if (!changes.length && !summary) return false;
    this.undo.push({changes, summary}); this.redo = [];
    return true;
  }
  apply(draft, direction) {
    const source = direction === 'undo' ? this.undo : this.redo;
    const command = source.pop();
    if (!command) return null;
    const result = copy(draft), side = direction === 'undo' ? 'before' : 'after';
    result.annotations = result.annotations.filter(mark => !command.changes.some(change => change.id === mark.id));
    command.changes.map(change => change[side]).filter(Boolean).sort((a, b) => a.index - b.index)
      .forEach(entry => result.annotations.splice(entry.index, 0, copy(entry.mark)));
    if (command.summary) result.summary = command.summary[side];
    (direction === 'undo' ? this.redo : this.undo).push(command);
    return result;
  }
}
