const state = {
  files: [],
  folders: [],
  allFiles: [],
  selected: new Set(),
  selectedFolders: new Set(),
  folderId: null,
  folderStack: [],
  sort: 'date',
  order: 'desc',
  type: '',
  q: '',
  view: localStorage.getItem('vault-view-mode') || 'list',
  activeTransfers: 0,
};

function beginTransfer() {
  state.activeTransfers += 1;
}

function endTransfer() {
  state.activeTransfers = Math.max(0, state.activeTransfers - 1);
}

function esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtSize(bytes) {
  if (bytes < 1024) return `${bytes} <span class="unit">B</span>`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} <span class="unit">KB</span>`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)} <span class="unit">MB</span>`;
  return `${(bytes / 1024 ** 3).toFixed(2)} <span class="unit">GB</span>`;
}

function fmtDate(iso) {
  const d = new Date(iso), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  if (diff < 172800) return '昨天';
  return d.toLocaleDateString('zh-TW');
}

function mimeIcon(mime) {
  if (!mime) return 'doc';
  if (mime.startsWith('image/')) return 'img';
  if (mime.startsWith('video/')) return 'vid';
  if (mime === 'application/zip' || mime === 'application/x-rar-compressed') return 'zip';
  return 'doc';
}

function mimeLabel(mime) {
  if (!mime) return '檔案';
  const parts = mime.split('/');
  return (parts[1] || parts[0]).toUpperCase().replace('OCTET-STREAM', '檔案');
}

function isPreviewable(mime) {
  return mime && (mime.startsWith('image/') || mime.startsWith('video/') || mime === 'application/pdf');
}

function canUseThumbnail(file) {
  const mime = file.mime_type || '';
  return !file.encrypted && (file.has_thumbnail || mime.startsWith('image/'));
}

function toast(msg, isErr = false) {
  const el = document.createElement('div');
  el.className = `toast${isErr ? ' err' : ''}`;
  el.textContent = msg;
  document.getElementById('toast-area').append(el);
  setTimeout(() => el.remove(), 3000);
}

let ctxOpen = false;
function openCtx(e, file) {
  e.stopPropagation();
  const menu = document.getElementById('ctx-menu');
  const canPreview = isPreviewable(file.mime_type);
  menu.innerHTML = `
    ${canPreview ? `<button data-action="preview"><svg width="12" height="12"><use href="#i-eye"/></svg> 預覽</button>` : ''}
    <button data-action="download"><svg width="12" height="12"><use href="#i-down"/></svg> 下載</button>
    <div class="sep"></div>
    <button data-action="delete" class="danger"><svg width="12" height="12"><use href="#i-trash"/></svg> 刪除</button>
  `;
  menu.style.display = 'block';
  const x = Math.min(e.clientX, window.innerWidth - 180);
  const y = Math.min(e.clientY, window.innerHeight - 140);
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  ctxOpen = true;
  menu.onclick = (ev) => {
    const action = ev.target.closest('[data-action]')?.dataset.action;
    if (action === 'preview') showPreview(file);
    if (action === 'download') doDownload(file);
    if (action === 'delete') doDelete(file.id);
    closeCtx();
  };
}
function closeCtx() {
  document.getElementById('ctx-menu').style.display = 'none';
  ctxOpen = false;
}
document.addEventListener('click', () => { if (ctxOpen) closeCtx(); });

async function showPreview(file) {
  const bg = document.getElementById('modal-bg');
  const content = document.getElementById('modal-content');
  let inner = '';
  let src = `/api/files/${file.id}/preview`;
  if (file.encrypted) {
    try {
      src = await decryptedObjectUrl(file);
    } catch (err) {
      toast(err.message || 'Decrypt failed', true);
      return;
    }
  }
  if (file.mime_type && file.mime_type.startsWith('image/')) {
    inner = `<img src="${src}" alt="${esc(file.name)}">`;
  } else if (file.mime_type && file.mime_type.startsWith('video/')) {
    inner = `<video src="${src}" controls preload="metadata" style="max-width:85vw;max-height:85vh;display:block"></video>`;
  } else if (file.mime_type === 'application/pdf') {
    inner = `<iframe src="${src}" title="${esc(file.name)}"></iframe>`;
  }
  content.innerHTML = `<button class="modal-close" id="modal-close"><svg width="12" height="12"><use href="#i-close"/></svg></button>${inner}`;
  bg.classList.add('show');
  document.getElementById('modal-close').onclick = closeModal;
}
function closeModal() { document.getElementById('modal-bg').classList.remove('show'); }
document.getElementById('modal-bg').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeModal();
});

async function doDownload(file) {
  if (file.encrypted) {
    try {
      const blob = await decryptedBlob(file);
      downloadBlob(blob, file.name);
    } catch (err) {
      toast(err.message || 'Decrypt failed', true);
    }
    return;
  }
  beginTransfer();
  const a = document.createElement('a');
  a.href = `/api/files/${file.id}/download`;
  a.download = file.name;
  a.click();
  setTimeout(endTransfer, 3000);
}

async function doDelete(id) {
  if (!confirm('確定要刪除這個檔案嗎？')) return;
  const r = await fetch(`/api/files/${id}`, { method: 'DELETE' });
  if (r.ok) { toast('已刪除'); loadFiles(); }
  else { toast('刪除失敗', true); }
}

async function renameFile(file) {
  const name = prompt('新的檔案名稱', file.name);
  if (name === null) return;
  const clean = name.trim();
  if (!clean || clean === file.name) return;
  const r = await fetch(`/api/files/${file.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: clean }),
  });
  if (r.ok) {
    toast('已重新命名');
    loadFiles();
  } else {
    try { toast((await r.json()).detail || '重新命名失敗', true); }
    catch { toast('重新命名失敗', true); }
  }
}

function updateBulkBar() {
  const bar = document.getElementById('bulk-bar');
  const n = state.selected.size + state.selectedFolders.size;
  document.getElementById('bulk-count').textContent = `已選 ${n} 個`;
  bar.classList.toggle('show', n > 0);
}

function renderPathbar() {
  const bar = document.getElementById('pathbar');
  const inFolder = state.folderStack.length > 0;
  const back = inFolder
    ? `<button class="back-btn" id="back-btn" title="返回上一層"><svg width="13" height="13"><use href="#i-back"/></svg> 返回</button>`
    : '';
  const parts = [back, `<button data-folder-jump="-1">Root</button>`];
  state.folderStack.forEach((folder, idx) => {
    parts.push(`<span>/</span><button data-folder-jump="${idx}">${esc(folder.name)}</button>`);
  });
  bar.innerHTML = parts.join('');
  const backBtn = document.getElementById('back-btn');
  if (backBtn) backBtn.addEventListener('click', goBack);
}

function goBack() {
  if (!state.folderStack.length) return;
  state.folderStack.pop();
  state.folderId = state.folderStack.length ? state.folderStack[state.folderStack.length - 1].id : null;
  state.selected.clear();
  state.selectedFolders.clear();
  loadFiles();
}

document.getElementById('pathbar').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-folder-jump]');
  if (!btn) return;
  const idx = Number(btn.dataset.folderJump);
  if (idx < 0) {
    state.folderId = null;
    state.folderStack = [];
  } else {
    state.folderStack = state.folderStack.slice(0, idx + 1);
    state.folderId = state.folderStack[idx].id;
  }
  state.selected.clear();
  loadFiles();
});

document.getElementById('bulk-cancel').onclick = () => {
  state.selected.clear();
  state.selectedFolders.clear();
  renderFiles();
};

document.getElementById('bulk-download').onclick = async () => {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  const btn = document.getElementById('bulk-download');
  if (btn.disabled) return;
  const selectedFiles = ids.map(id => state.files.find(f => f.id === id)).filter(Boolean);
  if (selectedFiles.some(file => file.encrypted)) {
    btn.disabled = true;
    try {
      for (const file of selectedFiles) await doDownload(file);
      toast(`Downloading ${selectedFiles.length} files`);
    } finally {
      btn.disabled = false;
    }
    return;
  }
  const label = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = 'Preparing...';
  beginTransfer();
  try {
    const r = await fetch('/api/files/bulk-download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids }),
    });
    if (!r.ok) {
      try { toast((await r.json()).detail || 'Download failed', true); }
      catch { toast('Download failed', true); }
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'vault-selection.zip';
    a.click();
    URL.revokeObjectURL(url);
    toast(`Downloading ${ids.length} files`);
  } catch {
    toast('Download failed', true);
  } finally {
    endTransfer();
    btn.disabled = false;
    btn.innerHTML = label;
  }
};

document.getElementById('bulk-del').onclick = async () => {
  const fileIds = Array.from(state.selected);
  const folderIds = Array.from(state.selectedFolders);
  const total = fileIds.length + folderIds.length;
  if (!total) return;
  if (!confirm(`確定要刪除 ${total} 個項目嗎？`)) return;
  let deleted = 0;
  if (fileIds.length) {
    const r = await fetch('/api/files/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: fileIds }),
    });
    if (r.ok) { const d = await r.json(); deleted += d.deleted || 0; }
    else { toast('批次刪除失敗', true); }
  }
  for (const id of folderIds) {
    const r = await fetch(`/api/folders/${id}`, { method: 'DELETE' });
    if (r.ok) deleted++;
  }
  toast(`已刪除 ${deleted} 個項目`);
  state.selected.clear();
  state.selectedFolders.clear();
  loadFiles();
};

async function uploadFile(file) {
  const MAX = 20 * 1024 * 1024;
  if (file.size > MAX) { toast(`${file.name} 超過 20 MB 限制`, true); return; }

  const toastEl = document.getElementById('upload-toast');
  document.getElementById('ut-name').textContent = file.name;
  document.getElementById('ut-prog').style.width = '0%';
  toastEl.classList.add('show');

  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append('file', file);
  if (state.folderId !== null) form.append('folder_id', state.folderId);

  await new Promise((resolve) => {
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        document.getElementById('ut-prog').style.width = `${(e.loaded / e.total * 100).toFixed(0)}%`;
      }
    });
    xhr.onload = () => {
      toastEl.classList.remove('show');
      if (xhr.status === 201) { toast(`${file.name} 上傳成功`); loadFiles(); loadStorage(); }
      else {
        try { toast(JSON.parse(xhr.responseText).detail || '上傳失敗', true); }
        catch { toast('上傳失敗', true); }
      }
      resolve();
    };
    xhr.onerror = () => { toastEl.classList.remove('show'); toast('網路錯誤', true); resolve(); };
    xhr.open('POST', '/api/upload');
    xhr.send(form);
  });
}

async function uploadFiles(files) {
  const queue = Array.from(files);
  const concurrency = 3;
  let next = 0;
  async function worker() {
    while (next < queue.length) {
      const file = queue[next++];
      await uploadFile(file);
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, queue.length) }, () => worker())
  );
  loadFiles();
  loadStorage();
}

function folderPathFromFile(file) {
  const raw = file.relativePath || file.webkitRelativePath || '';
  const parts = raw.split('/').filter(Boolean);
  parts.pop();
  return parts;
}

async function ensureFolderPath(pathParts) {
  if (!pathParts.length) return state.folderId;
  const key = `${state.folderId ?? 'root'}:${pathParts.join('/')}`;
  ensureFolderPath.cache ??= new Map();
  if (ensureFolderPath.cache.has(key)) return ensureFolderPath.cache.get(key);

  const request = (async () => {
    const r = await fetch('/api/folders/ensure', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: pathParts, parent_id: state.folderId }),
    });
    if (!r.ok) {
      try { throw new Error((await r.json()).detail || 'Create folder failed'); }
      catch (err) { throw err instanceof Error ? err : new Error('Create folder failed'); }
    }
    const folder = await r.json();
    return folder.id;
  })();
  ensureFolderPath.cache.set(key, request);
  try {
    return await request;
  } catch (err) {
    ensureFolderPath.cache.delete(key);
    throw err;
  }
}

const ENC_KEY = 'vault-encryption-key';
const ENC_MAGIC = new TextEncoder().encode('TCDENC1');

function bytesToBase64(bytes) {
  let s = '';
  bytes.forEach(b => { s += String.fromCharCode(b); });
  return btoa(s);
}

function base64ToBytes(value) {
  return Uint8Array.from(atob(value), c => c.charCodeAt(0));
}

async function getEncryptionKey() {
  let raw = localStorage.getItem(ENC_KEY);
  if (!raw) {
    const bytes = crypto.getRandomValues(new Uint8Array(32));
    raw = bytesToBase64(bytes);
    localStorage.setItem(ENC_KEY, raw);
  }
  return crypto.subtle.importKey('raw', base64ToBytes(raw), 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function encryptedUploadFile(file) {
  if (!state.settings.encrypt) return { file, originalMime: file.type || 'application/octet-stream', encrypted: false };
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await getEncryptionKey();
  const encrypted = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, await file.arrayBuffer()));
  const payload = new Uint8Array(ENC_MAGIC.length + iv.length + encrypted.length);
  payload.set(ENC_MAGIC, 0);
  payload.set(iv, ENC_MAGIC.length);
  payload.set(encrypted, ENC_MAGIC.length + iv.length);
  return {
    file: new File([payload], file.name, { type: 'application/octet-stream' }),
    originalMime: file.type || 'application/octet-stream',
    encrypted: true,
  };
}

async function decryptPayload(buffer, mimeType) {
  const bytes = new Uint8Array(buffer);
  const header = bytes.slice(0, ENC_MAGIC.length);
  if (!ENC_MAGIC.every((b, i) => header[i] === b)) throw new Error('Missing local encryption header');
  const iv = bytes.slice(ENC_MAGIC.length, ENC_MAGIC.length + 12);
  const data = bytes.slice(ENC_MAGIC.length + 12);
  const key = await getEncryptionKey();
  const plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, data);
  return new Blob([plain], { type: mimeType || 'application/octet-stream' });
}

async function decryptedBlob(file) {
  beginTransfer();
  try {
    const r = await fetch(`/api/files/${file.id}/download`);
    if (!r.ok) throw new Error('Download failed');
    return await decryptPayload(await r.arrayBuffer(), file.mime_type);
  } finally {
    endTransfer();
  }
}

async function decryptedObjectUrl(file) {
  return URL.createObjectURL(await decryptedBlob(file));
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function uploadFileTracked(file, targetFolderId = state.folderId) {
  const MAX = 20 * 1024 * 1024;
  let upload;
  beginTransfer();
  try {
    upload = await encryptedUploadFile(file);
  } catch {
    endTransfer();
    toast(`${file.name} encryption failed`, true);
    return false;
  }
  if (upload.file.size > MAX) {
    endTransfer();
    toast(`${file.name} exceeds the 20 MB limit`, true);
    return false;
  }

  const toastEl = document.getElementById('upload-toast');
  document.getElementById('ut-name').textContent = file.name;
  document.getElementById('ut-prog').style.width = '0%';
  toastEl.classList.add('show');

  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append('file', upload.file);
  form.append('encrypted', String(upload.encrypted));
  if (upload.encrypted) form.append('original_mime_type', upload.originalMime);
  if (targetFolderId !== null) form.append('folder_id', targetFolderId);

  return await new Promise((resolve) => {
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        document.getElementById('ut-prog').style.width = `${(e.loaded / e.total * 100).toFixed(0)}%`;
      }
    });
    xhr.onload = () => {
      endTransfer();
      toastEl.classList.remove('show');
      if (xhr.status === 201) {
        toast(`${file.name} uploaded`);
        resolve(true);
      } else {
        try { toast(JSON.parse(xhr.responseText).detail || 'Upload failed', true); }
        catch { toast('Upload failed', true); }
        resolve(false);
      }
    };
    xhr.onerror = () => { endTransfer(); toastEl.classList.remove('show'); toast('Network error', true); resolve(false); };
    xhr.open('POST', '/api/upload');
    xhr.send(form);
  });
}

async function uploadFilesTracked(files) {
  const queue = Array.from(files);
  if (!queue.length) return;
  ensureFolderPath.cache = new Map();
  const concurrency = 3;
  let next = 0;
  let ok = 0;
  let failed = 0;
  async function worker() {
    while (next < queue.length) {
      const file = queue[next++];
      try {
        const targetFolderId = await ensureFolderPath(folderPathFromFile(file));
        if (await uploadFileTracked(file, targetFolderId)) ok++;
        else failed++;
      } catch (err) {
        toast(err.message || 'Folder upload failed', true);
        failed++;
      }
    }
  }
  await Promise.all(
    Array.from({ length: Math.min(concurrency, queue.length) }, () => worker())
  );
  loadFiles();
  loadStorage();
  if (queue.length > 1) {
    toast(`Upload finished: ${ok} ok, ${failed} failed`, failed > 0);
  }
}

async function readEntryFiles(entry, prefix = '') {
  if (entry.isFile) {
    return await new Promise((resolve) => {
      entry.file((file) => {
        file.relativePath = `${prefix}${file.name}`;
        resolve([file]);
      }, () => resolve([]));
    });
  }
  if (!entry.isDirectory) return [];

  const reader = entry.createReader();
  const batches = [];
  while (true) {
    const entries = await new Promise((resolve) => reader.readEntries(resolve, () => resolve([])));
    if (!entries.length) break;
    batches.push(...entries);
  }

  const nested = await Promise.all(
    batches.map(child => readEntryFiles(child, `${prefix}${entry.name}/`))
  );
  return nested.flat();
}

async function filesFromDrop(dataTransfer) {
  const items = Array.from(dataTransfer.items || []);
  const entries = items
    .map(item => item.webkitGetAsEntry ? item.webkitGetAsEntry() : null)
    .filter(Boolean);
  if (!entries.length) return Array.from(dataTransfer.files || []);

  const nested = await Promise.all(entries.map(entry => readEntryFiles(entry)));
  return nested.flat();
}

function closeUploadMenu() {
  document.getElementById('upload-menu').classList.remove('show');
  document.getElementById('upload-btn').setAttribute('aria-expanded', 'false');
}

document.getElementById('upload-btn').onclick = (e) => {
  e.stopPropagation();
  const menu = document.getElementById('upload-menu');
  const open = menu.classList.toggle('show');
  e.currentTarget.setAttribute('aria-expanded', open ? 'true' : 'false');
};

document.getElementById('upload-menu').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-upload]');
  if (!btn) return;
  document.getElementById(btn.dataset.upload === 'folder' ? 'folder-input' : 'file-input').click();
  closeUploadMenu();
});

document.addEventListener('click', (e) => {
  if (!e.target.closest('.upload-menu-wrap')) closeUploadMenu();
});

document.getElementById('new-folder-btn').onclick = async () => {
  const name = prompt('Folder name');
  if (!name || !name.trim()) return;
  const r = await fetch('/api/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name.trim(), parent_id: state.folderId }),
  });
  if (r.ok) loadFiles();
  else {
    try { toast((await r.json()).detail || 'Create folder failed', true); }
    catch { toast('Create folder failed', true); }
  }
};
document.getElementById('file-input').onchange = async (e) => {
  await uploadFilesTracked(e.target.files);
  e.target.value = '';
};
document.getElementById('folder-input').onchange = async (e) => {
  await uploadFilesTracked(e.target.files);
  e.target.value = '';
};

document.addEventListener('dragover', (e) => { e.preventDefault(); document.getElementById('drop-overlay').classList.add('show'); });
document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) document.getElementById('drop-overlay').classList.remove('show'); });
document.addEventListener('drop', async (e) => {
  e.preventDefault();
  document.getElementById('drop-overlay').classList.remove('show');
  const files = await filesFromDrop(e.dataTransfer);
  await uploadFilesTracked(files);
});

function updateSortHeaders() {
  ['name','type','date','size'].forEach(k => {
    const el = document.getElementById(`h-${k}`);
    if (!el) return;
    const active = state.sort === k;
    el.classList.toggle('sort-active', active);
    const existingSvg = el.querySelector('svg');
    if (existingSvg) existingSvg.remove();
    if (active) {
      const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('width','8'); svg.setAttribute('height','8');
      const use = document.createElementNS('http://www.w3.org/2000/svg','use');
      use.setAttribute('href', state.order === 'asc' ? '#i-arrow-u' : '#i-arrow-d');
      svg.appendChild(use); el.appendChild(svg);
    }
  });
}

document.querySelector('.list-header').addEventListener('click', (e) => {
  if (e.target.closest('.resize-handle')) return;
  const col = e.target.closest('[data-sort]');
  if (!col) return;
  const key = col.dataset.sort;
  if (state.sort === key) state.order = state.order === 'desc' ? 'asc' : 'desc';
  else { state.sort = key; state.order = 'desc'; }
  loadFiles();
});

document.getElementById('tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  tab.classList.add('on');
  state.type = tab.dataset.type;
  state.selected.clear();
  loadFiles();
});

function syncViewButtons() {
  document.querySelectorAll('.view-btn').forEach(btn => {
    btn.classList.toggle('on', btn.dataset.view === state.view);
  });
}

document.getElementById('view-switch').addEventListener('click', (e) => {
  const btn = e.target.closest('[data-view]');
  if (!btn) return;
  state.view = btn.dataset.view;
  localStorage.setItem('vault-view-mode', state.view);
  syncViewButtons();
  renderFiles();
});

syncViewButtons();

let suppressNextRowClick = false;

function rectsOverlap(a, b) {
  return a.left <= b.right && a.right >= b.left && a.top <= b.bottom && a.bottom >= b.top;
}

function setRowSelection(row, selected) {
  const id = parseInt(row.dataset.id);
  if (selected) state.selected.add(id);
  else state.selected.delete(id);
  row.classList.toggle('selected', selected);
  const cb = row.querySelector('[data-cb]');
  if (cb) cb.checked = selected;
}

function setFolderSelection(row, id, selected) {
  if (selected) state.selectedFolders.add(id);
  else state.selectedFolders.delete(id);
  row.classList.toggle('selected', selected);
  const cb = row.querySelector('[data-cb-folder]');
  if (cb) cb.checked = selected;
}

function updateBoxSelection(selectRect, baseSelection, baseFolderSelection, append) {
  document.querySelectorAll('#file-list .file-item').forEach(row => {
    const id = parseInt(row.dataset.id);
    const hit = rectsOverlap(selectRect, row.getBoundingClientRect());
    const selected = append ? baseSelection.has(id) || hit : hit;
    setRowSelection(row, selected);
  });
  document.querySelectorAll('#file-list .folder-item').forEach(row => {
    const id = parseInt(row.dataset.folderId);
    const hit = rectsOverlap(selectRect, row.getBoundingClientRect());
    const selected = append ? baseFolderSelection.has(id) || hit : hit;
    setFolderSelection(row, id, selected);
  });
  updateBulkBar();
}

function installBoxSelection() {
  const box = document.getElementById('selection-box');
  let drag = null;

  document.addEventListener('contextmenu', (e) => {
    if (drag?.moved || e.target.closest('.wrap')) e.preventDefault();
  });

  document.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    if (e.target.closest('button,input,.tab,.list-header,.bulk-bar,.modal-bg,.ctx-menu,.upload-toast,.toast-area')) return;

    closeCtx();
    drag = {
      x: e.clientX,
      y: e.clientY,
      moved: false,
      append: e.ctrlKey || e.metaKey,
      baseSelection: new Set(state.selected),
      baseFolderSelection: new Set(state.selectedFolders),
    };
    if (!drag.append) { state.selected.clear(); state.selectedFolders.clear(); }
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (!drag) return;
    const left = Math.min(drag.x, e.clientX);
    const top = Math.min(drag.y, e.clientY);
    const width = Math.abs(e.clientX - drag.x);
    const height = Math.abs(e.clientY - drag.y);

    if (!drag.moved && width + height < 4) return;
    drag.moved = true;
    suppressNextRowClick = true;
    document.body.classList.add('box-selecting');
    Object.assign(box.style, {
      display: 'block',
      left: `${left}px`,
      top: `${top}px`,
      width: `${width}px`,
      height: `${height}px`,
    });
    updateBoxSelection(
      { left, top, right: left + width, bottom: top + height },
      drag.baseSelection,
      drag.baseFolderSelection,
      drag.append
    );
  });

  window.addEventListener('mouseup', () => {
    if (!drag) return;
    if (!drag.moved) {
      if (!drag.append) {
        state.selected = drag.baseSelection;
        state.selectedFolders = drag.baseFolderSelection;
      }
    }
    drag = null;
    box.style.display = 'none';
    document.body.classList.remove('box-selecting');
    updateBulkBar();
    setTimeout(() => { suppressNextRowClick = false; }, 0);
  });
}

installBoxSelection();

function installColumnResize() {
  const list = document.getElementById('file-list-section');
  const storageKey = 'vault-column-widths';
  const columns = {
    name: { css: '--name-col', min: 180, max: 560 },
    type: { css: '--type-col', min: 72, max: 220 },
    date: { css: '--date-col', min: 92, max: 260 },
    size: { css: '--size-col', min: 72, max: 180 },
  };

  function applySavedWidths() {
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || '{}');
      Object.entries(saved).forEach(([key, width]) => {
        if (columns[key] && Number.isFinite(width)) {
          list.style.setProperty(columns[key].css, `${width}px`);
        }
      });
    } catch {}
  }

  function saveWidth(key, width) {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(storageKey) || '{}'); } catch {}
    saved[key] = width;
    localStorage.setItem(storageKey, JSON.stringify(saved));
  }

  function ensureHandles() {
    Object.keys(columns).forEach(key => {
      const header = document.getElementById(`h-${key}`);
      if (!header || header.querySelector('.resize-handle')) return;
      header.dataset.resizeCol = key;
      const handle = document.createElement('i');
      handle.className = 'resize-handle';
      handle.dataset.resizeHandle = key;
      header.append(handle);
    });
  }

  applySavedWidths();
  ensureHandles();

  document.querySelector('.list-header').addEventListener('mousedown', (e) => {
    const handle = e.target.closest('.resize-handle');
    if (!handle) return;
    const key = handle.dataset.resizeHandle;
    const col = columns[key];
    const header = document.getElementById(`h-${key}`);
    if (!col || !header) return;

    const startX = e.clientX;
    const startWidth = header.getBoundingClientRect().width;
    document.body.classList.add('col-resizing');
    e.preventDefault();
    e.stopPropagation();

    function onMove(ev) {
      const width = Math.max(col.min, Math.min(col.max, Math.round(startWidth + ev.clientX - startX)));
      list.style.setProperty(col.css, `${width}px`);
    }

    function onUp(ev) {
      const width = Math.max(col.min, Math.min(col.max, Math.round(startWidth + ev.clientX - startX)));
      list.style.setProperty(col.css, `${width}px`);
      saveWidth(key, width);
      document.body.classList.remove('col-resizing');
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    }

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });
}

installColumnResize();

let searchTimer;
document.getElementById('search-input').addEventListener('input', (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.q = e.target.value.trim(); loadFiles(); }, 250);
});

function fileItemMarkup(f) {
  const icon = mimeIcon(f.mime_type);
  const sel = state.selected.has(f.id);
  if (state.view === 'list') {
    return `
    <div class="row file-item${sel ? ' selected' : ''}" data-id="${f.id}">
      <input type="checkbox" class="cb" data-cb="${f.id}" ${sel ? 'checked' : ''}>
      <div class="name-cell">
        <div class="ic ${icon}"><svg width="14" height="14"><use href="#i-${icon}"/></svg></div>
        <div>
          <div class="fname">${esc(f.name)}</div>
          <span class="fmeta">${mimeLabel(f.mime_type)}</span>
        </div>
      </div>
      <div class="col dim">${mimeLabel(f.mime_type)}</div>
      <div class="col">${fmtDate(f.uploaded_at)}</div>
      <div class="col size">${fmtSize(f.size)}</div>
      <span style="display:flex;gap:2px;align-items:center;justify-content:flex-end">${favBtnHtml(f,'file')}<button class="more-btn" data-more="${f.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button></span>
    </div>`;
  }

  const thumb = state.view === 'preview' && canUseThumbnail(f)
    ? `<img src="/api/files/${f.id}/thumbnail" alt="${esc(f.name)}" loading="lazy" onerror="this.replaceWith(this.nextElementSibling)"><div class="ic ${icon}"><svg width="18" height="18"><use href="#i-${icon}"/></svg></div>`
    : `<div class="ic ${icon}"><svg width="20" height="20"><use href="#i-${icon}"/></svg></div>`;
  return `
    <div class="file-card file-item${sel ? ' selected' : ''}" data-id="${f.id}">
      <input type="checkbox" class="cb" data-cb="${f.id}" ${sel ? 'checked' : ''}>
      ${favBtnHtml(f,'file')}
      <button class="more-btn" data-more="${f.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button>
      <div class="${state.view === 'preview' ? 'thumb-tile' : 'icon-tile'}">${thumb}</div>
      <div class="card-info">
        <div class="fname">${esc(f.name)}</div>
        <span class="fmeta">${fmtSize(f.size)} · ${mimeLabel(f.mime_type)}</span>
      </div>
    </div>`;
}

function folderItemMarkup(folder) {
  const sel = state.selectedFolders.has(folder.id);
  if (state.view === 'list') {
    return `
    <div class="row folder-row folder-item${sel ? ' selected' : ''}" data-folder-id="${folder.id}">
      <input type="checkbox" class="cb" data-cb-folder="${folder.id}" ${sel ? 'checked' : ''}>
      <div class="name-cell">
        <div class="ic folder"><svg width="14" height="14"><use href="#i-folder"/></svg></div>
        <div>
          <div class="fname">${esc(folder.name)}</div>
          <span class="fmeta">Folder</span>
        </div>
      </div>
      <div class="col dim">FOLDER</div>
      <div class="col">${fmtDate(folder.created_at)}</div>
      <div class="col size">--</div>
      <span style="display:flex;gap:2px;align-items:center;justify-content:flex-end">${favBtnHtml(folder,'folder')}<button class="more-btn" data-more-folder="${folder.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button></span>
    </div>`;
  }
  return `
    <div class="file-card folder-card folder-item${sel ? ' selected' : ''}" data-folder-id="${folder.id}">
      <input type="checkbox" class="cb" data-cb-folder="${folder.id}" ${sel ? 'checked' : ''}>
      ${favBtnHtml(folder,'folder')}
      <button class="more-btn" data-more-folder="${folder.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button>
      <div class="${state.view === 'preview' ? 'thumb-tile' : 'icon-tile'}">
        <div class="ic folder"><svg width="20" height="20"><use href="#i-folder"/></svg></div>
      </div>
      <div class="card-info">
        <div class="fname">${esc(folder.name)}</div>
        <span class="fmeta">Folder</span>
      </div>
    </div>`;
}

function attachFolderHandlers(list) {
  list.querySelectorAll('[data-folder-id]').forEach(item => {
    const id = parseInt(item.dataset.folderId);
    const folder = state.folders.find(f => f.id === id);
    if (!folder) return;

    const cb = item.querySelector('[data-cb-folder]');
    if (cb) {
      cb.addEventListener('click', (e) => {
        e.stopPropagation();
        const nowSel = !state.selectedFolders.has(id);
        setFolderSelection(item, id, nowSel);
        updateBulkBar();
      });
    }

    const moreBtn = item.querySelector('[data-more-folder]');
    if (moreBtn) {
      moreBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        showCtx(e, { kind: 'folder', folder });
      });
    }

    item.addEventListener('click', (e) => {
      if (suppressNextRowClick) return;
      if (e.target.closest('[data-cb-folder]') || e.target.closest('[data-more-folder]')) return;
      const totalSel = state.selected.size + state.selectedFolders.size;
      if (totalSel > 0) {
        const nowSel = !state.selectedFolders.has(id);
        setFolderSelection(item, id, nowSel);
        updateBulkBar();
      } else {
        state.folderId = folder.id;
        state.folderStack.push({ id: folder.id, name: folder.name });
        state.selected.clear();
        state.selectedFolders.clear();
        loadFiles();
      }
    });
  });
}

function renderFiles() {
  const list = document.getElementById('file-list');
  let displayFiles = state.files;
  let displayFolders = state.folders;
  if (state.type === 'favorite') {
    displayFiles = state.files.filter(f => state.favs.has(`file:${f.id}`));
    displayFolders = state.folders.filter(d => state.favs.has(`folder:${d.id}`));
  }
  if (displayFiles.length === 0 && displayFolders.length === 0) {
    list.className = '';
    list.innerHTML = '<div class="empty">沒有檔案</div>';
    updateBulkBar();
    renderPathbar();
    return;
  }
  list.className = state.view === 'list' ? '' : `file-grid ${state.view === 'preview' ? 'preview-grid' : ''}`;
  list.innerHTML = displayFolders.map(folderItemMarkup).join('') + displayFiles.map(fileItemMarkup).join('');
  attachFolderHandlers(list);

  list.querySelectorAll('.file-item').forEach(row => {
    const id = parseInt(row.dataset.id);
    const file = displayFiles.find(f => f.id === id);

    row.querySelector('[data-cb]').addEventListener('click', (e) => {
      e.stopPropagation();
      if (state.selected.has(id)) state.selected.delete(id);
      else state.selected.add(id);
      row.classList.toggle('selected', state.selected.has(id));
      e.target.checked = state.selected.has(id);
      updateBulkBar();
    });

    row.querySelector('[data-more]').addEventListener('click', (e) => {
      e.stopPropagation();
      openCtx(e, file);
    });

    row.addEventListener('click', (e) => {
      if (suppressNextRowClick) return;
      if (e.target.closest('[data-cb]') || e.target.closest('[data-more]')) return;
      if (state.selected.size > 0) {
        if (state.selected.has(id)) state.selected.delete(id);
        else state.selected.add(id);
        row.classList.toggle('selected', state.selected.has(id));
        updateBulkBar();
      } else if (isPreviewable(file.mime_type)) {
        showPreview(file);
      } else {
        doDownload(file);
      }
    });
  });

  updateBulkBar();
  updateSortHeaders();
  renderPathbar();
}

function updateCounts(files) {
  document.getElementById('cnt-all').textContent = files.length;
  document.getElementById('cnt-img').textContent = files.filter(f => f.mime_type?.startsWith('image/')).length;
  document.getElementById('cnt-vid').textContent = files.filter(f => f.mime_type?.startsWith('video/')).length;
  document.getElementById('cnt-doc').textContent = files.filter(f => f.mime_type?.startsWith('application/')).length;
  const favEl = document.getElementById('cnt-fav');
  if (favEl) favEl.textContent = files.filter(f => state.favs.has(`file:${f.id}`)).length + state.folders.filter(d => state.favs.has(`folder:${d.id}`)).length;
}

async function loadStorage() {
  try {
    const r = await fetch('/api/storage');
    const data = await r.json();
    const used = data.used_bytes;
    let label;
    if (used < 1024 ** 2) label = `${(used / 1024).toFixed(1)}<small> KB</small>`;
    else if (used < 1024 ** 3) label = `${(used / 1024 ** 2).toFixed(2)}<small> MB</small>`;
    else label = `${(used / 1024 ** 3).toFixed(2)}<small> GB</small>`;
    document.getElementById('storage-num').innerHTML = label;
    const QUOTA = 2 * 1024 ** 3; // 2 GB visual quota
    const pct = Math.min((data.used_bytes / QUOTA) * 100, 95);
    document.getElementById('storage-bar').style.width = `${pct.toFixed(1)}%`;
  } catch {}
}

async function loadFiles() {
  const params = new URLSearchParams({ sort: state.sort, order: state.order });
  if (state.q) params.set('q', state.q);
  if (state.type) params.set('type', state.type);
  if (state.folderId !== null) params.set('folder_id', state.folderId);
  const folderParams = new URLSearchParams();
  if (state.folderId !== null) folderParams.set('parent_id', state.folderId);

  try {
    const [r, folderRes] = await Promise.all([
      fetch(`/api/files?${params}`),
      fetch(`/api/folders?${folderParams}`),
    ]);
    state.files = await r.json();
    state.folders = await folderRes.json();
    if (state.type || state.q) {
      const all = await fetch('/api/files');
      state.allFiles = await all.json();
    } else {
      state.allFiles = state.files;
    }
    updateCounts(state.allFiles);
    renderFiles();
    document.getElementById('sync-status').textContent = `已同步 · 剛才`;
  } catch {
    toast('無法連線到伺服器', true);
  }
}

let ctxBusy = false;
let ctxTarget = null;

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast('Copied');
  } catch {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.append(ta);
    ta.select();
    document.execCommand('copy');
    ta.remove();
    toast('Copied');
  }
}

function expirySeconds() {
  const map = { '24h': 24 * 3600, '7d': 7 * 24 * 3600, never: null };
  return map[state.settings.expiry] ?? 24 * 3600;
}

async function fileDownloadUrl(file) {
  const r = await fetch(`/api/files/${file.id}/share`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ expires_in_seconds: expirySeconds() }),
  });
  if (!r.ok) throw new Error('Create share link failed');
  const data = await r.json();
  return data.url;
}

function folderPathLabel(folder = null) {
  const parts = ['Root', ...state.folderStack.map(f => f.name)];
  if (folder) parts.push(folder.name);
  return parts.join(' / ');
}

function enterFolder(folder) {
  state.folderId = folder.id;
  state.folderStack.push({ id: folder.id, name: folder.name });
  state.selected.clear();
  loadFiles();
}

async function promptCreateFolder(parentId = state.folderId) {
  const name = prompt('Folder name');
  if (!name || !name.trim()) return;
  const r = await fetch('/api/folders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: name.trim(), parent_id: parentId }),
  });
  if (r.ok) loadFiles();
  else {
    try { toast((await r.json()).detail || 'Create folder failed', true); }
    catch { toast('Create folder failed', true); }
  }
}

async function deleteSelectedFiles() {
  const fileIds = Array.from(state.selected);
  const folderIds = Array.from(state.selectedFolders);
  const total = fileIds.length + folderIds.length;
  if (!total) return;
  if (!confirm(`Delete ${total} selected item(s)?`)) return;
  let deleted = 0;
  if (fileIds.length) {
    const r = await fetch('/api/files/bulk-delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: fileIds }),
    });
    if (r.ok) { const d = await r.json(); deleted += d.deleted || 0; }
  }
  for (const id of folderIds) {
    const r = await fetch(`/api/folders/${id}`, { method: 'DELETE' });
    if (r.ok) deleted++;
  }
  toast(`Deleted ${deleted} item(s)`);
  state.selected.clear();
  state.selectedFolders.clear();
  loadFiles();
}

async function deleteFolder(folder) {
  if (!folder) return;
  if (!confirm(`Delete folder "${folder.name}" and all contents?`)) return;
  const r = await fetch(`/api/folders/${folder.id}`, { method: 'DELETE' });
  if (r.ok) {
    const data = await r.json();
    toast(`Deleted ${data.deleted_folders} folder(s), ${data.deleted_files} file(s)`);
    state.selected.clear();
    if (state.folderId === folder.id || state.folderStack.some(f => f.id === folder.id)) {
      state.folderId = null;
      state.folderStack = [];
    }
    loadFiles();
  } else {
    try { toast((await r.json()).detail || 'Folder delete failed', true); }
    catch { toast('Folder delete failed', true); }
  }
}

function closeCtx() {
  const menu = document.getElementById('ctx-menu');
  menu.style.display = 'none';
  menu.innerHTML = '';
  ctxOpen = false;
  ctxBusy = false;
  ctxTarget = null;
}

function renderCtxButton(action) {
  const danger = action.danger ? ' danger' : '';
  return `<button data-action="${action.key}" class="${danger.trim()}"><svg width="12" height="12"><use href="${action.icon}"/></svg> ${action.label}</button>`;
}

function showCtx(e, target) {
  const menu = document.getElementById('ctx-menu');
  let actions = [];
  ctxTarget = target;

  if (target?.kind === 'file') {
    const file = target.file;
    if (isPreviewable(file.mime_type)) {
      actions.push({ key: 'preview', icon: '#i-eye', label: '預覽' });
    }
    actions.push(
      { key: 'download', icon: '#i-down', label: '下載' },
      { key: 'rename-file', icon: '#i-doc', label: '重新命名' },
      { key: 'copy-link', icon: '#i-copy', label: '複製連結' },
      { sep: true },
      { key: 'delete-file', icon: '#i-trash', label: '刪除', danger: true }
    );
  } else if (target?.kind === 'selection') {
    actions.push({ key: 'create-folder', icon: '#i-folder', label: '新增資料夾' });
    const totalSel = state.selected.size + state.selectedFolders.size;
    if (totalSel > 0) {
      actions.push(
        { key: 'download-selected', icon: '#i-down', label: '下載選取' },
        { key: 'copy-selected', icon: '#i-copy', label: '複製連結' },
        { sep: true },
        { key: 'delete-selected', icon: '#i-trash', label: '刪除選取', danger: true }
      );
    }
  } else if (target?.kind === 'folder') {
    const folder = target.folder;
    actions.push(
      { key: 'open-folder', icon: '#i-folder', label: '開啟' },
      { key: 'create-folder', icon: '#i-folder', label: '創資料夾' },
      { key: 'copy-path', icon: '#i-copy', label: '複製路徑' },
      { sep: true },
      { key: 'delete-folder', icon: '#i-trash', label: '刪除資料夾', danger: true }
    );
  } else {
    actions.push({ key: 'create-folder', icon: '#i-folder', label: '創資料夾' });
    if (state.selected.size > 0) {
      actions.push(
        { key: 'download-selected', icon: '#i-down', label: '下載選取' },
        { key: 'copy-selected', icon: '#i-copy', label: '複製連結' },
        { sep: true },
        { key: 'delete-selected', icon: '#i-trash', label: '刪除選取', danger: true }
      );
    }
  }

  menu.innerHTML = actions.map(a => a.sep ? '<div class="sep"></div>' : renderCtxButton(a)).join('');
  if (!menu.innerHTML) return;
  menu.style.display = 'block';
  const x = Math.min(e.clientX, window.innerWidth - 220);
  const y = Math.min(e.clientY, window.innerHeight - (actions.length * 38 + 16));
  menu.style.left = `${Math.max(8, x)}px`;
  menu.style.top = `${Math.max(8, y)}px`;
  ctxOpen = true;
}

async function runCtxAction(action) {
  if (ctxBusy) return;
  ctxBusy = true;
  try {
    if (action === 'preview' && ctxTarget?.kind === 'file') showPreview(ctxTarget.file);
    else if (action === 'download' && ctxTarget?.kind === 'file') doDownload(ctxTarget.file);
    else if (action === 'rename-file' && ctxTarget?.kind === 'file') await renameFile(ctxTarget.file);
    else if (action === 'copy-link' && ctxTarget?.kind === 'file') await copyText(await fileDownloadUrl(ctxTarget.file));
    else if (action === 'delete-file' && ctxTarget?.kind === 'file') await doDelete(ctxTarget.file.id);
    else if (action === 'open-folder' && ctxTarget?.kind === 'folder') enterFolder(ctxTarget.folder);
    else if (action === 'create-folder') await promptCreateFolder(ctxTarget?.kind === 'folder' ? ctxTarget.folder.id : state.folderId);
    else if (action === 'copy-path' && ctxTarget?.kind === 'folder') await copyText(folderPathLabel(ctxTarget.folder));
    else if (action === 'delete-folder' && ctxTarget?.kind === 'folder') await deleteFolder(ctxTarget.folder);
    else if (action === 'download-selected') document.getElementById('bulk-download').click();
    else if (action === 'copy-selected') {
      const links = (await Promise.all(
        Array.from(state.selected)
          .map(id => state.files.find(f => f.id === id))
          .filter(Boolean)
          .map(file => fileDownloadUrl(file))
      )).join('\n');
      if (links) await copyText(links);
    } else if (action === 'delete-selected') await deleteSelectedFiles();
  } finally {
    closeCtx();
  }
}

document.getElementById('ctx-menu').addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  e.preventDefault();
  e.stopPropagation();
  await runCtxAction(btn.dataset.action);
});

document.getElementById('file-list').addEventListener('contextmenu', (e) => {
  e.preventDefault();
  if (e.target.closest('.ctx-menu')) return;
  const fileEl = e.target.closest('.file-item');
  const folderEl = e.target.closest('[data-folder-id]');
  if (fileEl) {
    const id = parseInt(fileEl.dataset.id);
    const file = state.files.find(f => f.id === id);
    if (!file) return;
    const totalSel = state.selected.size + state.selectedFolders.size;
    if (totalSel > 0 && state.selected.has(id)) showCtx(e, { kind: 'selection' });
    else showCtx(e, { kind: 'file', file });
    return;
  }
  if (folderEl) {
    const id = parseInt(folderEl.dataset.folderId);
    const folder = state.folders.find(f => f.id === id);
    if (!folder) return;
    const totalSel = state.selected.size + state.selectedFolders.size;
    if (totalSel > 0 && state.selectedFolders.has(id)) showCtx(e, { kind: 'selection' });
    else showCtx(e, { kind: 'folder', folder });
    return;
  }
  if (e.target.closest('#file-list')) showCtx(e, { kind: 'background' });
});

function openCtx(e, file) {
  showCtx(e, { kind: 'file', file });
}

// ===== Favorites =====
const FAV_KEY = 'vault-favorites';
function loadFavs() { try { return new Set(JSON.parse(localStorage.getItem(FAV_KEY) || '[]')); } catch { return new Set(); } }
function saveFavs(set) { localStorage.setItem(FAV_KEY, JSON.stringify([...set])); }
state.favs = loadFavs();

function favKey(item, kind) { return `${kind}:${item.id}`; }

function toggleFav(item, kind, btn) {
  const k = favKey(item, kind);
  const on = !state.favs.has(k);
  if (on) state.favs.add(k); else state.favs.delete(k);
  saveFavs(state.favs);
  if (btn) {
    btn.classList.toggle('is-fav', on);
    btn.classList.remove('pop');
    void btn.offsetWidth;
    if (on) btn.classList.add('pop');
    setTimeout(() => btn.classList.remove('pop'), 600);
  }
  if (state.type === 'favorite') renderFiles();
  updateCounts(state.allFiles.length ? state.allFiles : state.files);
  toast(on ? '加入最愛' : '已移除');
}

function favBtnHtml(item, kind) {
  const on = state.favs && state.favs.has(favKey(item, kind));
  return `<button class="fav-btn${on ? ' is-fav' : ''}" data-fav="${kind}:${item.id}" title="加入最愛"><svg width="14" height="14"><use href="#i-star"/></svg><span class="fav-spark"><i></i><i></i><i></i><i></i><i></i><i></i></span></button>`;
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-fav]');
  if (!btn) return;
  e.stopPropagation();
  const [kind, idStr] = btn.dataset.fav.split(':');
  const id = parseInt(idStr);
  const item = kind === 'folder' ? state.folders.find(f => f.id === id) : state.files.find(f => f.id === id);
  if (item) toggleFav(item, kind, btn);
}, true);

// ===== Settings =====
const SETTINGS_KEY = 'vault-settings';
const defaultSettings = {
  botToken: '', chatId: '', apiBaseUrl: 'https://api.telegram.org',
  theme: 'dark',
  encrypt: true, expiry: '24h', shareBaseUrl: '', idleTimeoutSeconds: 900
};
function loadSettings() { try { return { ...defaultSettings, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}') }; } catch { return { ...defaultSettings }; } }
function saveSettings(s) { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); }
state.settings = loadSettings();

function applyTheme(theme) {
  let resolved = theme;
  if (theme === 'auto') {
    resolved = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  document.documentElement.dataset.theme = resolved;
}

function applySettings(s) {
  document.getElementById('set-bot-token').value = s.botToken;
  document.getElementById('set-chat-id').value = s.chatId;
  document.getElementById('set-api-base-url').value = s.apiBaseUrl;
  document.querySelectorAll('#set-theme button').forEach(b => b.classList.toggle('on', b.dataset.v === s.theme));
  document.querySelectorAll('#set-expiry button').forEach(b => b.classList.toggle('on', b.dataset.v === s.expiry));
  document.querySelectorAll('#set-share-base button').forEach(b => {
    const value = s.shareBaseUrl ? 'localhost' : '';
    b.classList.toggle('on', b.dataset.v === value);
  });
  document.querySelectorAll('#set-idle-timeout button').forEach(b => {
    b.classList.toggle('on', Number(b.dataset.v) === Number(s.idleTimeoutSeconds));
  });
  ['encrypt'].forEach(k => {
    const t = document.querySelector(`[data-toggle-key="${k}"]`);
    if (t) t.classList.toggle('on', !!s[k]);
  });
  applyTheme(s.theme);
}

async function loadTelegramSettings() {
  try {
    const r = await fetch('/api/settings/telegram');
    if (!r.ok) return;
    const data = await r.json();
    state.settings.botToken = data.bot_token || '';
    state.settings.chatId = data.chat_id || state.settings.chatId;
    state.settings.apiBaseUrl = data.api_base_url || state.settings.apiBaseUrl;
  } catch {}
}

async function loadSyncSettings() {
  try {
    const r = await fetch('/api/settings/sync');
    if (!r.ok) return;
    const data = await r.json();
    document.getElementById('set-tg-api-id').value = data.api_id || '';
    const hint = document.getElementById('sync-session-hint');
    if (data.has_session) hint.textContent = '✓ Session 已儲存，同步時可直接使用。';
    else if (data.api_id_set) hint.textContent = '尚未建立 Session，首次同步時會自動產生並儲存。';
    else hint.textContent = '';
  } catch {}
}

async function loadShareSettings() {
  try {
    const r = await fetch('/api/settings/share');
    if (!r.ok) return;
    const data = await r.json();
    state.settings.shareBaseUrl = data.base_url || '';
  } catch {}
}

async function loadServerSettings() {
  try {
    const r = await fetch('/api/settings/server');
    if (!r.ok) return;
    const data = await r.json();
    state.settings.idleTimeoutSeconds = data.idle_timeout_seconds;
  } catch {}
}

async function openSettings() {
  await Promise.all([loadTelegramSettings(), loadShareSettings(), loadServerSettings(), loadSyncSettings()]);
  applySettings(state.settings);
  document.getElementById('settings-bg').classList.add('show');
}
function closeSettings() { document.getElementById('settings-bg').classList.remove('show'); }

document.getElementById('settings-btn').onclick = openSettings;
document.getElementById('settings-close').onclick = closeSettings;
document.getElementById('set-cancel').onclick = closeSettings;
document.getElementById('settings-bg').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeSettings(); });

document.querySelectorAll('.seg-pill').forEach(pill => {
  pill.addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-v]');
    if (!btn) return;
    pill.querySelectorAll('button').forEach(b => b.classList.toggle('on', b === btn));
  });
});
document.querySelectorAll('[data-toggle-key]').forEach(t => {
  t.addEventListener('click', () => t.classList.toggle('on'));
});
document.querySelectorAll('[data-toggle]').forEach(b => {
  b.addEventListener('click', () => {
    const id = b.dataset.toggle;
    const inp = document.getElementById(id);
    if (!inp) return;
    inp.type = inp.type === 'password' ? 'text' : 'password';
    b.querySelector('use').setAttribute('href', inp.type === 'password' ? '#i-eye' : '#i-eye-off');
  });
});

document.getElementById('set-test').onclick = async () => {
  const conn = document.getElementById('set-conn');
  conn.className = 'conn-status'; conn.textContent = '測試中…';
  try {
    const r = await fetch('/api/settings/telegram/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        bot_token: document.getElementById('set-bot-token').value.trim(),
        chat_id: document.getElementById('set-chat-id').value.trim(),
        api_base_url: document.getElementById('set-api-base-url').value.trim() || 'https://api.telegram.org',
      }),
    });
    if (!r.ok) throw new Error((await r.json()).detail || '連線失敗');
    const data = await r.json();
    conn.className = 'conn-status ok';
    conn.textContent = `連線成功 · ${data.bot || 'bot'}`;
  } catch (err) {
    conn.className = 'conn-status err';
    conn.textContent = err.message || '連線失敗';
  }
};

document.getElementById('set-save').onclick = async () => {
  const s = { ...state.settings };
  s.botToken = document.getElementById('set-bot-token').value.trim();
  s.chatId = document.getElementById('set-chat-id').value.trim();
  s.apiBaseUrl = document.getElementById('set-api-base-url').value.trim() || 'https://api.telegram.org';
  s.theme = document.querySelector('#set-theme button.on')?.dataset.v || 'dark';
  s.expiry = document.querySelector('#set-expiry button.on')?.dataset.v || '24h';
  s.shareBaseUrl = document.querySelector('#set-share-base button.on')?.dataset.v === 'localhost'
    ? `${location.protocol}//localhost:${location.port || (location.protocol === 'https:' ? '443' : '80')}`
    : '';
  s.idleTimeoutSeconds = Number(document.querySelector('#set-idle-timeout button.on')?.dataset.v || 900);
  ['encrypt'].forEach(k => {
    const t = document.querySelector(`[data-toggle-key="${k}"]`);
    s[k] = !!(t && t.classList.contains('on'));
  });

  if (s.botToken && s.chatId) {
    const r = await fetch('/api/settings/telegram', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bot_token: s.botToken, chat_id: s.chatId, api_base_url: s.apiBaseUrl }),
    });
    if (!r.ok) {
      try { toast((await r.json()).detail || 'Telegram 設定儲存失敗', true); }
      catch { toast('Telegram 設定儲存失敗', true); }
      return;
    }
  }
  const shareRes = await fetch('/api/settings/share', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ base_url: s.shareBaseUrl }),
  });
  if (!shareRes.ok) {
    try { toast((await shareRes.json()).detail || '分享設定儲存失敗', true); }
    catch { toast('分享設定儲存失敗', true); }
    return;
  }
  const serverRes = await fetch('/api/settings/server', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ idle_timeout_seconds: s.idleTimeoutSeconds }),
  });
  if (!serverRes.ok) {
    try { toast((await serverRes.json()).detail || '伺服器設定儲存失敗', true); }
    catch { toast('伺服器設定儲存失敗', true); }
    return;
  }
  const syncApiId = document.getElementById('set-tg-api-id').value.trim();
  const syncApiHash = document.getElementById('set-tg-api-hash').value.trim();
  if (syncApiId && syncApiHash) {
    const syncRes = await fetch('/api/settings/sync', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_id: syncApiId, api_hash: syncApiHash }),
    });
    if (!syncRes.ok) {
      try { toast((await syncRes.json()).detail || '同步設定儲存失敗', true); }
      catch { toast('同步設定儲存失敗', true); }
      return;
    }
  }
  state.settings = s;
  saveSettings(s);
  applyTheme(s.theme);
  closeSettings();
  toast('設定已儲存');
};

async function requestServerShutdown({ clearLocalKeys = false } = {}) {
  if (state.activeTransfers > 0 && !confirm(`目前仍有 ${state.activeTransfers} 個上傳或下載工作進行中。仍要關閉伺服器嗎？`)) return;
  if (!confirm('確認關閉伺服器？目前連線會中斷。')) return;
  if (clearLocalKeys) {
    if (!confirm('清除本機加密金鑰？舊的加密檔案將無法在此瀏覽器解密。')) return;
    localStorage.removeItem(ENC_KEY);
    localStorage.removeItem(SETTINGS_KEY);
  }
  try {
    await fetch('/api/server/shutdown', { method: 'POST' });
    document.body.innerHTML = `<div class="empty" style="min-height:100vh;display:grid;place-items:center">${clearLocalKeys ? '已登出並關閉伺服器' : '正在關閉伺服器'}</div>`;
  } catch {
    toast('伺服器關閉失敗', true);
  }
}

document.getElementById('shutdown-btn').onclick = () => {
  requestServerShutdown();
};

document.getElementById('set-clear-key').onclick = async () => {
  await requestServerShutdown({ clearLocalKeys: true });
};

applyTheme(state.settings.theme);

// ===== Telegram Sync =====
document.getElementById('sync-btn').onclick = async () => {
  const btn = document.getElementById('sync-btn');
  const svg = btn.querySelector('svg');
  if (btn.disabled) return;
  btn.disabled = true;
  svg.style.animation = 'spin 1s linear infinite';
  document.getElementById('sync-status').textContent = '同步中…';
  try {
    const r = await fetch('/api/sync', { method: 'POST' });
    const data = await r.json();
    if (!r.ok) {
      toast(data.detail || '同步失敗', true);
      document.getElementById('sync-status').textContent = '同步失敗';
      return;
    }
    const parts = [];
    if (data.imported > 0) parts.push(`匯入 ${data.imported} 個`);
    if (data.deleted > 0) parts.push(`刪除 ${data.deleted} 個`);
    if (parts.length === 0) parts.push('無變更');
    toast(`同步完成：${parts.join('，')}`);
    document.getElementById('sync-status').textContent = `同步完成 · ${parts.join('，')}`;
    if (data.imported > 0 || data.deleted > 0) { loadFiles(); loadStorage(); }
  } catch {
    toast('同步失敗：網路錯誤', true);
    document.getElementById('sync-status').textContent = '同步失敗';
  } finally {
    btn.disabled = false;
    svg.style.animation = '';
  }
};

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (document.getElementById('settings-bg').classList.contains('show')) { closeSettings(); return; }
    closeModal();
  }
  if (e.key === 'Backspace' && state.folderStack.length && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
    goBack();
  }
});

loadFiles();
loadStorage();

// SSE — auto-refresh when the sync listener imports or deletes files
(function connectSSE() {
  const es = new EventSource('/api/events');
  es.addEventListener('sync', (e) => {
    const data = JSON.parse(e.data);
    if (data.imported > 0 || data.deleted > 0) {
      loadFiles();
      loadStorage();
    }
  });
  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 5000);
  };
}());
