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
let recordingRows = [];
let selectedRecordingId = null;

const nameCollator = new Intl.Collator('pl', {numeric: true, sensitivity: 'base'});

const correctionWord = (count) => {
  if (count === 1) return 'zmiana';
  const lastTwo = count % 100;
  const last = count % 10;
  if (last >= 2 && last <= 4 && !(lastTwo >= 12 && lastTwo <= 14)) return 'zmiany';
  return 'zmian';
};

const segmentWord = count => count === 1 ? 'segment' : 'segmentów';

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
    setText(node, segment.corrected_text);
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

function renderSegment(segment) {
  const fragment = template.content.cloneNode(true);
  const article = fragment.querySelector('.segment');
  const timestamp = fragment.querySelector('.timestamp');
  setText(timestamp, clock(segment.start_ms));
  timestamp.addEventListener('click', () => {
    audio.currentTime = segment.start_ms / 1000;
    audio.play();
  });
  setText(fragment.querySelector('.speaker'), segment.speaker);
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

async function loadRecording(recordingId) {
  const transcript = await fetch(`/api/recordings/${encodeURIComponent(recordingId)}`, {cache: 'no-store'}).then(r => r.json());
  empty.hidden = true;
  detail.hidden = false;
  setText(document.querySelector('#recording-title'), transcript.display_name || transcript.recording_id);
  setText(document.querySelector('#metadata'), `${clock(transcript.audio.duration_ms)} · ${transcript.audio.codec} · ${transcript.audio.sample_rate_hz} Hz · ${transcript.audio.channels} kanał`);
  audio.src = `/api/recordings/${encodeURIComponent(recordingId)}/audio`;
  segmentsNode.replaceChildren(...transcript.segments.map(renderSegment));
}

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
