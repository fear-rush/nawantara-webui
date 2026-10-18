export function mountDashboard(root) {
const $ = (selector) => root.querySelector(selector);
const lifecycle = new AbortController();
let disposed = false;
let rmsBase = null;
let view = 'jobs', page = 1, busy = false, selectedJob = null, version = 0;
const filters = $('#filters');
const labels = (value) => String(value ?? 'Unknown').replaceAll('_', ' ');
const date = (value) => value ? new Date(value).toLocaleString() : 'Unknown';
function node(tag, text, className = '') {
  const item = document.createElement(tag);
  item.textContent = text; item.className = className;
  return item;
}
async function api(path, body) {
  const token = localStorage.getItem('token');
  const response = await fetch('/api/v1/document-activity/' + path, {
    method: body ? 'POST' : 'GET', cache: 'no-store', signal: lifecycle.signal,
    headers: { Authorization: 'Bearer ' + (token || ''), 'Content-Type': 'application/json' },
    ...(body ? { body: JSON.stringify(body) } : {})
  });
  if ([401, 403].includes(response.status)) throw new Error('Administrator access is required. Sign in to Nawantara with an administrator account.');
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Request failed. Try again.');
  return data;
}
function duration(job) {
  if (!job.started_at) return job.status === 'queued' ? 'Not started' : 'Unknown';
  const seconds = Math.max(0, Math.round(((job.finished_at ? new Date(job.finished_at).getTime() : Date.now()) - new Date(job.started_at).getTime()) / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
function showError(selector, error) {
  if (disposed) return;
  $(selector).textContent = error ? error.message : '';
  $(selector).hidden = !error;
}
function query() {
  const params = new URLSearchParams();
  for (const [key, value] of new FormData(filters)) {
    if (!value) continue;
    if (key === 'from' || key === 'to') {
      const local = new Date(value + (key === 'to' ? 'T23:59:59.999' : 'T00:00:00'));
      params.set(key, local.toISOString());
    } else params.set(key, value);
  }
  params.set('page', page);
  return params;
}
function choices(name, values) {
  const select = filters.elements.namedItem(name), previous = select.value;
  const first = select.options[0].cloneNode(true);
  select.replaceChildren(first);
  for (const [value, label] of values) {
    const option = node('option', label); option.value = value; select.append(option);
  }
  if (previous && ![...select.options].some(o => o.value === previous)) {
    const option = node('option', previous); option.value = previous; select.append(option);
  }
  select.value = previous;
}
function row(item) {
  const tr = document.createElement('tr');
  const action = node('td', labels(item.action));
  const documents = item.documents || (item.document_id ? [{ rms_id: item.document_id }] : []);
  for (const document of documents) action.append(node('span', `RMS ${document.rms_id}${document.title ? ': ' + document.title : ''}`, 'secondary'));
  if (item.resource_id) action.append(node('span', `Resource ${item.resource_id}`, 'secondary'));
  const actor = node('td', item.actor_name || item.actor_id || 'Unknown / system');
  actor.append(node('span', `${labels(item.actor_role)} / ${labels(item.origin)}`, 'secondary'));
  const status = document.createElement('td');
  status.append(node('span', labels(item.status), `badge ${item.status}`));
  if (item.cancel_requested && !item.finished_at) status.append(node('span', 'Cancellation requested', 'secondary'));
  const detail = document.createElement('td');
  if (item.job_id) {
    const button = node('button', 'View job'); button.type = 'button';
    button.addEventListener('click', () => openJob(item.job_id)); detail.append(button);
  } else if (item.changed_fields?.length) detail.textContent = item.changed_fields.join(', ');
  else if (item.http_status) detail.textContent = `HTTP ${item.http_status}`;
  tr.append(action, actor, status, node('td', labels(item.stage || item.scope)),
    node('td', date(item.started_at || item.occurred_at || item.created_at)),
    node('td', view === 'jobs' ? duration(item) : ''), detail);
  return tr;
}
function health(data) {
  const fresh = data.worker_heartbeat && Date.now() - new Date(data.worker_heartbeat).getTime() < 60000;
  $('#health').textContent = `${fresh ? 'Worker connected' : 'Worker heartbeat is stale or unavailable'}. Nawantara collected: ${date(data.nawantara_observed_at)}. RMS collected: ${date(data.rms_observed_at)}.`;
}
async function load() {
  if (busy || disposed) return;
  busy = true; const revision = version;
  try {
    const data = await api(view + '?' + query());
    if (disposed || revision !== version) return;
    showError('#error', null); health(data);
    if (data.rms_url) {
      const url = new URL(data.rms_url);
      if (['http:', 'https:'].includes(url.protocol)) {
        rmsBase = url.href.replace(/\/$/, '');
        $('#rms-link').href = rmsBase + '/tasks'; $('#rms-link').hidden = false;
      }
    }
    $('#rows').replaceChildren(...data.items.map(row));
    if (!data.items.length) {
      const tr = document.createElement('tr'), td = node('td', 'No activity matches these filters.', 'empty');
      td.colSpan = 7; tr.append(td); $('#rows').append(tr);
    }
    choices('actor_id', data.actors.map(a => [a.actor_id, a.actor_name || a.actor_id]));
    choices('action', data.actions.map(a => [a, labels(a)]));
    $('#count').textContent = `${data.count} ${view === 'jobs' ? 'jobs' : 'events'} / Page ${page}`;
    $('#previous').disabled = page <= 1;
    $('#next').disabled = page * data.page_size >= data.count;
  } catch (error) { showError('#error', error); }
  finally { busy = false; if (!disposed && revision !== version) load(); }
}
function addDefinition(list, title, value) { list.append(node('dt', title), node('dd', value)); }
function renderDetail(job) {
  const body = $('#detail-body'), list = document.createElement('dl');
  addDefinition(list, 'Job ID', job.job_id);
  addDefinition(list, 'Action', labels(job.action));
  addDefinition(list, 'Initiator / owner', `${job.actor_name || 'Unknown'} (${job.actor_id || 'unknown'})`);
  addDefinition(list, 'Status', labels(job.status) + (job.cancel_requested ? ' (cancellation requested)' : ''));
  addDefinition(list, 'Stage', labels(job.stage));
  addDefinition(list, 'Created', date(job.created_at));
  addDefinition(list, 'Started', date(job.started_at));
  addDefinition(list, 'Finished', job.finished_at ? date(job.finished_at) : 'Not confirmed');
  addDefinition(list, 'Duration', duration(job));
  addDefinition(list, 'Attempt', String(job.attempt || 1));
  if (job.attribution) addDefinition(list, 'Attribution', job.attribution);
  if (job.parent_job_id) addDefinition(list, 'Related job', job.parent_job_id);
  if (job.summary) for (const [key, value] of Object.entries(job.summary)) addDefinition(list, labels(key), String(value));
  if (job.error) addDefinition(list, 'Error', job.error);
  if (job.error_type) addDefinition(list, 'Error type', job.error_type);
  body.replaceChildren(list);
  for (const document of job.documents || []) body.append(node('p', `RMS document ${document.rms_id}${document.title ? ': ' + document.title : ''}`));
  if (!job.controllable) body.append(node('p', 'This is an observed task. Manage RMS tasks in RMS; child processing stages follow their parent job.', 'muted'));
  if (['failed', 'interrupted'].includes(job.status) && job.controllable && !job.can_retry) body.append(node('p', 'The write outcome is uncertain. Check RMS before submitting another action.', 'muted'));
  if (rmsBase) for (const document of job.documents || []) {
    const link = node('a', `Open RMS document ${document.rms_id}`);
    link.href = rmsBase + '/documents/' + encodeURIComponent(document.rms_id) + '/details';
    link.target = '_blank'; link.rel = 'noopener noreferrer'; body.append(link, node('br', ''));
  }
  const timeline = document.createElement('ol'); timeline.className = 'timeline';
  for (const event of [...job.events].reverse()) timeline.append(node('li', `${date(event.occurred_at)}: ${labels(event.action)} / ${labels(event.status)}${event.stage ? ' / ' + labels(event.stage) : ''}${event.actor_name ? ' / ' + event.actor_name : ''}${event.message ? ': ' + event.message : ''}${event.error ? ': ' + event.error : ''}`));
  if (!job.events.length) timeline.append(node('li', 'No log was recorded for this historical job.'));
  body.append(node('h3', 'Job log'), timeline);
  const controls = $('#controls'); controls.replaceChildren();
  const refresh = node('button', 'Refresh details'); refresh.onclick = () => openJob(job.job_id); controls.append(refresh);
  if (job.controllable && ['queued', 'processing'].includes(job.status) && !job.cancel_requested) addControl('Cancel job', 'cancel');
  if (job.can_retry) addControl(job.retry_job_id ? 'Open retry' : 'Retry from checkpoint', 'retry');
}
function addControl(label, operation) {
  const button = node('button', label);
  button.onclick = async () => {
    if (operation === 'cancel' && !confirm('Cancel at the next safe boundary? Changes already saved will remain.')) return;
    button.disabled = true;
    const requestedJob = selectedJob;
    try {
      const job = await api('jobs/' + encodeURIComponent(requestedJob), { operation });
      if (!disposed && selectedJob === requestedJob) await openJob(job.job_id);
      load();
    } catch (error) { showError('#detail-error', error); button.disabled = false; }
  };
  $('#controls').append(button);
}
async function refreshDetail(id) {
  try {
    const job = await api('jobs/' + encodeURIComponent(id));
    if (!disposed && selectedJob === id && $('#detail').open) renderDetail(job);
  } catch (error) { showError('#detail-error', error); }
}
async function openJob(id) {
  selectedJob = id;
  $('#detail-body').replaceChildren(node('p', 'Loading job details...'));
  $('#controls').replaceChildren();
  if (!$('#detail').open) $('#detail').showModal();
  showError('#detail-error', null);
  try { const job = await api('jobs/' + encodeURIComponent(id)); if (!disposed && selectedJob === id) renderDetail(job); }
  catch (error) { showError('#detail-error', error); }
}
for (const kind of ['jobs', 'events']) $(`#${kind}-tab`).onclick = () => {
  view = kind; page = 1; version++;
  $('#jobs-tab').setAttribute('aria-pressed', String(kind === 'jobs'));
  $('#events-tab').setAttribute('aria-pressed', String(kind === 'events'));
  $('#caption').textContent = kind === 'jobs' ? 'Document jobs' : 'Document changes'; load();
};
filters.onsubmit = (event) => { event.preventDefault(); page = 1; version++; load(); };
$('#reset').onclick = () => { filters.reset(); page = 1; version++; load(); };
$('#refresh').onclick = load;
$('#previous').onclick = () => { page--; version++; load(); };
$('#next').onclick = () => { page++; version++; load(); };
$('#close').onclick = () => $('#detail').close();
const timer = setInterval(() => { if (!document.hidden) { load(); if ($('#detail').open && selectedJob) refreshDetail(selectedJob); } }, 10000);
load();
return () => {
  disposed = true;
  lifecycle.abort();
  clearInterval(timer);
  if ($('#detail').open) $('#detail').close();
};
}
