export function initialSequence(steps, edited) {
  return (steps.find(step => step.sequence === edited) || steps.find(step => step.failure_hint) ||
    steps.find(step => !['ok', 'unconfirmed'].includes(step.status)) || steps.at(-1))?.sequence;
}

export function runKind(view) {
  if (!view.run.parent_id) return 'Recorded run';
  return view.run.override_applied || view.steps.some(step => step.sequence === view.run.fork_step && (step.origin === 'overridden' || step.status === 'unconfirmed')) ||
    view.run.status === 'override_not_applied' || view.run.provider === 'claude' ? 'Response test' :
    view.run.status === 'replayed' ? 'Unchanged replay' : 'Replay attempt';
}

export function runGroups(runs) {
  const newestFirst = [...runs].reverse();
  const ids = new Set(runs.map(run => run.id));
  const children = new Map();
  for (const run of newestFirst) {
    if (!children.has(run.parent_id)) children.set(run.parent_id, []);
    children.get(run.parent_id).push(run);
  }
  const groups = [];
  const seen = new Set();
  const roots = newestFirst.filter(run => !ids.has(run.parent_id));
  for (const root of [...roots, ...newestFirst]) {
    if (seen.has(root.id)) continue;
    const group = [];
    const pending = [root];
    while (pending.length) {
      const run = pending.pop();
      if (seen.has(run.id)) continue;
      seen.add(run.id);
      group.push(run);
      pending.push(...[...(children.get(run.id) || [])].reverse());
    }
    groups.push(group);
  }
  return groups;
}

export function valuePreview(value, limit = 500) {
  if (value === undefined) return 'Unavailable';
  const text = JSON.stringify(value, null, 2);
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

export function comparisonPresentation(comparison, recorded, test) {
  const moments = new Map();
  const add = (sequence, title, description) => {
    if (!sequence) return;
    if (!moments.has(sequence)) moments.set(sequence, { sequence, titles: [], descriptions: [] });
    const moment = moments.get(sequence);
    moment.titles.push(title);
    moment.descriptions.push(description);
  };
  const kind = runKind(test);
  const unconfirmed = comparison.outcome === 'override_not_applied' ||
    (kind === 'Response test' && test.run.provider !== 'example' && !comparison.override_applied);
  const stopped = test.steps.find(step => step.status === 'blocked');
  if (kind === 'Response test') add(comparison.fork_step, unconfirmed ? 'Proposed response' : 'Response edited',
    unconfirmed ? 'Its use by the model was not confirmed.' : 'A replacement response was supplied at this step.');
  const first = comparison.first_change;
  if (first && stopped?.operation === 'system' && stopped.sequence === first.sequence) add(first.sequence, 'Replay diagnostic',
    'This entry records an execution problem, not a changed model or tool request.');
  else if (first) add(first.sequence, 'First observed difference', first.kind === 'length' ? 'One run has no corresponding step.' :
    `The first saved ${first.kind} difference is here.${first.sequence < comparison.fork_step && kind === 'Response test' ? ' It precedes the edit; this observation cannot be attributed to the replacement response.' : ''}`);
  if (comparison.first_changed_request) add(comparison.first_changed_request, 'First changed request',
    'This request differs from the recording.');
  if (stopped) add(stopped.sequence, 'Execution stopped', stopped.operation === 'system' ?
    'Replay could not continue; inspect the recorded diagnostic.' : 'No model or tool response was supplied here.');
  const canAnswer = ['complete', 'replayed', 'live_complete', 'wrong_answer'].includes(comparison.outcome);
  const answer = comparison.answer_comparison?.fork;
  return {
    kind,
    recordedAnswer: comparison.answer_comparison?.original ?? recorded.final_answer,
    testAnswer: canAnswer ? answer : undefined,
    answerUnavailable: comparison.outcome === 'blocked' ? 'No new answer · run stopped' : unconfirmed ?
      'Unavailable · response use not confirmed' : canAnswer ? 'Unavailable · not captured' : 'No confirmed final answer',
    stopped,
    moments: [...moments.values()]
  };
}
