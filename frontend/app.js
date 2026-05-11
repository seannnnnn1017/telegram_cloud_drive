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
  expandedFolders: new Set(),
  folderChildren: new Map(),
  visibleFiles: [],
  visibleFolders: [],
  clipboard: null,
  previewingIds: new Set(),
  previewRequestId: 0,
  previewObjectUrl: null,
  previewProgressId: null,
  deleteSelectedBusy: false,
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
  if (bytes < 1024) return `${bytes}<span class="unit">B</span>`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)}<span class="unit">KB</span>`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(2)}<span class="unit">MB</span>`;
  return `${(bytes / 1024 ** 3).toFixed(2)}<span class="unit">GB</span>`;
}

function fmtDate(iso) {
  const d = new Date(iso), now = new Date();
  const diff = (now - d) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 172800) return '昨天';
  return d.toLocaleDateString('zh-TW', { month: 'short', day: 'numeric' });
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

function indentRail(depth) {
  if (!depth) return '';
  let html = '<div class="indent-rail">';
  for (let i = 0; i < depth; i++) html += '<div class="rail"></div>';
  return html + '</div>';
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

const uploadProgress = new Map();
let uploadProgressSeq = 0;

function updateUploadToastVisibility() {
  const toastEl = document.getElementById('upload-toast');
  toastEl.classList.toggle('show', uploadProgress.size > 0);
}

function createUploadProgress(name) {
  const id = `upload-${++uploadProgressSeq}`;
  const toastEl = document.getElementById('upload-toast');
  let list = document.getElementById('upload-progress-list');
  if (!list) {
    list = document.createElement('div');
    list.className = 'ut-list';
    list.id = 'upload-progress-list';
    toastEl.append(list);
  }
  const item = document.createElement('div');
  item.className = 'ut-item';
  item.dataset.uploadId = id;
  item.innerHTML = `
    <div class="ut-meta">
      <div class="ut-name"></div>
      <div class="ut-pct">0%</div>
    </div>
    <div class="ut-bar"><i style="width:0%"></i></div>
  `;
  const nameEl = item.querySelector('.ut-name');
  if (nameEl) nameEl.textContent = name;
  list.append(item);
  uploadProgress.set(id, item);
  updateUploadToastVisibility();
  return id;
}

function setUploadProgress(id, percent) {
  const item = uploadProgress.get(id);
  if (!item) return;
  const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
  item.querySelector('.ut-bar i').style.width = `${safePercent}%`;
  item.querySelector('.ut-pct').textContent = `${safePercent}%`;
}

function finishUploadProgress(id) {
  const item = uploadProgress.get(id);
  if (!item) return;
  setUploadProgress(id, 100);
  uploadProgress.delete(id);
  setTimeout(() => {
    item.remove();
    updateUploadToastVisibility();
  }, 350);
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
  if (state.previewProgressId || state.previewingIds.has(file.id)) return;
  state.previewingIds.add(file.id);
  const requestId = ++state.previewRequestId;
  const bg = document.getElementById('modal-bg');
  const content = document.getElementById('modal-content');
  const progressId = createUploadProgress(`Opening ${file.name}`);
  state.previewProgressId = progressId;
  let inner = '';
  let src = `/api/files/${file.id}/preview`;
  setUploadProgress(progressId, 8);
  bg.classList.add('show');
  content.setAttribute('aria-busy', 'true');
  content.innerHTML = `
    <button class="modal-close" id="modal-close"><svg width="12" height="12"><use href="#i-close"/></svg></button>
    <div class="preview-loading">
      <div class="preview-label">Opening preview...</div>
      <div class="preview-progress"><i></i></div>
    </div>
  `;
  document.getElementById('modal-close').onclick = closeModal;
  try {
    if (file.encrypted) {
      src = await decryptedObjectUrl(file);
      if (state.previewObjectUrl) URL.revokeObjectURL(state.previewObjectUrl);
      state.previewObjectUrl = src;
    }
    setUploadProgress(progressId, 55);
    if (requestId !== state.previewRequestId) return;
    if (file.mime_type && file.mime_type.startsWith('image/')) {
      inner = `<img src="${src}" alt="${esc(file.name)}">`;
    } else if (file.mime_type && file.mime_type.startsWith('video/')) {
      inner = `<video src="${src}" controls preload="metadata" style="max-width:85vw;max-height:85vh;display:block"></video>`;
    } else if (file.mime_type === 'application/pdf') {
      inner = `<iframe src="${src}" title="${esc(file.name)}"></iframe>`;
    }
    content.innerHTML = `
      <button class="modal-close" id="modal-close"><svg width="12" height="12"><use href="#i-close"/></svg></button>
      <div class="preview-progress preview-progress-top"><i></i></div>
      ${inner}
    `;
    document.getElementById('modal-close').onclick = closeModal;
    const media = content.querySelector('img,video,iframe');
    const markReady = () => {
      if (requestId !== state.previewRequestId) return;
      content.removeAttribute('aria-busy');
      content.querySelector('.preview-progress-top')?.remove();
      finishUploadProgress(progressId);
      if (state.previewProgressId === progressId) state.previewProgressId = null;
    };
    if (media) {
      media.addEventListener('load', markReady, { once: true });
      media.addEventListener('loadedmetadata', markReady, { once: true });
      media.addEventListener('error', () => {
        markReady();
        toast('Preview failed', true);
      }, { once: true });
      setTimeout(markReady, file.mime_type === 'application/pdf' ? 1200 : 5000);
    } else {
      markReady();
    }
  } catch (err) {
    if (requestId === state.previewRequestId) {
      closeModal();
      toast(err.message || 'Preview failed', true);
      finishUploadProgress(progressId);
      if (state.previewProgressId === progressId) state.previewProgressId = null;
    }
  } finally {
    state.previewingIds.delete(file.id);
  }
}
function closeModal() {
  state.previewRequestId += 1;
  document.getElementById('modal-bg').classList.remove('show');
  const content = document.getElementById('modal-content');
  content.removeAttribute('aria-busy');
  if (state.previewObjectUrl) {
    URL.revokeObjectURL(state.previewObjectUrl);
    state.previewObjectUrl = null;
  }
  if (state.previewProgressId) {
    finishUploadProgress(state.previewProgressId);
    state.previewProgressId = null;
  }
}
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
  if (!confirm('Delete this file?')) return;
  const progressId = createUploadProgress('Deleting file');
  setUploadProgress(progressId, 20);
  const r = await fetch(`/api/files/${id}`, { method: 'DELETE' });
  finishUploadProgress(progressId);
  if (r.ok) {
    const data = await r.json();
    toast(data.remote_failed ? 'Deleted locally; Telegram delete needs attention' : 'Deleted');
    loadFiles();
    loadStorage();
  }
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
  document.getElementById('bulk-paste').disabled = !state.clipboard;
}

function selectedPayload(fallback = null) {
  const fileIds = Array.from(state.selected);
  const folderIds = Array.from(state.selectedFolders);
  if (fileIds.length || folderIds.length) return { fileIds, folderIds };
  if (fallback?.kind === 'file') return { fileIds: [fallback.file.id], folderIds: [] };
  if (fallback?.kind === 'folder') return { fileIds: [], folderIds: [fallback.folder.id] };
  return { fileIds: [], folderIds: [] };
}

function setClipboard(operation, payload) {
  const total = payload.fileIds.length + payload.folderIds.length;
  if (!total) return;
  state.clipboard = { operation, fileIds: payload.fileIds, folderIds: payload.folderIds };
  toast(`${operation === 'move' ? '已剪下' : '已複製'} ${total} 個項目`);
  updateBulkBar();
}

async function relocateItems(payload, targetFolderId, operation) {
  const total = payload.fileIds.length + payload.folderIds.length;
  if (!total) return false;
  const progressId = createUploadProgress(`${operation === 'move' ? 'Moving' : 'Copying'} ${total} item(s)`);
  setUploadProgress(progressId, 20);
  try {
    const r = await fetch('/api/items/relocate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_ids: payload.fileIds,
        folder_ids: payload.folderIds,
        target_folder_id: targetFolderId,
        operation,
      }),
    });
    if (!r.ok) {
      try { toast((await r.json()).detail || 'Move failed', true); }
      catch { toast('Move failed', true); }
      return false;
    }
    const data = await r.json();
    setUploadProgress(progressId, 100);
    const changed = operation === 'move'
      ? (data.moved_files || 0) + (data.moved_folders || 0)
      : (data.copied_files || 0) + (data.copied_folders || 0);
    toast(`${operation === 'move' ? 'Moved' : 'Copied'} ${changed} item(s)${data.remote_failed ? '; remote metadata needs attention' : ''}`, !!data.remote_failed);
    state.selected.clear();
    state.selectedFolders.clear();
    state.folderChildren.clear();
    await loadFiles();
    loadStorage();
    return true;
  } finally {
    finishUploadProgress(progressId);
  }
}

async function pasteClipboard(targetFolderId = state.folderId) {
  if (!state.clipboard) return;
  const ok = await relocateItems(state.clipboard, targetFolderId, state.clipboard.operation);
  if (ok && state.clipboard.operation === 'move') state.clipboard = null;
  updateBulkBar();
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
  state.expandedFolders.clear();
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
  state.selectedFolders.clear();
  state.expandedFolders.clear();
  loadFiles();
});

document.getElementById('bulk-cancel').onclick = () => {
  state.selected.clear();
  state.selectedFolders.clear();
  renderFiles();
};
document.getElementById('bulk-cut').onclick = () => setClipboard('move', selectedPayload());
document.getElementById('bulk-copy-items').onclick = () => setClipboard('copy', selectedPayload());
document.getElementById('bulk-paste').onclick = () => pasteClipboard(state.folderId);

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

  const progressId = createUploadProgress(file.name);

  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append('file', file);
  if (state.folderId !== null) form.append('folder_id', state.folderId);

  await new Promise((resolve) => {
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        setUploadProgress(progressId, e.loaded / e.total * 100);
      }
    });
    xhr.onload = () => {
      finishUploadProgress(progressId);
      if (xhr.status === 201) { toast(`${file.name} 上傳成功`); loadFiles(); loadStorage(); }
      else {
        try { toast(JSON.parse(xhr.responseText).detail || '上傳失敗', true); }
        catch { toast('上傳失敗', true); }
      }
      resolve();
    };
    xhr.onerror = () => { finishUploadProgress(progressId); toast('網路錯誤', true); resolve(); };
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

  const progressId = createUploadProgress(file.name);

  const xhr = new XMLHttpRequest();
  const form = new FormData();
  form.append('file', upload.file);
  form.append('encrypted', String(upload.encrypted));
  if (upload.encrypted) form.append('original_mime_type', upload.originalMime);
  if (targetFolderId !== null) form.append('folder_id', targetFolderId);

  return await new Promise((resolve) => {
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        setUploadProgress(progressId, e.loaded / e.total * 100);
      }
    });
    xhr.onload = () => {
      endTransfer();
      finishUploadProgress(progressId);
      if (xhr.status === 201) {
        toast(`${file.name} uploaded`);
        loadFiles();
        loadStorage();
        resolve(true);
      } else {
        try { toast(JSON.parse(xhr.responseText).detail || 'Upload failed', true); }
        catch { toast('Upload failed', true); }
        resolve(false);
      }
    };
    xhr.onerror = () => { endTransfer(); finishUploadProgress(progressId); toast('Network error', true); resolve(false); };
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

document.addEventListener('dragover', (e) => {
  if (isInternalDrag(e)) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return;
  }
  e.preventDefault();
  document.getElementById('drop-overlay').classList.add('show');
});
document.addEventListener('dragleave', (e) => { if (!e.relatedTarget) document.getElementById('drop-overlay').classList.remove('show'); });
document.addEventListener('drop', async (e) => {
  e.preventDefault();
  document.getElementById('drop-overlay').classList.remove('show');
  if (isInternalDrag(e)) {
    const payload = JSON.parse(e.dataTransfer.getData('application/x-telecloud-items') || '{}');
    await relocateItems({ fileIds: payload.fileIds || [], folderIds: payload.folderIds || [] }, state.folderId, 'move');
    return;
  }
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

function dragPayloadFromElement(el) {
  const fileEl = el.closest('.file-item');
  const folderEl = el.closest('.folder-item');
  if (fileEl) {
    const id = parseInt(fileEl.dataset.id);
    if (!state.selected.has(id)) {
      state.selected.clear();
      state.selectedFolders.clear();
      setRowSelection(fileEl, true);
    }
  } else if (folderEl) {
    const id = parseInt(folderEl.dataset.folderId);
    if (!state.selectedFolders.has(id)) {
      state.selected.clear();
      state.selectedFolders.clear();
      setFolderSelection(folderEl, id, true);
    }
  }
  updateBulkBar();
  return selectedPayload();
}

function isInternalDrag(e) {
  return Array.from(e.dataTransfer?.types || []).includes('application/x-telecloud-items');
}

function installInternalDragHandlers(list) {
  list.querySelectorAll('.file-item,.folder-item').forEach(item => {
    item.addEventListener('dragstart', (e) => {
      if (e.target.closest('button,input')) {
        e.preventDefault();
        return;
      }
      const payload = dragPayloadFromElement(item);
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('application/x-telecloud-items', JSON.stringify(payload));
      e.dataTransfer.setData('text/plain', 'telecloud-items');
      item.classList.add('dragging');
    });
    item.addEventListener('dragend', () => {
      document.querySelectorAll('.drop-target,.dragging').forEach(el => el.classList.remove('drop-target', 'dragging'));
    });
  });

  list.querySelectorAll('.folder-item').forEach(item => {
    item.addEventListener('dragover', (e) => {
      if (!isInternalDrag(e)) return;
      e.preventDefault();
      e.stopPropagation();
      e.dataTransfer.dropEffect = 'move';
      item.classList.add('drop-target');
    });
    item.addEventListener('dragleave', () => item.classList.remove('drop-target'));
    item.addEventListener('drop', async (e) => {
      if (!isInternalDrag(e)) return;
      e.preventDefault();
      e.stopPropagation();
      item.classList.remove('drop-target');
      const payload = JSON.parse(e.dataTransfer.getData('application/x-telecloud-items') || '{}');
      const targetFolderId = parseInt(item.dataset.folderId);
      await relocateItems({ fileIds: payload.fileIds || [], folderIds: payload.folderIds || [] }, targetFolderId, 'move');
    });
  });
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
    if (e.target.closest('.file-item,.folder-item')) return;

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
  const depth = f.__depth || 0;
  if (state.view === 'list') {
    return `
    <div class="row file-row file-item${sel ? ' selected' : ''}" data-id="${f.id}" data-depth="${depth}" style="--depth:${depth}" draggable="true">
      <input type="checkbox" class="cb" data-cb="${f.id}" ${sel ? 'checked' : ''}>
      <div class="name-cell">
        ${indentRail(depth)}
        <span class="twisty spacer"></span>
        <div class="ic ${icon}"><svg width="14" height="14"><use href="#i-${icon}"/></svg></div>
        <div class="label">
          <div class="fname">${esc(f.name)}</div>
          <span class="fmeta">${mimeLabel(f.mime_type)}</span>
        </div>
      </div>
      <div class="col dim">${mimeLabel(f.mime_type)}</div>
      <div class="col">${fmtDate(f.uploaded_at)}</div>
      <div class="col size">${fmtSize(f.size)}</div>
      <span class="row-actions">${favBtnHtml(f,'file')}<button class="more-btn" data-more="${f.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button></span>
    </div>`;
  }

  const thumb = state.view === 'preview' && canUseThumbnail(f)
    ? `<img src="/api/files/${f.id}/thumbnail" alt="${esc(f.name)}" loading="lazy" onerror="this.replaceWith(this.nextElementSibling)"><div class="ic ${icon}"><svg width="18" height="18"><use href="#i-${icon}"/></svg></div>`
    : `<div class="ic ${icon}"><svg width="20" height="20"><use href="#i-${icon}"/></svg></div>`;
  return `
    <div class="file-card file-item${sel ? ' selected' : ''}" data-id="${f.id}" draggable="true">
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
  const depth = folder.__depth || 0;
  const expanded = state.expandedFolders.has(folder.id);
  if (state.view === 'list') {
    const children = state.folderChildren.get(folder.id);
    const count = children ? children.files.length + children.folders.length : (folder.item_count || folder.count || 0);
    return `
    <div class="row folder-row folder-item${sel ? ' selected' : ''}${expanded ? ' is-open' : ''}" data-folder-id="${folder.id}" data-depth="${depth}" style="--depth:${depth}" draggable="true">
      <input type="checkbox" class="cb" data-cb-folder="${folder.id}" ${sel ? 'checked' : ''}>
      <div class="name-cell">
        ${indentRail(depth)}
        <button class="twisty ${expanded ? 'open' : ''}" data-twisty="${folder.id}" aria-label="toggle">
          <svg width="11" height="11"><use href="#i-chev"/></svg>
        </button>
        <div class="ic folder">
          <svg width="14" height="14"><use href="#i-${expanded ? 'folder-open' : 'folder'}"/></svg>
        </div>
        <div class="label">
          <div class="fname">${esc(folder.name)}</div>
          <span class="fmeta">Folder · ${expanded ? 'expanded' : 'collapsed'}</span>
        </div>
      </div>
      <div class="col dim">FOLDER</div>
      <div class="col">${fmtDate(folder.created_at)}</div>
      <div class="col size dim">—</div>
      <span class="row-actions">${favBtnHtml(folder,'folder')}<button class="more-btn" data-more-folder="${folder.id}"><svg width="14" height="14"><use href="#i-dots"/></svg></button></span>
    </div>`;
  }
  return `
    <div class="file-card folder-card folder-item${sel ? ' selected' : ''}" data-folder-id="${folder.id}" draggable="true">
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

async function loadFolderChildren(folderId) {
  if (state.folderChildren.has(folderId)) return state.folderChildren.get(folderId);
  const [filesRes, foldersRes] = await Promise.all([
    fetch(`/api/files?folder_id=${encodeURIComponent(folderId)}&sort=${encodeURIComponent(state.sort)}&order=${encodeURIComponent(state.order)}`),
    fetch(`/api/folders?parent_id=${encodeURIComponent(folderId)}`),
  ]);
  const children = {
    files: await filesRes.json(),
    folders: await foldersRes.json(),
  };
  state.folderChildren.set(folderId, children);
  return children;
}

async function toggleFolderExpanded(folder) {
  if (state.expandedFolders.has(folder.id)) {
    state.expandedFolders.delete(folder.id);
    renderFiles();
    return;
  }
  try {
    await loadFolderChildren(folder.id);
    state.expandedFolders.add(folder.id);
    renderFiles();
  } catch {
    toast('Folder expand failed', true);
  }
}

function listFolderEntries(folder, depth = 0) {
  const folderEntry = { ...folder, __depth: depth };
  const entries = [{ type: 'folder', item: folderEntry }];
  if (!state.expandedFolders.has(folder.id)) return entries;

  const children = state.folderChildren.get(folder.id);
  if (!children) return entries;
  children.folders.forEach(child => {
    entries.push(...listFolderEntries(child, depth + 1));
  });
  children.files.forEach(file => {
    entries.push({ type: 'file', item: { ...file, __depth: depth + 1 } });
  });
  return entries;
}

function listEntries(folders, files) {
  const entries = [];
  folders.forEach(folder => entries.push(...listFolderEntries(folder, 0)));
  files.forEach(file => entries.push({ type: 'file', item: { ...file, __depth: 0 } }));
  return entries;
}

function attachFolderHandlers(list) {
  list.querySelectorAll('[data-folder-id]').forEach(item => {
    const id = parseInt(item.dataset.folderId);
    const folder = state.visibleFolders.find(f => f.id === id) || state.folders.find(f => f.id === id);
    if (!folder) return;
    let clickTimer = null;

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

    const twistyBtn = item.querySelector('[data-twisty]');
    if (twistyBtn) {
      twistyBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        clearTimeout(clickTimer);
        clickTimer = null;
        toggleFolderExpanded(folder);
      });
    }

    item.addEventListener('click', (e) => {
      if (suppressNextRowClick) return;
      if (e.target.closest('[data-cb-folder]') || e.target.closest('[data-more-folder]') || e.target.closest('[data-twisty]')) return;
      const totalSel = state.selected.size + state.selectedFolders.size;
      if (totalSel > 0) {
        const nowSel = !state.selectedFolders.has(id);
        setFolderSelection(item, id, nowSel);
        updateBulkBar();
      } else {
        if (state.view !== 'list') {
          enterFolder(folder);
          return;
        }
        clearTimeout(clickTimer);
        clickTimer = null;
        toggleFolderExpanded(folder);
      }
    });

    item.addEventListener('dblclick', (e) => {
      if (e.target.closest('[data-cb-folder]') || e.target.closest('[data-more-folder]') || e.target.closest('[data-twisty]')) return;
      clearTimeout(clickTimer);
      clickTimer = null;
      enterFolder(folder);
    });
  });
}

async function expandAllFolders(folders) {
  for (const folder of folders) {
    if (!state.expandedFolders.has(folder.id)) {
      try { await loadFolderChildren(folder.id); } catch { continue; }
      state.expandedFolders.add(folder.id);
    }
    const children = state.folderChildren.get(folder.id);
    if (children && children.folders.length) await expandAllFolders(children.folders);
  }
}

document.getElementById('expand-all').onclick = async () => {
  const btn = document.getElementById('expand-all');
  btn.disabled = true;
  try { await expandAllFolders(state.folders); renderFiles(); }
  finally { btn.disabled = false; }
};

document.getElementById('collapse-all').onclick = () => {
  state.expandedFolders.clear();
  renderFiles();
};

function renderFiles() {
  const list = document.getElementById('file-list');
  let displayFiles = state.files;
  let displayFolders = state.folders;
  if (state.type === 'favorite') {
    displayFiles = state.files.filter(f => f.favorite);
    displayFolders = state.folders.filter(d => d.favorite);
  }
  if (displayFiles.length === 0 && displayFolders.length === 0) {
    list.className = '';
    list.innerHTML = '<div class="empty-state">沒有檔案</div>';
    updateBulkBar();
    renderPathbar();
    return;
  }
  list.className = state.view === 'list' ? '' : `file-grid ${state.view === 'preview' ? 'preview-grid' : ''}`;
  if (state.view === 'list') {
    const entries = listEntries(displayFolders, displayFiles);
    state.visibleFolders = entries.filter(entry => entry.type === 'folder').map(entry => entry.item);
    state.visibleFiles = entries.filter(entry => entry.type === 'file').map(entry => entry.item);
    list.innerHTML = entries.map(entry => (
      entry.type === 'folder' ? folderItemMarkup(entry.item) : fileItemMarkup(entry.item)
    )).join('');
  } else {
    state.visibleFolders = displayFolders;
    state.visibleFiles = displayFiles;
    list.innerHTML = displayFolders.map(folderItemMarkup).join('') + displayFiles.map(fileItemMarkup).join('');
  }
  attachFolderHandlers(list);
  installInternalDragHandlers(list);

  list.querySelectorAll('.file-item').forEach(row => {
    const id = parseInt(row.dataset.id);
    const file = state.visibleFiles.find(f => f.id === id);

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
  if (favEl) favEl.textContent = files.filter(f => f.favorite).length + state.folders.filter(d => d.favorite).length;
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
  state.selectedFolders.clear();
  state.expandedFolders.clear();
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
  if (state.deleteSelectedBusy) return;
  const fileIds = Array.from(state.selected);
  const folderIds = Array.from(state.selectedFolders);
  const total = fileIds.length + folderIds.length;
  if (!total) return;
  if (!confirm(`Delete ${total} selected item(s)?`)) return;
  state.deleteSelectedBusy = true;
  const deleteBtn = document.getElementById('bulk-del');
  const downloadBtn = document.getElementById('bulk-download');
  const cancelBtn = document.getElementById('bulk-cancel');
  [deleteBtn, downloadBtn, cancelBtn].forEach(btn => { if (btn) btn.disabled = true; });
  const progressId = createUploadProgress(`Deleting ${total} items`);
  let done = 0;
  setUploadProgress(progressId, 0);
  let deleted = 0;
  let remoteFailed = 0;
  let failed = 0;
  try {
    if (fileIds.length) {
      const r = await fetch('/api/files/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: fileIds }),
      });
      if (r.ok) {
        const d = await r.json();
        deleted += d.deleted || 0;
        remoteFailed += d.remote_failed || 0;
      } else {
        failed += fileIds.length;
        try { toast((await r.json()).detail || 'Bulk delete failed', true); }
        catch { toast('Bulk delete failed', true); }
      }
      done += fileIds.length;
      setUploadProgress(progressId, done / total * 100);
    }
    for (const id of folderIds) {
      const r = await fetch(`/api/folders/${id}`, { method: 'DELETE' });
      if (r.ok) {
        const d = await r.json();
        deleted += (d.deleted_files || 0) + (d.deleted_folders || 0);
        remoteFailed += d.remote_failed || 0;
      } else {
        failed += 1;
        try { toast((await r.json()).detail || 'Folder delete failed', true); }
        catch { toast('Folder delete failed', true); }
      }
      done++;
      setUploadProgress(progressId, done / total * 100);
    }
    if (remoteFailed) toast(`Deleted ${deleted} item(s) locally; ${remoteFailed} Telegram delete(s) need attention`, true);
    else toast(`Deleted ${deleted} item(s)${failed ? `, ${failed} failed` : ''}`);
    state.selected.clear();
    state.selectedFolders.clear();
    await loadFiles();
    loadStorage();
  } finally {
    finishUploadProgress(progressId);
    state.deleteSelectedBusy = false;
    [deleteBtn, downloadBtn, cancelBtn].forEach(btn => { if (btn) btn.disabled = false; });
  }
}

document.getElementById('bulk-del').onclick = deleteSelectedFiles;

async function deleteFolder(folder) {
  if (!folder) return;
  if (!confirm(`Delete folder "${folder.name}" and all contents?`)) return;
  const progressId = createUploadProgress(`Deleting ${folder.name}`);
  setUploadProgress(progressId, 20);
  const r = await fetch(`/api/folders/${folder.id}`, { method: 'DELETE' });
  finishUploadProgress(progressId);
  if (r.ok) {
    const data = await r.json();
    const msg = `Deleted ${data.deleted_folders} folder(s), ${data.deleted_files} file(s)`;
    toast(data.remote_failed ? `${msg}; ${data.remote_failed} Telegram delete(s) need attention` : msg, !!data.remote_failed);
    state.selected.clear();
    state.selectedFolders.clear();
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
      { key: 'cut-items', icon: '#i-folder', label: '剪下' },
      { key: 'copy-items', icon: '#i-copy', label: '複製檔案' },
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
        { key: 'cut-items', icon: '#i-folder', label: '剪下選取' },
        { key: 'copy-items', icon: '#i-copy', label: '複製選取' },
        { sep: true },
        { key: 'delete-selected', icon: '#i-trash', label: '刪除選取', danger: true }
      );
    }
    if (state.clipboard) actions.push({ sep: true }, { key: 'paste-items', icon: '#i-folder-open', label: '貼上到這裡' });
  } else if (target?.kind === 'folder') {
    const folder = target.folder;
    actions.push(
      { key: 'open-folder', icon: '#i-folder', label: '開啟' },
      { key: 'create-folder', icon: '#i-folder', label: '創資料夾' },
      { key: 'copy-path', icon: '#i-copy', label: '複製路徑' },
      { key: 'cut-items', icon: '#i-folder', label: '剪下' },
      { key: 'copy-items', icon: '#i-copy', label: '複製資料夾' },
      ...(state.clipboard ? [{ key: 'paste-into-folder', icon: '#i-folder-open', label: '貼入資料夾' }] : []),
      { sep: true },
      { key: 'delete-folder', icon: '#i-trash', label: '刪除資料夾', danger: true }
    );
  } else {
    actions.push({ key: 'create-folder', icon: '#i-folder', label: '創資料夾' });
    if (state.clipboard) actions.push({ key: 'paste-items', icon: '#i-folder-open', label: '貼上' });
    if (state.selected.size + state.selectedFolders.size > 0) {
      actions.push(
        { key: 'download-selected', icon: '#i-down', label: '下載選取' },
        { key: 'copy-selected', icon: '#i-copy', label: '複製連結' },
        { key: 'cut-items', icon: '#i-folder', label: '剪下選取' },
        { key: 'copy-items', icon: '#i-copy', label: '複製選取' },
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
    else if (action === 'cut-items') setClipboard('move', selectedPayload(ctxTarget));
    else if (action === 'copy-items') setClipboard('copy', selectedPayload(ctxTarget));
    else if (action === 'paste-items') await pasteClipboard(state.folderId);
    else if (action === 'paste-into-folder' && ctxTarget?.kind === 'folder') await pasteClipboard(ctxTarget.folder.id);
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
    const file = state.visibleFiles.find(f => f.id === id) || state.files.find(f => f.id === id);
    if (!file) return;
    const totalSel = state.selected.size + state.selectedFolders.size;
    if (totalSel > 0 && state.selected.has(id)) showCtx(e, { kind: 'selection' });
    else showCtx(e, { kind: 'file', file });
    return;
  }
  if (folderEl) {
    const id = parseInt(folderEl.dataset.folderId);
    const folder = state.visibleFolders.find(f => f.id === id) || state.folders.find(f => f.id === id);
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
async function toggleFav(item, kind, btn) {
  const on = !item.favorite;
  const url = kind === 'folder' ? `/api/folders/${item.id}/favorite` : `/api/files/${item.id}/favorite`;
  if (btn) {
    btn.disabled = true;
    btn.classList.toggle('is-fav', on);
    btn.classList.remove('pop');
    void btn.offsetWidth;
    if (on) btn.classList.add('pop');
    setTimeout(() => btn.classList.remove('pop'), 600);
  }
  try {
    const r = await fetch(url, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ favorite: on }),
    });
    if (!r.ok) throw new Error('Favorite update failed');
    const updated = await r.json();
    Object.assign(item, updated);
    await loadFiles();
    toast(on ? 'Added to favorites' : 'Removed from favorites');
  } catch {
    if (btn) btn.classList.toggle('is-fav', !on);
    toast('Favorite update failed', true);
  } finally {
    if (btn) btn.disabled = false;
  }
  return;
  if (state.type === 'favorite') renderFiles();
  updateCounts(state.allFiles.length ? state.allFiles : state.files);
  toast(on ? '加入最愛' : '已移除');
}

function favBtnHtml(item, kind) {
  const on = item.favorite;
  return `<button class="fav-btn${on ? ' is-fav' : ''}" data-fav="${kind}:${item.id}" title="加入最愛"><svg width="14" height="14"><use href="#i-star"/></svg><span class="fav-spark"><i></i><i></i><i></i><i></i><i></i><i></i></span></button>`;
}

document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-fav]');
  if (!btn) return;
  e.stopPropagation();
  const [kind, idStr] = btn.dataset.fav.split(':');
  const id = parseInt(idStr);
  const item = kind === 'folder'
    ? (state.visibleFolders.find(f => f.id === id) || state.folders.find(f => f.id === id))
    : (state.visibleFiles.find(f => f.id === id) || state.files.find(f => f.id === id));
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
    document.body.innerHTML = `<div class="empty-state" style="min-height:100vh;display:grid;place-items:center">${clearLocalKeys ? '已登出並關閉伺服器' : '正在關閉伺服器'}</div>`;
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
