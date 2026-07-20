const list = document.querySelector('#recordings');
const stats = document.querySelector('#stats');
const empty = document.querySelector('#empty');
const detail = document.querySelector('#detail');
const segmentsNode = document.querySelector('#segments');
const audio = document.querySelector('#audio');
const showRaw = document.querySelector('#show-raw');
const template = document.querySelector('#segment-template');
const themeToggle = document.querySelector('#theme-toggle');
const sidebarResizer = document.querySelector('#sidebar-resizer');
const comparison = document.querySelector('#diarization-comparison');
const timeline = document.querySelector('#diarization-timeline');
const speakerLegend = document.querySelector('#speaker-legend');
const transcriptDivision = document.querySelector('#transcript-division');
const divisionTabs = document.querySelector('#division-tabs');
let recordingRows = [];
let selectedRecordingId = null;
let currentDurationMs = 0;
let currentTranscript = null;
let activeDivisionSystem = null;

const nameCollator = new Intl.Collator('pl', {numeric: true, sensitivity: 'base'});

const correctionWord = (count) => {
  if (count === 1) return 'zmiana';
  const lastTwo = count % 100;
  const last = count % 10;
  if (last >= 2 && last <= 4 && !(lastTwo >= 12 && lastTwo <= 14)) return 'zmiany';
  return 'zmian';
};

const segmentWord = count => count === 1 ? 'segment' : 'segmentów';
const blockWord = (count) => {
  if (count === 1) return 'blok';
  const lastTwo = count % 100;
  const last = count % 10;
  return last >= 2 && last <= 4 && !(lastTwo >= 12 && lastTwo <= 14) ? 'bloki' : 'bloków';
};

function setTheme(theme) {
  const dark = theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  themeToggle.textContent = dark ? 'Jasny motyw' : 'Ciemny motyw';
  themeToggle.setAttribute('aria-pressed', String(dark));
  try { localStorage.setItem('pbx-theme', dark ? 'dark' : 'light'); } catch (_) {}
}

function renderCorrectedText(node, segment) {
  const corrections = [...(segment.corrections || [])]
    .filter(item => Number.isInteger(item.start_char) && Number.isInteger(item.end_char))
    .sort((a, b) => a.start_char - b.start_char);
  if (!corrections.length) {
    node.append(document.createTextNode(String(segment.corrected_text ?? '')));
    return;
  }
  const source = String(segment.raw_text ?? '');
  let cursor = 0;
  corrections.forEach(correction => {
    const start = Math.max(cursor, correction.start_char);
    const end = Math.max(start, correction.end_char);
    node.append(document.createTextNode(source.slice(cursor, start)));
    const marked = document.createElement('mark');
    marked.className = 'correction-inline';
    marked.textContent = correction.replacement || '∅';
    marked.title = `Poprawiono z: ${correction.original || '∅'}`;
    marked.setAttribute('aria-label', `Poprawiono z ${correction.original || 'pustego fragmentu'} na ${correction.replacement || 'usunięcie'}`);
    node.append(marked);
    cursor = end;
  });
  node.append(document.createTextNode(source.slice(cursor)));
}

const clock = (ms) => {
  const total = Math.floor(ms / 1000);
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
};

const setText = (node, value) => { node.textContent = String(value ?? ''); };

async function loadList() {
  const rows = await fetch('/api/recordings', {cache: 'no-store'}).then(r => r.json());
  recordingRows = rows.sort((a, b) => nameCollator.compare(a.display_name || a.recording_id, b.display_name || b.recording_id));
  list.replaceChildren();
  let correctionCount = 0;
  recordingRows.forEach((row, index) => {
    correctionCount += row.correction_count;
    const option = document.createElement('button');
    const displayName = row.display_name || row.recording_id;
    option.type = 'button';
    option.className = 'recording-option';
    option.dataset.recordingId = row.recording_id;
    option.setAttribute('role', 'option');
    option.setAttribute('aria-selected', String(index === 0));
    option.title = displayName;
    option.innerHTML = '<span class="recording-name"></span><span class="recording-meta"></span><span class="recording-corrections"></span>';
    setText(option.querySelector('.recording-name'), displayName);
    setText(option.querySelector('.recording-meta'), `${clock(row.duration_ms)} · ${row.segment_count} ${segmentWord(row.segment_count)}`);
    const badge = option.querySelector('.recording-corrections');
    setText(badge, `${row.correction_count} ${correctionWord(row.correction_count)}`);
    badge.classList.toggle('is-empty', row.correction_count === 0);
    option.addEventListener('click', () => selectRecording(row.recording_id));
    list.append(option);
  });
  setText(stats, `${recordingRows.length} transkrypcji · ${correctionCount} ${correctionWord(correctionCount)}`);
  if (recordingRows.length) {
    await selectRecording(recordingRows[0].recording_id, false);
  }
}

async function selectRecording(recordingId, focus = true) {
  selectedRecordingId = recordingId;
  const options = [...list.querySelectorAll('.recording-option')];
  options.forEach(option => option.setAttribute('aria-selected', String(option.dataset.recordingId === recordingId)));
  const selected = options.find(option => option.dataset.recordingId === recordingId);
  if (focus) selected?.focus({preventScroll: true});
  selected?.scrollIntoView({block: 'nearest'});
  await loadRecording(recordingId);
}

const systemLabel = systemId => ({
  pyannote: 'Pyannote',
  nvidia_sortformer: 'NVIDIA',
}[systemId] || systemId);

const speakerIndex = speaker => {
  const match = String(speaker || '').match(/(\d+)$/);
  return match ? Number(match[1]) % 8 : 7;
};

function renderDiarization(data, durationMs) {
  timeline.replaceChildren();
  speakerLegend.replaceChildren();
  const systems = data?.systems || [];
  comparison.hidden = systems.length === 0;
  if (!systems.length) return;

  const speakers = [...new Set(systems.flatMap(system => (system.turns || []).map(turn => turn.speaker)))];
  speakers.sort(nameCollator.compare);
  speakers.forEach(speaker => {
    const item = document.createElement('span');
    item.className = 'speaker-legend-item';
    item.innerHTML = `<i class="speaker-color speaker-${speakerIndex(speaker)}"></i>`;
    item.append(document.createTextNode(speaker));
    speakerLegend.append(item);
  });

  systems.forEach(system => {
    const row = document.createElement('div');
    row.className = 'timeline-row';
    const name = document.createElement('div');
    name.className = 'timeline-system';
    setText(name, systemLabel(system.system_id));
    const track = document.createElement('div');
    track.className = 'timeline-track';
    if (system.status !== 'complete') {
      track.classList.add('is-failed');
      const failed = document.createElement('span');
      failed.className = 'timeline-error';
      setText(failed, `Brak wyniku (${system.error_type || 'błąd'})`);
      track.append(failed);
    }
    (system.turns || []).forEach(turn => {
      const block = document.createElement('button');
      block.type = 'button';
      block.className = `speaker-block speaker-${speakerIndex(turn.speaker)}`;
      block.style.left = `${Math.max(0, turn.start_ms / durationMs * 100)}%`;
      block.style.width = `${Math.max(.12, (turn.end_ms - turn.start_ms) / durationMs * 100)}%`;
      const original = turn.original_speaker && turn.original_speaker !== turn.speaker ? ` · oryg. ${turn.original_speaker}` : '';
      block.title = `${systemLabel(system.system_id)} · ${turn.speaker}${original} · ${clock(turn.start_ms)}–${clock(turn.end_ms)}`;
      block.setAttribute('aria-label', block.title);
      block.addEventListener('click', () => {
        audio.currentTime = turn.start_ms / 1000;
        audio.play();
      });
      track.append(block);
    });
    const cursor = document.createElement('span');
    cursor.className = 'playback-cursor';
    track.append(cursor);
    row.append(name, track);
    timeline.append(row);
  });
}

function renderSegment(segment, primarySystem) {
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector('.segment');
  const timestamp = fragment.querySelector('.timestamp');
  setText(timestamp, clock(segment.start_ms));
  timestamp.addEventListener('click', () => {
    audio.currentTime = segment.start_ms / 1000;
    audio.play();
  });
  setText(fragment.querySelector('.speaker'), segment.speaker);
  const interpretations = fragment.querySelector('.speaker-interpretations');
  const speakerValues = Object.entries(segment.speaker_interpretations || {});
  const disagreement = new Set(speakerValues.map(([, speaker]) => speaker)).size > 1;
  speakerValues.forEach(([systemId, speaker]) => {
    const badge = document.createElement('span');
    badge.className = `speaker-system-badge${systemId === primarySystem ? ' is-primary' : ''}`;
    setText(badge, `${systemLabel(systemId)}: ${speaker}`);
    interpretations.append(badge);
  });
  if (disagreement) {
    article.classList.add('speaker-disagreement');
    article.title = 'Systemy diaryzacji przypisały różne etykiety mówcy';
  }
  const corrections = segment.corrections || [];
  renderCorrectedText(fragment.querySelector('.text'), segment);
  const raw = fragment.querySelector('.raw');
  setText(fragment.querySelector('.raw-text'), segment.raw_text);
  raw.hidden = !showRaw.checked;
  const changes = fragment.querySelector('.changes');
  corrections.forEach(correction => {
    const badge = document.createElement('span');
    badge.className = 'change';
    const old = document.createElement('span');
    old.className = 'old';
    setText(old, correction.original || '∅');
    const arrow = document.createElement('span');
    arrow.className = 'arrow';
    arrow.textContent = '→';
    const replacement = document.createElement('span');
    setText(replacement, correction.replacement || '∅');
    badge.append(old, arrow, replacement);
    changes.append(badge);
  });
  if (corrections.length) {
    article.classList.add('has-corrections');
    const count = fragment.querySelector('.change-count');
    count.hidden = false;
    setText(count, `${corrections.length} ${correctionWord(corrections.length)}`);
    changes.hidden = false;
  }
  article.dataset.startMs = segment.start_ms;
  return fragment;
}

const overlapMs = (firstStart, firstEnd, secondStart, secondEnd) =>
  Math.max(0, Math.min(firstEnd, secondEnd) - Math.max(firstStart, secondStart));

function locateTurn(turns, startMs, endMs) {
  const midpoint = (startMs + endMs) / 2;
  let bestIndex = -1;
  let bestOverlap = 0;
  turns.forEach((turn, index) => {
    const overlap = overlapMs(startMs, endMs, turn.start_ms, turn.end_ms);
    if (overlap > bestOverlap || (overlap === bestOverlap && turn.start_ms <= midpoint && midpoint <= turn.end_ms)) {
      bestIndex = index;
      bestOverlap = overlap;
    }
  });
  if (bestIndex >= 0) return bestIndex;
  let nearestDistance = Infinity;
  turns.forEach((turn, index) => {
    const distance = Math.min(Math.abs(midpoint - turn.start_ms), Math.abs(midpoint - turn.end_ms));
    if (distance < nearestDistance && distance <= 750) {
      nearestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function locateWordSpans(segment) {
  const source = String(segment.raw_text ?? '');
  let cursor = 0;
  return (segment.words || []).map(word => {
    const token = String(word.text ?? '').trim();
    if (!token) return {start: cursor, end: cursor};
    let start = source.indexOf(token, cursor);
    if (start < 0) start = source.toLocaleLowerCase('pl').indexOf(token.toLocaleLowerCase('pl'), cursor);
    if (start < 0) start = cursor;
    const end = Math.min(source.length, Math.max(start, start + token.length));
    cursor = end;
    return {start, end};
  });
}

function sliceSegment(segment, startChar, endChar, startMs, endMs) {
  const source = String(segment.raw_text ?? '');
  const rawText = source.slice(startChar, endChar);
  const corrections = (segment.corrections || [])
    .filter(item => item.start_char >= startChar && item.end_char <= endChar)
    .map(item => ({...item, start_char: item.start_char - startChar, end_char: item.end_char - startChar}));
  return {
    start_ms: startMs,
    end_ms: endMs,
    raw_text: rawText,
    corrected_text: rawText,
    corrections,
  };
}

function buildDivisionBlocks(transcript, systemId) {
  const system = (transcript.speaker_diarization?.systems || [])
    .find(item => item.system_id === systemId && item.status === 'complete');
  if (!system) return [];
  const turns = system.turns || [];
  const pieces = [];

  (transcript.segments || []).forEach(segment => {
    const words = segment.words || [];
    if (!words.length) {
      const turnIndex = locateTurn(turns, segment.start_ms, segment.end_ms);
      pieces.push({
        turnIndex,
        speaker: turnIndex >= 0 ? turns[turnIndex].speaker : 'SPEAKER_UNKNOWN',
        ...sliceSegment(segment, 0, String(segment.raw_text ?? '').length, segment.start_ms, segment.end_ms),
      });
      return;
    }

    const spans = locateWordSpans(segment);
    const assignments = words.map(word => locateTurn(turns, word.start_ms, word.end_ms));
    let first = 0;
    while (first < words.length) {
      let last = first;
      while (last + 1 < words.length && assignments[last + 1] === assignments[first]) last += 1;
      const startChar = first === 0 ? 0 : spans[first].start;
      const endChar = last + 1 < words.length ? spans[last + 1].start : String(segment.raw_text ?? '').length;
      const turnIndex = assignments[first];
      pieces.push({
        turnIndex,
        speaker: turnIndex >= 0 ? turns[turnIndex].speaker : 'SPEAKER_UNKNOWN',
        ...sliceSegment(segment, startChar, endChar, words[first].start_ms, words[last].end_ms),
      });
      first = last + 1;
    }
  });

  const blocks = [];
  pieces.forEach(piece => {
    const previous = blocks.at(-1);
    if (previous && previous.speaker === piece.speaker) {
      previous.end_ms = Math.max(previous.end_ms, piece.end_ms);
      previous.pieces.push(piece);
      previous.correction_count += piece.corrections.length;
    } else {
      blocks.push({
        turnIndex: piece.turnIndex,
        speaker: piece.speaker,
        start_ms: piece.start_ms,
        end_ms: piece.end_ms,
        pieces: [piece],
        correction_count: piece.corrections.length,
      });
    }
  });
  return blocks;
}

function renderDivisionBlock(block, systemId) {
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector('.segment');
  const timestamp = fragment.querySelector('.timestamp');
  setText(timestamp, clock(block.start_ms));
  timestamp.addEventListener('click', () => {
    audio.currentTime = block.start_ms / 1000;
    audio.play();
  });
  setText(fragment.querySelector('.speaker'), block.speaker);
  const badge = document.createElement('span');
  badge.className = 'speaker-system-badge is-primary';
  setText(badge, `Podział: ${systemLabel(systemId)}`);
  fragment.querySelector('.speaker-interpretations').append(badge);

  const correctedNode = fragment.querySelector('.text');
  const rawNode = fragment.querySelector('.raw-text');
  block.pieces.forEach((piece, index) => {
    if (index) {
      correctedNode.append(document.createTextNode(' '));
      rawNode.append(document.createTextNode(' '));
    }
    renderCorrectedText(correctedNode, piece);
    rawNode.append(document.createTextNode(piece.raw_text));
  });
  fragment.querySelector('.raw').hidden = !showRaw.checked;

  const changes = fragment.querySelector('.changes');
  block.pieces.flatMap(piece => piece.corrections).forEach(correction => {
    const change = document.createElement('span');
    change.className = 'change';
    const old = document.createElement('span');
    old.className = 'old';
    setText(old, correction.original || '∅');
    const arrow = document.createElement('span');
    arrow.className = 'arrow';
    arrow.textContent = '→';
    const replacement = document.createElement('span');
    setText(replacement, correction.replacement || '∅');
    change.append(old, arrow, replacement);
    changes.append(change);
  });
  if (block.correction_count) {
    article.classList.add('has-corrections');
    const count = fragment.querySelector('.change-count');
    count.hidden = false;
    setText(count, `${block.correction_count} ${correctionWord(block.correction_count)}`);
    changes.hidden = false;
  }
  article.dataset.startMs = block.start_ms;
  return fragment;
}

function renderTranscriptDivision(systemId) {
  if (!currentTranscript) return;
  activeDivisionSystem = systemId;
  try { localStorage.setItem('pbx-division-system', systemId); } catch (_) {}
  divisionTabs.querySelectorAll('.division-tab').forEach(tab => {
    tab.setAttribute('aria-selected', String(tab.dataset.systemId === systemId));
    tab.tabIndex = tab.dataset.systemId === systemId ? 0 : -1;
  });
  const blocks = buildDivisionBlocks(currentTranscript, systemId);
  segmentsNode.replaceChildren(...blocks.map(block => renderDivisionBlock(block, systemId)));
}

function setupTranscriptDivision(transcript) {
  const available = (transcript.speaker_diarization?.systems || [])
    .filter(system => system.status === 'complete' && (system.turns || []).length && ['pyannote', 'nvidia_sortformer'].includes(system.system_id));
  divisionTabs.replaceChildren();
  transcriptDivision.hidden = available.length === 0;
  if (!available.length) {
    segmentsNode.replaceChildren(...transcript.segments.map(segment => renderSegment(segment, transcript.speaker_diarization?.primary_system)));
    return;
  }
  const blockCounts = new Map(available.map(system => [system.system_id, buildDivisionBlocks(transcript, system.system_id).length]));
  available.forEach(system => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'division-tab';
    tab.dataset.systemId = system.system_id;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', 'segments');
    const count = blockCounts.get(system.system_id);
    setText(tab, `${systemLabel(system.system_id)} · ${count} ${blockWord(count)}`);
    tab.addEventListener('click', () => renderTranscriptDivision(system.system_id));
    divisionTabs.append(tab);
  });
  let preferred = null;
  try { preferred = localStorage.getItem('pbx-division-system'); } catch (_) {}
  const systemIds = available.map(system => system.system_id);
  const selected = systemIds.includes(preferred)
    ? preferred
    : (systemIds.includes(transcript.speaker_diarization?.primary_system) ? transcript.speaker_diarization.primary_system : systemIds[0]);
  renderTranscriptDivision(selected);
}

async function loadRecording(recordingId) {
  const transcript = await fetch(`/api/recordings/${encodeURIComponent(recordingId)}`, {cache: 'no-store'}).then(r => r.json());
  currentTranscript = transcript;
  empty.hidden = true;
  detail.hidden = false;
  setText(document.querySelector('#recording-title'), transcript.display_name || transcript.recording_id);
  setText(document.querySelector('#metadata'), `${clock(transcript.audio.duration_ms)} · ${transcript.audio.codec} · ${transcript.audio.sample_rate_hz} Hz · ${transcript.audio.channels} kanał`);
  currentDurationMs = Math.max(1, transcript.audio.duration_ms || 1);
  audio.src = `/api/recordings/${encodeURIComponent(recordingId)}/audio`;
  renderDiarization(transcript.speaker_diarization, currentDurationMs);
  setupTranscriptDivision(transcript);
}

audio.addEventListener('timeupdate', () => {
  const position = Math.max(0, Math.min(100, audio.currentTime * 1000 / currentDurationMs * 100));
  document.querySelectorAll('.playback-cursor').forEach(cursor => { cursor.style.left = `${position}%`; });
});

list.addEventListener('keydown', event => {
  if (!recordingRows.length) return;
  const index = Math.max(0, recordingRows.findIndex(row => row.recording_id === selectedRecordingId));
  let next = index;
  if (event.key === 'ArrowDown') next = Math.min(recordingRows.length - 1, index + 1);
  else if (event.key === 'ArrowUp') next = Math.max(0, index - 1);
  else if (event.key === 'Home') next = 0;
  else if (event.key === 'End') next = recordingRows.length - 1;
  else return;
  event.preventDefault();
  selectRecording(recordingRows[next].recording_id);
});
showRaw.addEventListener('change', () => {
  document.querySelectorAll('.raw').forEach(node => { node.hidden = !showRaw.checked; });
});
themeToggle.addEventListener('click', () => {
  setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});
setTheme(document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light');

const setSidebarWidth = width => {
  const max = Math.max(320, Math.min(760, window.innerWidth - 360));
  const value = Math.max(240, Math.min(max, Math.round(width)));
  document.documentElement.style.setProperty('--sidebar-width', `${value}px`);
  sidebarResizer.setAttribute('aria-valuemin', '240');
  sidebarResizer.setAttribute('aria-valuemax', String(max));
  sidebarResizer.setAttribute('aria-valuenow', String(value));
  try { localStorage.setItem('pbx-sidebar-width', String(value)); } catch (_) {}
};

try { setSidebarWidth(Number(localStorage.getItem('pbx-sidebar-width')) || 320); } catch (_) { setSidebarWidth(320); }
sidebarResizer.addEventListener('pointerdown', event => {
  if (event.button !== 0) return;
  sidebarResizer.setPointerCapture(event.pointerId);
  sidebarResizer.classList.add('is-dragging');
  document.body.classList.add('is-resizing');
});
sidebarResizer.addEventListener('pointermove', event => {
  if (sidebarResizer.hasPointerCapture(event.pointerId)) setSidebarWidth(event.clientX - 5);
});
const stopResize = event => {
  if (sidebarResizer.hasPointerCapture(event.pointerId)) sidebarResizer.releasePointerCapture(event.pointerId);
  sidebarResizer.classList.remove('is-dragging');
  document.body.classList.remove('is-resizing');
};
sidebarResizer.addEventListener('pointerup', stopResize);
sidebarResizer.addEventListener('pointercancel', stopResize);
let mouseResizeActive = false;
sidebarResizer.addEventListener('mousedown', event => {
  if (event.button !== 0) return;
  mouseResizeActive = true;
  sidebarResizer.classList.add('is-dragging');
  document.body.classList.add('is-resizing');
  event.preventDefault();
});
window.addEventListener('mousemove', event => {
  if (mouseResizeActive) setSidebarWidth(event.clientX - 5);
});
window.addEventListener('mouseup', () => {
  if (!mouseResizeActive) return;
  mouseResizeActive = false;
  sidebarResizer.classList.remove('is-dragging');
  document.body.classList.remove('is-resizing');
});
sidebarResizer.addEventListener('keydown', event => {
  const current = Number.parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width'), 10) || 320;
  if (event.key === 'ArrowLeft') setSidebarWidth(current - 20);
  else if (event.key === 'ArrowRight') setSidebarWidth(current + 20);
  else if (event.key === 'Home') setSidebarWidth(240);
  else return;
  event.preventDefault();
});
window.addEventListener('resize', () => setSidebarWidth(Number.parseInt(getComputedStyle(document.documentElement).getPropertyValue('--sidebar-width'), 10) || 320));
loadList().catch(() => setText(stats, 'Nie udało się wczytać danych'));
