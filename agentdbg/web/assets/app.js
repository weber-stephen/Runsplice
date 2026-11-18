import { changedFields } from './response_diff.mjs';
import { initialSequence, runKind, runGroups, valuePreview, comparisonPresentation } from './inspection.mjs';

const token = new URLSearchParams(location.search).get('token');
const state = { runs: [], base: null, fork: null, comparison: null, selected: null, activeRunId: null, pairRequest: 0, stepRequest: 0, playing: false, playedToEnd: false, playbackTimer: null, playbackGeneration: 0 };
const $ = id => document.getElementById(id);

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function loadingTimeline() {
  const loading = element('div', 'timeline-loading');
  loading.setAttribute('role', 'status');
  loading.setAttribute('aria-label', 'Loading run timeline');
  for (let index = 0; index < 3; index++) loading.append(element('div', 'loading-step'));
  return loading;
}

function label(application) {
  return application === 'example' ? 'Invoice' : application[0].toUpperCase() + application.slice(1);
}

const runNames = { example: 'Invoice check', triage: 'Support triage', delivery: 'Delivery check', openai: 'OpenAI SDK session', claude: 'Claude SDK session' };
const expectedAnswers = { example: 'paid', triage: 'urgent', delivery: 'delivered' };
const stepNames = { lookup_customer: 'Look up customer', search_invoice: 'Check invoice status', read_policy: 'Read answer rule', get_ticket: 'Read support ticket', get_playbook: 'Choose response guide', track_package: 'Check package status' };
const runStatuses = { complete: 'Completed', wrong_answer: 'Answer needs review', blocked: 'Stopped before answering', replayed: 'Re-run completed', replayed_error: 'Re-run failed', live_complete: 'Live branch completed', override_not_applied: 'Response change not confirmed', error: 'Failed', running: 'Running' };

function runName(run) { return runNames[run.application] || `${label(run.application)} run`; }
function runStatus(run) { return runStatuses[run.status] || run.status.replaceAll('_', ' '); }
function stepName(step) { return step.operation === 'system' ? 'Replay diagnostic' : step.status === 'blocked' ? 'Stopped before answer' : step.operation === 'model' ? 'Model response' : stepNames[step.name] || (step.name || step.operation).replaceAll('_', ' '); }
function stepState(step) { return step.status === 'blocked' ? 'Stopped' : step.status === 'unconfirmed' ? 'Not confirmed' : step.status !== 'ok' ? 'Failed' : step.origin === 'overridden' ? 'Changed response' : step.origin === 'live' ? 'Live call' : 'Saved'; }
function runTime(run) { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(run.created.replace(' ', 'T') + 'Z')); }
function quoted(value) { return ['string', 'number', 'boolean'].includes(typeof value) ? `“${value}”` : null; }

function comparisonMoment(sequence, title, description) {
  const row = element('div', 'moment');
  row.append(element('strong', '', title), element('span', '', description));
  if (![...state.base.steps, ...state.fork.steps].some(step => step.sequence === sequence)) return row;
  const button = element('button', '', `Inspect step ${sequence}`);
  button.type = 'button';
  button.addEventListener('click', () => {
    const run = state.fork.steps.some(step => step.sequence === sequence) ? state.fork : state.base;
    if (run.steps.some(step => step.sequence === sequence)) selectStep(run.run.id, sequence).then(() => $('inspector-title').focus());
  });
  row.append(button);
  return row;
}

function renderComparisonOverview() {
  const result = $('comparison-result');
  const moments = $('key-moments');
  result.replaceChildren();
  moments.replaceChildren();
  if (!state.comparison) return;
  const presentation = comparisonPresentation(state.comparison, state.base, state.fork);
  const answerGrid = element('div', 'answer-grid');
  for (const [title, value, unavailable] of [
    ['Recorded answer', presentation.recordedAnswer, 'Unavailable · not captured'],
    [presentation.kind === 'Unchanged replay' ? 'Replay answer' : 'Test answer', presentation.testAnswer, presentation.answerUnavailable]
  ]) {
    const card = element('div', 'answer-card');
    card.append(element('span', 'answer-label', title), element('strong', '', value == null ? unavailable : valuePreview(value, 240)));
    answerGrid.append(card);
  }
  const outcome = element('p', 'comparison-outcome', `${presentation.kind}: ${runStatus(state.fork.run)}.`);
  if (presentation.testAnswer != null && presentation.recordedAnswer != null) {
    outcome.append(document.createTextNode(JSON.stringify(presentation.testAnswer) === JSON.stringify(presentation.recordedAnswer) ? ' The answer stayed the same.' : ' The answer changed.'));
  }
  if (presentation.stopped) outcome.append(document.createTextNode(presentation.stopped.operation === 'system' ?
    ' Replay could not continue. Inspect the recorded diagnostic.' :
    state.comparison.first_changed_request ? ' A changed request cannot use a saved response. No response was supplied at the stopping point.' : ' No response was supplied at the stopping point. Inspect its recorded diagnostic.'));
  result.append(element('h3', '', 'Outcome'), answerGrid, outcome);
  if (state.fork.run.provider !== 'example') result.append(element('p', 'capture-note',
    `${state.fork.run.provider === 'claude' ? 'Live Claude branch' : 'OpenAI SDK rerun'} · Response use ${state.fork.run.override_applied ? 'confirmed' : 'not confirmed'} · ${state.fork.run.live_calls} live calls. Live results are observations, not deterministic guarantees.`));
  if ([state.base, state.fork].some(view => !view.run.replayable)) result.append(element('p', 'capture-note',
    'Capture is incomplete or limited. Missing data cannot establish that these runs were unchanged.'));
  moments.append(element('h3', '', 'Key steps'));
  for (const moment of presentation.moments) moments.append(comparisonMoment(moment.sequence,
    `Step ${moment.sequence} · ${moment.titles.join(' · ')}`, moment.descriptions.join(' ')));
  if (!state.comparison.first_change) moments.append(element('p', 'muted', 'No difference was found in the available captures.'));
}

function renderRunDetails() {
  const content = $('run-details-content');
  content.replaceChildren();
  for (const [name, run] of [['Recorded run', state.base.run], ['Test run', state.fork?.run]]) {
    if (!run) continue;
    const row = element('div', 'run-detail-row');
    const copy = element('button', 'copy-id', 'Copy ID');
    copy.type = 'button';
    copy.addEventListener('click', () => navigator.clipboard?.writeText ? navigator.clipboard.writeText(run.id).then(() => status('Run ID copied.')).catch(() => status('Select the ID to copy it.', true)) : status('Select the ID to copy it.', true));
    row.append(element('span', '', name), element('code', '', run.id), copy);
    content.append(row);
  }
  content.append(element('p', 'muted', `${state.base.run.provider} · ${state.base.run.capture_content ? 'Step details saved' : 'Step details not saved'} · ${runTime(state.base.run)}`));
  if (state.fork && state.fork.run.provider !== 'example') content.append(element('p', 'muted', `Response change ${state.fork.run.override_applied ? 'applied' : 'not confirmed'} · ${state.fork.run.live_calls} live calls`));
  $('run-details').hidden = false;
}

function outcomeSummary(finalDetail) {
  const run = state.base.run;
  const answer = quoted(state.base.final_answer ?? finalDetail?.response?.answer);
  const expected = quoted(expectedAnswers[run.application]);
  let summary = run.status === 'wrong_answer' && answer && expected
    ? `The agent answered ${answer}; this example expected ${expected}.`
    : answer ? `The agent answered ${answer}. ${runStatus(run)}.` : `${runStatus(run)}. ${run.capture_content ? 'Select a step to see what happened.' : 'Step details were not saved.'}`;
  if (state.fork) summary += state.fork.run.status === 'blocked' ? ' The test run stopped before answering.' : ` ${runStatus(state.fork.run)}.`;
  return summary;
}

const exampleQuestions = {
  example: ['Why did the invoice answer say unpaid?', 'Inspect the stale lookup, substitute paid, and find the changed request where replay stops.'],
  triage: ['Why did this ticket get routine handling?', 'Change low priority to high and find where the next tool request diverges.'],
  delivery: ['Why did the answer say in transit?', 'Inspect the delivered tool result, replace the final model answer, and compare the completed result.']
};

function captureNotice(view, summary) {
  if (!view.run.capture_content) return 'Unavailable: requests, responses, and state were not captured.';
  return summary.limitation ? `Capture limited: ${summary.limitation.replaceAll('_', ' ')}. Some values are unavailable; replay is limited.` : null;
}

function payloadPreview(parent, title, value) {
  const section = element('section', 'evidence-section');
  section.append(element('h3', '', title), element('pre', 'payload-preview', valuePreview(value)));
  if (value !== undefined && JSON.stringify(value, null, 2).length > 500) section.append(element('p', 'muted', 'More in Raw data.'));
  parent.append(section);
}

function renderSimple(view, summary, detail) {
  const content = $('simple-content');
  content.replaceChildren();
  if (detail.failure_hint) content.append(element('p', 'hint', `Example diagnosis: ${detail.failure_hint}`));
  const limitation = captureNotice(view, summary);
  if (limitation) content.append(element('p', 'capture-note', limitation));
  if (summary.status === 'blocked') content.append(element('h3', 'evidence-alert', summary.operation === 'system' ? 'Replay could not continue' : 'Replay stopped before a response'));
  if (summary.status === 'unconfirmed') content.append(element('p', 'capture-note', 'The proposed response was not confirmed as received by the model.'));
  if (summary.origin === 'live') content.append(element('p', 'capture-note', 'This response came from a live SDK call in a separate branch.'));
  payloadPreview(content, 'Request', view.run.capture_content ? detail.request : undefined);
  payloadPreview(content, summary.status === 'blocked' ? 'Blocked diagnostic · not a model or tool response' : 'Response', view.run.capture_content ? detail.response : undefined);
  if (detail.step.error_type || detail.step.error_message) payloadPreview(content, 'Recorded error', { type: detail.step.error_type, message: detail.step.error_message });
  content.append(element('h3', 'change-heading', 'State changes'));
  if (detail.state_delta === null || !view.run.capture_content) content.append(element('p', 'muted', 'Unavailable · no complete before and after state.'));
  else {
    const entries = Object.entries(detail.state_delta);
    if (!entries.length) content.append(element('p', 'muted', 'No captured state changes.'));
    for (const [key, change] of entries.slice(0, 5)) {
      const row = element('div', 'change-row');
      row.append(element('strong', 'change-path', key), element('span', '', `Before: ${change.before_missing ? 'Missing' : valuePreview(change.before, 120)}`), element('span', '', `After: ${change.after_missing ? 'Missing' : valuePreview(change.after, 120)}`));
      content.append(row);
    }
    content.append(element('p', 'muted', 'Complete state changes in Raw data.'));
  }
  renderContext(content, detail.context, view, detail);
  $('playback-caption').textContent = `Step ${summary.sequence}: ${stepName(summary)}, ${stepState(summary)}.`;
}

function renderChangedFields(content, title, changes) {
  if (!changes.length) return;
  content.append(element('h3', 'change-heading', title));
  for (const change of changes) {
    const row = element('div', 'change-row');
    row.append(element('strong', 'change-path', change.path), element('span', 'change-before', `Recorded: ${change.recorded}`), element('span', 'change-after', `Test: ${change.test}`));
    content.append(row);
  }
}

function renderComparisonStep(sequence, original, test) {
  const content = $('simple-content');
  content.replaceChildren();
  if (!original || !test) {
    content.append(element('p', 'simple-copy', `${!original ? 'Recorded' : 'Test'} run has no step ${sequence}; the paths no longer align.`));
    const available = original || test;
    if (available) {
      payloadPreview(content, `${original ? 'Recorded' : 'Test'} request`, available.request);
      payloadPreview(content, available.step.status === 'blocked' ? 'Blocked diagnostic' : `${original ? 'Recorded' : 'Test'} response`, available.response);
    }
    return;
  }
  const blocked = test.step.status === 'blocked';
  const diverged = original.step.operation !== test.step.operation || original.request?.name !== test.request?.name;
  if (blocked) {
    content.append(element('h3', 'evidence-alert', test.step.operation === 'system' ? 'Replay could not continue' : 'Replay stopped before a response'));
    if (test.step.operation === 'system') {
      payloadPreview(content, 'Recorded diagnostic · not an agent request or response', test.response);
      content.append(element('p', 'simple-copy', 'This execution problem does not establish that the agent’s next request changed.'));
      return;
    }
  }
  const limitations = [captureNotice(state.base, original.step), captureNotice(state.fork, test.step)].filter(Boolean);
  if (limitations.length) {
    content.append(element('p', 'capture-note', [...new Set(limitations)].join(' ')));
  } else if (diverged) {
    content.append(element('p', 'simple-copy', 'The paths diverged: these are different operations. Their payloads are labeled separately in Raw data.'));
    payloadPreview(content, 'Recorded request', original.request);
    payloadPreview(content, 'Attempted request', test.request);
  } else {
    const response = blocked ? { changes: [], more: false } : changedFields(original.response, test.response);
    const request = changedFields(original.request, test.request);
    const responseRows = response.changes;
    const requestRows = request.changes.slice(0, 5 - responseRows.length);
    if (blocked && requestRows.length) content.querySelector('.evidence-alert').textContent = 'Request changed; replay stopped';
    renderChangedFields(content, 'Changed response', responseRows);
    renderChangedFields(content, 'Changed request · recorded → attempted', requestRows);
    if (!responseRows.length && !requestRows.length) content.append(element('p', 'simple-copy', blocked ?
      'No captured request difference is available here. Inspect the blocked diagnostic.' : 'No captured request or response difference at this step.'));
    if (response.more || request.more || request.changes.length > requestRows.length) content.append(element('p', 'muted', 'More in Raw data.'));
    if (blocked) content.append(element('p', 'simple-copy', 'No response was supplied. A saved response cannot be reused for a different request.'));
  }
  if (state.comparison.outcome === 'override_not_applied') content.append(element('p', 'capture-note', 'A captured difference does not confirm that the proposed response reached the model.'));
  if (blocked) detailSection(content, 'Blocked diagnostic · not a model or tool response', test.response);
  for (const [name, item, view] of [['Recorded', original, state.base], ['Test', test, state.fork]]) {
    if (item.step.operation === 'model') renderContext(content, item.context, view, item, `${name} context`);
  }
  $('playback-caption').textContent = `Step ${sequence}: ${blocked ? 'execution stopped' : 'comparison selected'}.`;
}

function setView(view) {
  $('simple-content').hidden = view !== 'simple';
  $('step-content').hidden = view !== 'technical';
  $('simple-view').setAttribute('aria-pressed', String(view === 'simple'));
  $('technical-view').setAttribute('aria-pressed', String(view === 'technical'));
}

function playbackView() {
  return state.fork && state.fork.run.id === state.selected?.runId ? state.fork : state.base;
}

function updatePlayback() {
  const view = playbackView();
  const count = view?.steps.length || 0;
  document.querySelector('.playback').hidden = !count;
  document.querySelector('.legend').hidden = !count;
  const position = view && state.selected?.runId === view.run.id ? state.selected.sequence : 0;
  $('play-button').disabled = !count;
  $('play-label').textContent = state.playing ? 'Pause' : state.playedToEnd ? 'Watch again' : 'Watch saved run';
  $('play-button').classList.toggle('is-playing', state.playing);
  $('previous-button').disabled = position <= 1;
  $('next-button').disabled = !count || position >= count;
  const isTestRun = count && view === state.fork;
  document.querySelector('.playback').dataset.run = isTestRun ? 'test' : 'recorded';
  $('playback-lane').textContent = count ? isTestRun ? 'Test run' : 'Recorded run' : 'Walkthrough';
  $('playback-label').textContent = count ? `Step ${position || 1} of ${count}` : 'Select a run to begin';
  $('playback-fill').style.transform = `scaleX(${count ? position / count : 0})`;
  document.querySelectorAll('.step-node').forEach(node => {
    const current = node.dataset.run === state.selected?.runId && Number(node.dataset.sequence) === position;
    node.classList.toggle('played', node.dataset.run === state.selected?.runId && Number(node.dataset.sequence) < position);
    if (current) node.setAttribute('aria-current', 'step');
    else node.removeAttribute('aria-current');
  });
  document.querySelectorAll('.lane').forEach(lane => {
    const active = lane.dataset.run === state.selected?.runId;
    lane.classList.toggle('active-lane', active);
    lane.querySelector('.lane-state').textContent = active ? 'Selected' : 'Compare';
  });
  document.querySelectorAll('.run-item').forEach(button => {
    const active = button.dataset.run === state.activeRunId;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });
}

function pausePlayback() {
  clearTimeout(state.playbackTimer);
  state.playbackTimer = null;
  state.playing = false;
  ++state.playbackGeneration;
  updatePlayback();
}

async function playStep(runId, sequence, generation) {
  if (!state.playing || generation !== state.playbackGeneration) return;
  await selectStep(runId, sequence, true);
  if (!state.playing || generation !== state.playbackGeneration || state.selected?.runId !== runId || state.selected.sequence !== sequence) return;
  const count = playbackView().steps.length;
  if (sequence === count) { state.playedToEnd = true; pausePlayback(); status('Walkthrough complete.'); return; }
  state.playbackTimer = setTimeout(() => playStep(runId, sequence + 1, generation), 2500);
}

function startPlayback(fromStart = false) {
  const view = playbackView();
  if (!view?.steps.length) return;
  pausePlayback();
  state.playedToEnd = false;
  const generation = state.playbackGeneration;
  state.playing = true;
  updatePlayback();
  const sequence = fromStart || state.selected?.sequence === view.steps.length ? 1 : state.selected?.sequence || 1;
  playStep(view.run.id, sequence, generation);
}

function status(message, error = false) {
  $('workspace-status').textContent = message;
  $('workspace-status').hidden = !message;
  $('workspace-status').classList.toggle('is-error', error);
  $('status').textContent = message;
  $('status').style.color = error ? '#a94741' : '';
  for (const dialog of [$('record-dialog'), $('fork-dialog')]) {
    dialog.querySelector('.dialog-error').textContent = error && dialog.open ? message : '';
  }
}

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Agentdbg-Token': token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: 'no-store'
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function fillExamples() {
  for (const id of ['example-filter', 'record-example']) {
    const select = $(id);
    select.replaceChildren();
    if (id === 'example-filter') select.add(new Option('All runs', 'all'));
    for (const example of Object.keys(state.examples)) select.add(new Option(label(example), example));
    if (id === 'example-filter') for (const provider of ['openai', 'claude']) select.add(new Option(runNames[provider], provider));
  }
}

function renderRuns() {
  const filter = $('example-filter').value;
  const runs = state.runs.filter(run => filter === 'all' || run.application === filter);
  $('run-count').textContent = String(runs.length);
  const list = $('run-list');
  list.replaceChildren();
  if (!runs.length) list.append(element('p', 'muted', 'No runs for this example yet.'));
  for (const family of runGroups(runs)) {
    const group = element('div', 'run-group');
    group.append(element('div', 'run-group-title', runName(family[0])));
    for (const item of family) {
    const button = element('button', 'run-item');
    button.type = 'button';
    button.dataset.run = item.id;
    button.title = `Run ID ${item.id}`;
    button.setAttribute('aria-label', `${item.kind || 'Recorded run'}, ${runStatus(item)}, ${runTime(item)}`);
    if (state.activeRunId === item.id) { button.classList.add('active'); button.setAttribute('aria-current', 'true'); }
    const top = element('span', 'run-top');
    top.append(element('span', '', item.parent_id ? `${item.kind || 'Saved result'} · step ${item.fork_step}` : 'Recorded run'));
    top.append(element('span', `run-status ${item.status === 'blocked' ? 'blocked' : ''}`, runStatus(item)));
    const meta = element('span', 'run-meta');
    meta.append(element('span', '', runTime(item)));
    button.append(top, meta);
    button.addEventListener('click', () => selectRun(item.id));
    group.append(button);
    }
    list.append(group);
  }
}

async function refreshRuns(preferredId) {
  state.runs = (await api('/api/runs')).runs;
  renderRuns();
  if (preferredId) await selectRun(preferredId);
  else if (!state.base && state.runs.length) await selectRun(state.runs.at(-1).id);
}

async function selectRun(runId) {
  status('');
  try {
    const selected = state.runs.find(run => run.id === runId);
    const baseId = selected.parent_id || selected.id;
    await loadPair(baseId, selected.parent_id ? selected.id : null);
    if (state.activeRunId === runId) $('timeline-title').scrollIntoView({ block: 'start', behavior: 'auto' });
  } catch (error) { status(error.message, true); }
}

async function loadPair(baseId, forkId) {
  pausePlayback();
  state.playedToEnd = false;
  const request = ++state.pairRequest;
  ++state.stepRequest;
  clearInspector();
  setView('simple');
  $('comparison-overview').hidden = true;
  $('recorded-outcome').hidden = true;
  $('timeline-toggle').hidden = true;
  $('timeline-panel').hidden = false;
  $('timeline').replaceChildren(loadingTimeline());
  $('fork-picker').disabled = true;
  const [base, fork, comparison] = await Promise.all([
    api(`/api/run?id=${encodeURIComponent(baseId)}`),
    forkId ? api(`/api/run?id=${encodeURIComponent(forkId)}`) : null,
    forkId ? api(`/api/compare?original=${encodeURIComponent(baseId)}&fork=${encodeURIComponent(forkId)}`) : null
  ]);
  if (request !== state.pairRequest) return;
  const last = base.steps.at(-1);
  const finalDetail = base.run.capture_content && last?.operation === 'model'
    ? await api(`/api/step?id=${encodeURIComponent(baseId)}&sequence=${last.sequence}`) : null;
  if (request !== state.pairRequest) return;
  state.base = base;
  state.fork = fork;
  state.comparison = comparison;
  state.activeRunId = forkId || baseId;
  const picker = $('fork-picker');
  picker.replaceChildren(new Option('No comparison', ''));
  state.runs.filter(run => run.parent_id === baseId).forEach((run, index) => picker.add(new Option(`${run.kind || 'Saved result'} ${index + 1} · step ${run.fork_step} · ${runStatus(run)}`, run.id)));
  picker.disabled = picker.options.length === 1;
  picker.value = forkId || '';
  $('timeline-title').textContent = runName(state.base.run);
  $('timeline-summary').textContent = state.fork ? `Recorded ${runTime(base.run)} · Test ${runTime(fork.run)}` : `Recorded ${runTime(base.run)} · ${runStatus(base.run)}`;
  renderRunDetails();
  renderRuns();
  renderTimeline();
  renderComparisonOverview();
  $('comparison-overview').hidden = !fork;
  $('recorded-outcome').hidden = !!fork;
  $('recorded-outcome').replaceChildren(element('h3', '', 'Outcome'), element('p', '', outcomeSummary(finalDetail)));
  const limited = [base, fork].filter(Boolean).filter(view => !view.run.replayable);
  if (limited.length) $('recorded-outcome').append(element('p', 'capture-note', base.run.capture_content ? 'Capture is limited; full evidence or replay may be unavailable.' : 'Requests, responses, and state were not captured.'));
  const selectedRun = fork?.run || base.run;
  $('execution-mode').textContent = selectedRun.provider === 'example' ? 'Offline example' : selectedRun.provider === 'claude' ? `Claude SDK · ${selectedRun.parent_id ? 'live branch' : 'recorded'}` : `OpenAI SDK · ${!selectedRun.parent_id ? 'recorded' : selectedRun.live_calls ? 'includes live calls' : 'saved responses'}`;
  $('timeline-toggle').hidden = !fork;
  $('timeline-toggle').setAttribute('aria-expanded', 'false');
  $('timeline-toggle').textContent = 'Show full timeline';
  $('timeline-panel').hidden = !!fork;
  const edited = state.fork?.steps.some(step => step.sequence === comparison?.fork_step) ? state.fork : state.base;
  const selected = edited?.steps.some(step => step.sequence === comparison?.fork_step) ? edited : state.fork?.steps.length ? state.fork : state.base;
  const initial = { runId: selected.run.id, sequence: initialSequence(selected.steps, comparison?.fork_step) };
  if (initial.sequence) await selectStep(initial.runId, initial.sequence);
  else { clearInspector(); $('recorded-outcome').append(element('p', 'muted', 'No steps were captured for this run.')); }
  updatePlayback();
}

function makeLane(view, title, maxSteps, isFork) {
  const row = element('div', 'lane');
  row.dataset.run = view.run.id;
  row.setAttribute('role', 'group');
  row.setAttribute('aria-label', title);
  row.style.setProperty('--steps', maxSteps);
  const heading = element('div', 'lane-label', title);
  heading.append(element('small', 'lane-state', 'Compare'));
  row.append(heading);
  for (let sequence = 1; sequence <= maxSteps; sequence++) {
    const cell = element('div', 'step-cell');
    const step = view.steps.find(item => item.sequence === sequence);
    if (isFork && sequence === state.comparison?.fork_step) {
      cell.classList.add('fork-cell');
      cell.append(element('span', 'branch-connector'), element('span', 'branch-label', `Fork at step ${sequence}`));
    }
    if (step) {
      const node = element('button', `step-node ${step.origin === 'overridden' ? 'overridden' : ''} ${step.origin === 'live' ? 'live' : ''} ${step.status === 'blocked' ? 'blocked' : ''}`);
      node.type = 'button';
      node.dataset.run = view.run.id;
      node.dataset.sequence = String(sequence);
      const firstChange = isFork && state.comparison?.first_change?.sequence === sequence;
      if (firstChange) node.classList.add('first-change');
      if (state.selected?.runId === view.run.id && state.selected.sequence === sequence) node.classList.add('selected');
      const titleText = stepName(step);
      node.setAttribute('aria-label', `${title} step ${sequence}: ${titleText}, ${stepState(step)}${firstChange ? `, first ${state.comparison.first_change.kind} change` : ''}`);
      node.append(element('span', 'node-marker', String(sequence)), element('div', 'node-kind', step.operation === 'model' ? 'Model' : step.operation === 'tool' ? 'Tool' : 'System'), element('div', 'node-title', titleText), element('div', 'node-foot', stepState(step)));
      if (firstChange) node.append(element('span', 'node-flag', `First ${state.comparison.first_change.kind} change`));
      node.addEventListener('click', () => selectStep(view.run.id, sequence));
      node.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
        event.preventDefault();
        const nodes = [...row.querySelectorAll('.step-node')];
        nodes[Math.max(0, Math.min(nodes.length - 1, nodes.indexOf(node) + (event.key === 'ArrowRight' ? 1 : -1)))].focus();
      });
      cell.append(node);
    }
    row.append(cell);
  }
  return row;
}

function renderTimeline() {
  const timeline = $('timeline');
  timeline.replaceChildren();
  const maxSteps = Math.max(state.base.steps.length, state.fork?.steps.length || 0, 1);
  timeline.append(makeLane(state.base, 'Recorded run', maxSteps, false));
  if (state.fork) timeline.append(makeLane(state.fork, 'Test run', maxSteps, true));
}

function detailSection(parent, title, value) {
  const section = element('details', 'detail-section');
  section.append(element('summary', 'detail-title', title));
  const pre = element('pre');
  pre.textContent = value === undefined ? 'Unavailable' : JSON.stringify(value, null, 2);
  section.append(pre);
  parent.append(section);
}

function renderContext(parent, context, view, detail, title = 'Context') {
  if (!context) return;
  const section = element('details', 'context-detail');
  section.append(element('summary', '', title));
  if (view.run.provider === 'claude') {
    section.append(element('p', 'capture-note', 'Only submitted context was captured. Claude’s internal model request and hidden context are unavailable.'));
    for (const key of ['submitted_prompt', 'prior_prompts', 'system_prompt', 'observed_step_sequences']) {
      if (Object.hasOwn(detail.request || {}, key)) payloadPreview(section, key.replaceAll('_', ' '), detail.request[key]);
    }
  } else if (captureNotice(view, detail.step) || !Array.isArray(detail.request?.messages ?? detail.request?.input)) {
    section.append(element('p', 'capture-note', 'Complete message context is unavailable; message counts and changes cannot be established.'));
  } else {
    const roles = Object.entries(context.by_role || {});
    section.append(element('p', 'context-summary', `${context.added} added · ${context.removed} removed · ${context.changed} changed messages compared with the previous captured model request.`));
    const list = element('dl', 'context-roles');
    for (const [role, data] of roles) list.append(element('dt', '', role), element('dd', '', `${data.count} messages · ${data.bytes} bytes`));
    section.append(list, element('p', 'muted', 'Byte sizes are not provider token counts.'));
    for (const [heading, candidates] of [['Repeated large tool-output candidates', context.repeated_large_tool_outputs], ['Unchanged message candidates', context.unchanged_content_candidates]]) {
      if (!candidates?.length) continue;
      section.append(element('h4', '', `${heading} (${candidates.length})`));
      for (const value of candidates.slice(0, 5)) section.append(element('pre', 'payload-preview', valuePreview(value, 240)));
      section.append(element('p', 'muted', 'Complete analysis in Raw data.'));
    }
    if (context.input_tokens !== null && context.input_tokens !== undefined) section.append(element('p', 'context-summary', `Provider-reported input tokens: ${context.input_tokens}`));
  }
  parent.append(section);
}

function clearInspector() {
  state.selected = null;
  $('step-detail').hidden = true;
  $('inspector-title').textContent = 'Select a step';
  $('simple-content').replaceChildren(element('p', 'muted', 'Play a saved run or select a step to see what the agent did.'));
  $('playback-caption').textContent = 'Select a step to follow its path.';
  $('step-content').replaceChildren(element('p', 'muted', 'Choose a timeline step to inspect what the agent saw.'));
  $('replay-button').disabled = true;
  $('fork-button').disabled = true;
}

async function selectStep(runId, sequence, fromPlayback = false) {
  if (!fromPlayback) { pausePlayback(); state.playedToEnd = false; }
  const restoreFocus = $('step-detail').contains(document.activeElement);
  const pairRequest = state.pairRequest;
  const stepRequest = ++state.stepRequest;
  clearInspector();
  try {
    const other = state.fork && (runId === state.fork.run.id ? state.base : state.fork);
    const hasOther = other?.steps.some(step => step.sequence === sequence);
    const [detail, paired] = await Promise.all([
      api(`/api/step?id=${encodeURIComponent(runId)}&sequence=${sequence}`),
      hasOther ? api(`/api/step?id=${encodeURIComponent(other.run.id)}&sequence=${sequence}`) : null
    ]);
    if (pairRequest !== state.pairRequest || stepRequest !== state.stepRequest) return;
    const view = state.fork?.run.id === runId ? state.fork : state.base;
    const summary = view.steps.find(step => step.sequence === sequence);
    state.selected = { runId, sequence, detail, summary };
    $('step-detail').hidden = false;
    document.querySelectorAll('.step-node').forEach(node => node.classList.toggle('selected', node.dataset.run === runId && Number(node.dataset.sequence) === sequence));
    updatePlayback();
    if (fromPlayback && !$('timeline-panel').hidden) document.querySelector('.step-node.selected')?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    $('inspector-title').textContent = `Step ${sequence} · ${stepName(summary)}`;
    $('step-meta').textContent = `${view === state.fork ? 'Test run' : 'Recorded run'} · ${stepState(summary)}`;
    const content = $('step-content');
    content.replaceChildren();
    if (state.fork) {
      const recorded = view === state.base ? detail : paired;
      const test = view === state.fork ? detail : paired;
      renderComparisonStep(sequence, recorded, test);
      for (const [name, item] of [['Recorded run', recorded], ['Test run', test]]) {
        content.append(element('h3', 'raw-heading', name));
        if (!item) { content.append(element('p', 'muted', `No step ${sequence} in this run.`)); continue; }
        if (item.step.limitation || !(name === 'Recorded run' ? state.base : state.fork).run.capture_content) content.append(element('p', 'muted', `Capture unavailable${item.step.limitation ? `: ${item.step.limitation}` : '.'}`));
        detailSection(content, 'Request', item.request);
        detailSection(content, item.step.status === 'blocked' ? 'Blocked diagnostic · not a model or tool response' : 'Response', item.response);
        detailSection(content, 'State change', item.state_delta);
        detailSection(content, 'Context analysis', item.context);
      }
    } else {
      if (detail.failure_hint) content.append(element('div', 'hint', `Example diagnosis: ${detail.failure_hint}`));
      if (detail.step.limitation) content.append(element('div', 'hint', `Capture limitation: ${detail.step.limitation}`));
      renderSimple(view, summary, detail);
      detailSection(content, 'Request', detail.request);
      detailSection(content, summary.status === 'blocked' ? 'Blocked diagnostic · not a model or tool response' : 'Response', detail.response);
      detailSection(content, 'State change', detail.state_delta);
      detailSection(content, 'Context analysis', detail.context);
    }
    $('replay-button').hidden = !summary.can_replay;
    $('fork-button').hidden = !summary.can_fork;
    $('replay-button').disabled = !summary.can_replay;
    $('fork-button').disabled = !summary.can_fork;
    $('action-note').replaceChildren();
    for (const [action, reason] of Object.entries(summary.action_limits || {})) {
      if (reason) $('action-note').append(element('span', '', `${action === 'fork' ? 'Response test' : 'Unchanged replay'}: ${reason}`));
    }
    if (summary.can_replay || summary.can_fork) $('action-note').append(element('span', '', view.run.provider === 'example' ? 'Offline · saved checkpoint · Linux Docker required. Original recording preserved.' : view.run.provider === 'claude' ? 'Tool response tests start a live Claude branch.' : 'SDK tests rerun from the beginning; live continuation is optional.'));
    $('step-previous').disabled = view.steps[0]?.sequence === sequence;
    $('step-next').disabled = view.steps.at(-1)?.sequence === sequence;
    if (restoreFocus && !fromPlayback) $('inspector-title').focus({ preventScroll: true });
    $('step-position').textContent = `${view === state.fork ? runKind(view) : 'Recorded run'} · Step ${sequence} of ${view.steps.length}`;
  } catch (error) { if (pairRequest === state.pairRequest && stepRequest === state.stepRequest) status(error.message, true); }
}

async function runAction(path, body, autoplay = false) {
  pausePlayback();
  status('Working…');
  $('record-submit').disabled = true;
  $('fork-submit').disabled = true;
  $('replay-button').disabled = true;
  $('fork-button').disabled = true;
  try {
    const result = await api(path, body);
    $('record-dialog').close();
    $('fork-dialog').close();
    await refreshRuns(result.run_id);
    status('New run saved and selected.');
    if (autoplay) startPlayback(true);
    else $('timeline-title').focus();
  } catch (error) { status(error.message, true); }
  finally {
    $('record-submit').disabled = false;
    $('fork-submit').disabled = false;
    if (state.selected) {
      $('replay-button').disabled = !state.selected.summary.can_replay;
      $('fork-button').disabled = !state.selected.summary.can_fork;
    }
  }
}

$('example-filter').addEventListener('change', renderRuns);
$('demo-button').addEventListener('click', () => openRecording('delivery'));
$('play-button').addEventListener('click', () => state.playing ? pausePlayback() : startPlayback());
$('timeline-toggle').addEventListener('click', () => {
  const panel = $('timeline-panel');
  if (!panel.hidden) pausePlayback();
  panel.hidden = !panel.hidden;
  $('timeline-toggle').setAttribute('aria-expanded', String(!panel.hidden));
  $('timeline-toggle').textContent = panel.hidden ? 'Show full timeline' : 'Hide full timeline';
});
$('previous-button').addEventListener('click', () => {
  if (state.selected?.sequence > 1) selectStep(state.selected.runId, state.selected.sequence - 1);
});
$('next-button').addEventListener('click', () => {
  const view = playbackView();
  if (state.selected && state.selected.sequence < view.steps.length) selectStep(state.selected.runId, state.selected.sequence + 1);
});
$('simple-view').addEventListener('click', () => setView('simple'));
$('technical-view').addEventListener('click', () => setView('technical'));
document.addEventListener('visibilitychange', () => { if (document.hidden && state.playing) pausePlayback(); });
$('fork-picker').addEventListener('change', event => loadPair(state.base.run.id, event.target.value || null).catch(error => status(error.message, true)));
function updateRecordingExample() {
  const [question, expectation] = exampleQuestions[$('record-example').value];
  $('record-question').textContent = question;
  $('record-expectation').textContent = expectation;
}

function openRecording(example) {
  $('record-example').value = example;
  $('capture-content').checked = false;
  updateRecordingExample();
  status('');
  $('record-dialog').showModal();
}

$('record-example').addEventListener('change', updateRecordingExample);
$('record-open').addEventListener('click', () => {
  const filter = $('example-filter').value;
  openRecording(state.examples[filter] ? filter : 'delivery');
});
for (const [id, direction] of [['step-previous', -1], ['step-next', 1]]) {
  $(id).addEventListener('click', () => {
    const view = playbackView();
    const index = view?.steps.findIndex(step => step.sequence === state.selected?.sequence);
    const next = view?.steps[index + direction];
    if (next) selectStep(view.run.id, next.sequence);
  });
}

$('record-cancel').addEventListener('click', () => $('record-dialog').close());
$('record-submit').addEventListener('click', () => runAction('/api/record', {
  application: $('record-example').value,
  capture_content: $('capture-content').checked,
  redact_fields: ($('redact-fields').value || '').split(',').map(field => field.trim()).filter(Boolean)
}));
$('replay-button').addEventListener('click', () => {
  if (state.selected) runAction('/api/replay', { run_id: state.selected.runId, from_step: state.selected.sequence });
});
$('fork-button').addEventListener('click', () => {
  if (!state.selected) return;
  $('fork-description').textContent = `Step ${state.selected.sequence} · ${stepName(state.selected.summary)}: edit the saved response.`;
  $('fork-response').value = JSON.stringify(state.selected.detail.response, null, 2);
  updateReplacementPreview();
  const provider = (state.fork?.run.id === state.selected.runId ? state.fork : state.base).run.provider;
  $('fork-live').checked = false;
  $('fork-live').disabled = provider === 'example';
  $('fork-live').hidden = provider === 'example';
  $('fork-live').textContent = provider === 'claude' ? 'I understand this starts a live Claude branch' : 'Allow live continuation after a changed request';
  $('fork-note').textContent = provider === 'claude' ? 'This reruns the selected turn in a new live Claude session branch. The replacement applies only if the selected tool call recurs. Live calls may use tokens. Only read-only tools are allowed. The original recording is preserved.' : provider === 'openai' ? 'This reruns a new SDK session from its beginning. Matching requests use saved responses. A changed request stops the run unless you allow live continuation. Live calls may use tokens. The original recording is preserved.' : 'This restores the checkpoint before the selected step in Linux Docker and supplies the replacement response. Later matching requests use saved responses; a changed request stops replay. No live calls are made. The original recording is preserved.';
  $('fork-dialog').showModal();
  status('');
});
function updateReplacementPreview() {
  try { $('fork-preview').textContent = valuePreview(JSON.parse($('fork-response').value), 2000); }
  catch { $('fork-preview').textContent = 'Enter valid JSON to preview the replacement.'; }
}
$('fork-response').addEventListener('input', updateReplacementPreview);
$('fork-cancel').addEventListener('click', () => $('fork-dialog').close());
$('fork-submit').addEventListener('click', () => {
  try {
    const response = JSON.parse($('fork-response').value);
    const provider = (state.fork?.run.id === state.selected.runId ? state.fork : state.base).run.provider;
    if (provider === 'example' && (!response || typeof response !== 'object' || Array.isArray(response))) throw new Error('Response must be a JSON object.');
    if (provider === 'claude' && !$('fork-live').checked) throw new Error('Confirm the live Claude branch before creating it.');
    runAction('/api/fork', { run_id: state.selected.runId, step: state.selected.sequence, response, continue_live: $('fork-live').checked });
  } catch (error) { status(error.message, true); }
});

try {
  const examples = await api('/api/examples');
  state.examples = Object.fromEntries(examples.examples.map(id => [id, true]));
  fillExamples();
  await refreshRuns();
  updatePlayback();
} catch (error) { status(error.message, true); }
