const $ = (id) => document.getElementById(id);
const STORAGE_KEY = 'lantern-archive-v1';
const state = {name: '', relationship: '', memories: ['', '']};
let ready = false;
let busy = false;

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

function load() {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
    if (stored && typeof stored.name === 'string' && Array.isArray(stored.memories)) {
      state.name = stored.name.slice(0, 80);
      state.relationship = String(stored.relationship || '').slice(0, 80);
      state.memories = stored.memories.slice(0, 8).map(value => String(value).slice(0, 600));
      if (!state.memories.length) state.memories = [''];
    }
  } catch (_) { /* A malformed saved draft should not block the app. */ }
}

function renderMemories() {
  $('memories').replaceChildren();
  state.memories.forEach((memory, index) => {
    const row = document.createElement('div'); row.className = 'memory-row';
    const number = document.createElement('span'); number.className = 'memory-number'; number.textContent = String(index + 1).padStart(2, '0');
    const field = document.createElement('textarea'); field.className = 'memory-input'; field.maxLength = 600;
    field.placeholder = index === 0 ? 'The kitchen always smelled like toasted cinnamon on Sundays…' : 'A small moment, an object, a phrase…';
    field.setAttribute('aria-label', `Memory ${index + 1}`); field.value = memory;
    field.addEventListener('input', () => { state.memories[index] = field.value; save(); });
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove-memory'; remove.textContent = '×';
    remove.setAttribute('aria-label', `Remove memory ${index + 1}`);
    remove.addEventListener('click', () => { state.memories.splice(index, 1); if (!state.memories.length) state.memories.push(''); renderMemories(); save(); });
    row.append(number, field, remove); $('memories').append(row);
  });
  $('memory-count').textContent = `${String(state.memories.length).padStart(2, '0')} / 08`;
  $('add-memory').disabled = state.memories.length >= 8;
}

function setStatus(isReady) {
  ready = isReady;
  $('status').classList.toggle('ready', isReady);
  $('status-text').textContent = isReady ? 'Local model ready' : 'Starting local model';
  if (!busy) { $('generate').disabled = !isReady; $('generate-label').textContent = isReady ? 'Write a remembrance' : 'Preparing the local model…'; }
  $('ask-question').disabled = !isReady;
}

async function pollStatus() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    const result = await response.json(); setStatus(Boolean(result.ready));
    if (result.error) { $('status-text').textContent = result.error; $('generate-label').textContent = 'Local model unavailable'; }
  } catch (_) { setStatus(false); }
}

async function generate() {
  if (!ready || busy) return;
  state.name = $('name').value.trim(); state.relationship = $('relationship').value.trim(); save();
  if (!state.name || !state.memories.some(value => value.trim())) {
    alert('Add a name and at least one memory before writing.'); return;
  }
  busy = true; $('generate').disabled = true; $('generate-label').textContent = 'Writing locally…';
  try {
    const response = await fetch('/api/generate', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The local model could not complete the draft.');
    $('draft').textContent = result.draft; $('empty').hidden = true; $('output-content').hidden = false;
    $('copy').disabled = false; $('download').disabled = false;
  } catch (error) { alert(error.message); }
  finally { busy = false; $('generate').disabled = !ready; $('generate-label').textContent = 'Rewrite the remembrance'; }
}

async function askQuestion() {
  if (!ready) return;
  state.name = $('name').value.trim(); state.relationship = $('relationship').value.trim(); save();
  if (!state.name || !state.memories.some(value => value.trim())) {
    alert('Add a name and at least one memory to get a question.'); return;
  }
  $('ask-question').disabled = true; $('question-text').textContent = 'Thinking of a question…';
  try {
    const response = await fetch('/api/question', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(state)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The local model could not suggest a question.');
    $('question-text').textContent = result.question;
  } catch (error) { $('question-text').textContent = error.message; }
  finally { $('ask-question').disabled = !ready; }
}

function clearAll() {
  if (!confirm('Clear the name and memories from this browser?')) return;
  state.name = ''; state.relationship = ''; state.memories = ['', ''];
  $('name').value = ''; $('relationship').value = ''; renderMemories(); save();
  $('draft').textContent = ''; $('empty').hidden = false; $('output-content').hidden = true;
  $('question-text').textContent = 'Need help recalling one more detail?';
  $('copy').disabled = true; $('download').disabled = true;
}

function sample() {
  state.name = 'Mira'; state.relationship = 'Grandmother';
  state.memories = [
    'She saved orange peels in a blue tin and used them to flavor tea.',
    'Every winter she taught the grandchildren to mend socks by the window.',
    'She called the first rain of spring “the garden waking up.”'
  ];
  $('name').value = state.name; $('relationship').value = state.relationship; renderMemories(); save();
}

function downloadDraft() {
  const blob = new Blob([$('draft').innerText.trim() + '\n'], {type: 'text/plain;charset=utf-8'});
  const link = document.createElement('a'); link.href = URL.createObjectURL(blob);
  link.download = `remembrance-${(state.name || 'draft').toLowerCase().replace(/[^a-z0-9]+/g, '-')}.txt`;
  link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

load(); $('name').value = state.name; $('relationship').value = state.relationship; renderMemories();
$('name').addEventListener('input', event => { state.name = event.target.value; save(); });
$('relationship').addEventListener('input', event => { state.relationship = event.target.value; save(); });
$('add-memory').addEventListener('click', () => { if (state.memories.length < 8) { state.memories.push(''); renderMemories(); save(); $('memories').lastElementChild.querySelector('textarea').focus(); } });
$('sample').addEventListener('click', sample); $('clear').addEventListener('click', clearAll);
$('generate').addEventListener('click', generate);
$('ask-question').addEventListener('click', askQuestion);
$('copy').addEventListener('click', async () => { await navigator.clipboard.writeText($('draft').innerText.trim()); $('copy').textContent = 'Copied ✓'; setTimeout(() => $('copy').textContent = 'Copy draft ↗', 2000); });
$('download').addEventListener('click', downloadDraft);
pollStatus(); setInterval(pollStatus, 3000);
