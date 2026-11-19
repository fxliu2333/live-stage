const $ = (id) => document.getElementById(id);

const ui = {
  chipService: $('chip-service'),
  chipStage: $('chip-stage'),
  chipAudio: $('chip-audio'),
  deviceSelect: $('device-select'),
  audioTag: $('audio-tag'),
  toneNote: $('tone-note'),
  videoTag: $('video-tag'),
  videoNote: $('video-note'),
  cameraSelect: $('camera-select'),
  replyTag: $('reply-tag'),
  replyNote: $('reply-note'),
  paintNote: $('paint-note'),
  cloudTag: $('cloud-tag'),
  cloudBase: $('cloud-base'),
  cloudPhone: $('cloud-phone'),
  cloudPassword: $('cloud-password'),
  cloudNote: $('cloud-note'),
  cloudProjectField: $('cloud-project-field'),
  cloudProject: $('cloud-project'),
  cloudVoice: $('cloud-voice'),
  systemNote: $('system-note'),
  playNote: $('play-note'),
  recordNote: $('record-note'),
  scriptTag: $('script-tag'),
  scriptText: $('script-text'),
  scriptList: $('script-list'),
  scriptNote: $('script-note'),
  liveTag: $('live-tag'),
  liveUrl: $('live-url'),
  liveList: $('live-comments'),
  liveNote: $('live-note'),
};

let config = null;

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `请求失败 ${response.status}`);
  return data;
}

function setChip(chip, text, kind) {
  chip.textContent = text;
  chip.classList.toggle('ok', kind === 'ok');
  chip.classList.toggle('bad', kind === 'bad');
}

function note(el, text, isError) {
  el.textContent = text;
  el.classList.toggle('err', Boolean(isError));
}

async function loadRuntime() {
  try {
    const info = await api('/api/runtime');
    const paint = info.stage_paint;
    setChip(ui.chipStage, `演播 ${paint?.width || info.stage_width}×${paint?.height || info.stage_height}`, 'ok');
    setChip(ui.chipService, `本地服务 :${info.port || location.port}`, 'ok');
    try {
      const audioState = await api('/api/audio/status');
      const active = Number(audioState.active) || 0;
      ui.playNote.textContent = `播放通道：${active} 路${audioState.device ? ' · ' + audioState.device : ''}`;
      ui.playNote.classList.toggle('err', active > 1);
    } catch (error) {
      /* 状态查询失败不影响主流程 */
    }
    ui.videoTag.textContent = info.video ? info.video : '未设置素材';
    ui.videoTag.classList.toggle('ok', Boolean(info.video));
    if (paint?.width) {
      ui.paintNote.textContent =
        `演播窗口实际绘制 ${paint.width}×${paint.height}（${paint.css_width}×${paint.css_height} @${paint.dpr}x）`;
      ui.paintNote.classList.remove('err');
      if (paint.width < 1000) {
        ui.paintNote.textContent += ' · 低于 1080，捕获后会被放大';
        ui.paintNote.classList.add('err');
      }
    }
  } catch (error) {
    setChip(ui.chipService, '本地服务不可用', 'bad');
  }
}

async function loadDevices() {
  ui.deviceSelect.innerHTML = '<option value="">正在读取设备…</option>';
  try {
    const data = await api('/api/audio/devices');
    const devices = data.devices || [];
    if (!devices.length) {
      ui.deviceSelect.innerHTML = '<option value="">没有可用输出设备</option>';
      setChip(ui.chipAudio, '无输出设备', 'bad');
      return;
    }
    ui.deviceSelect.innerHTML = devices.map((device) => {
      const marks = [device.host_api];
      if (device.virtual) marks.push('虚拟');
      if (device.is_default) marks.push('默认');
      return `<option value="${escapeHtml(device.name)}">${escapeHtml(device.name)}（${escapeHtml(marks.join(' · '))}）</option>`;
    }).join('');

    if (config?.audio?.output_device) {
      const match = devices.find((d) => d.name === config.audio.output_device);
      if (match) ui.deviceSelect.value = match.name;
    } else {
      const cable = devices.find((d) => d.cable);
      if (cable) ui.deviceSelect.value = cable.name;
    }

    if (data.virtual_cable) {
      setChip(ui.chipAudio, '已检测到 VB-Cable', 'ok');
      ui.audioTag.textContent = '可用';
      ui.audioTag.classList.add('ok');
      $('cable-note').hidden = true;
    } else {
      setChip(ui.chipAudio, '未装 VB-Cable', 'bad');
      ui.audioTag.textContent = '缺少 VB-Cable';
      ui.audioTag.classList.remove('ok');
      const cableNote = $('cable-note');
      cableNote.hidden = false;
      cableNote.classList.add('err');
      cableNote.textContent = data.virtual_any
        ? '只找到其他虚拟声卡（如 ToDesk），不能代替 VB-Cable。请装 VB-Audio Virtual Cable（vb-audio.com/Cable）后重启电脑。'
        : '没找到虚拟声卡。请装 VB-Audio Virtual Cable（vb-audio.com/Cable），装完重启电脑。';
    }
  } catch (error) {
    ui.deviceSelect.innerHTML = '<option value="">读取失败</option>';
    setChip(ui.chipAudio, '设备读取失败', 'bad');
  }
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

async function loadConfig() {
  config = await api('/api/config');
  if (config.audio.output_device) ui.deviceSelect.value = config.audio.output_device;
  if (config.live?.room_url) ui.liveUrl.value = config.live.room_url;
  if (config.live?.filler !== undefined) $('live-filler').value = config.live.filler;
  $('monitor-audio').checked = config.audio.monitor !== false;
  document.querySelectorAll('[data-main]').forEach((button) => {
    button.classList.toggle('active', button.dataset.main === (config.stage.main || 'camera'));
  });
  document.querySelectorAll('[data-overlay]').forEach((button) => {
    button.classList.toggle('active', button.dataset.overlay === (config.stage.overlay || 'none'));
  });
  const overlay = config.stage.overlay || 'none';
  $('pip-options').hidden = overlay !== 'pip';
  const pip = config.stage.pip || {};
  document.querySelectorAll('[data-pip-corner]').forEach((button) => {
    button.classList.toggle('active', button.dataset.pipCorner === (pip.corner || 'br'));
  });
  document.querySelectorAll('[data-pip-size]').forEach((button) => {
    button.classList.toggle('active', button.dataset.pipSize === (pip.size || 'medium'));
  });
  if (config.stage.camera) {
    const option = document.createElement('option');
    option.value = config.stage.camera;
    option.textContent = '已保存的摄像头';
    ui.cameraSelect.appendChild(option);
    ui.cameraSelect.value = config.stage.camera;
  }
}

async function savePatch(patch) {
  config = await api('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

ui.deviceSelect.addEventListener('change', async () => {
  await savePatch({ audio: { output_device: ui.deviceSelect.value } });
  note(ui.toneNote, `已切换输出设备：${ui.deviceSelect.value || '默认设备'}`);
});

$('refresh-devices').addEventListener('click', () => loadDevices());

$('monitor-audio').addEventListener('change', async () => {
  await savePatch({ audio: { monitor: $('monitor-audio').checked } });
  note(ui.toneNote, $('monitor-audio').checked ? '已开启本地监听' : '已关闭本地监听，只有虚拟声卡能听到');
});

$('test-tone').addEventListener('click', async () => {
  const button = $('test-tone');
  button.disabled = true;
  note(ui.toneNote, '正在播放…');
  try {
    const device = ui.deviceSelect.value || '';
    await savePatch({ audio: { output_device: device } });
    await api('/api/audio/test-tone', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device, seconds: 2 }),
    });
    setTimeout(async () => {
      try {
        const status = await api('/api/audio/status');
        if (status.ok === false) note(ui.toneNote, `播放失败：${status.error}`, true);
        else if (status.device) note(ui.toneNote, `已通过 ${status.device} 播放`);
      } catch (error) {
        note(ui.toneNote, error.message, true);
      }
    }, 2600);
  } catch (error) {
    note(ui.toneNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

document.querySelectorAll('[data-main]').forEach((button) => {
  button.addEventListener('click', async () => {
    document.querySelectorAll('[data-main]').forEach((b) => b.classList.toggle('active', b === button));
    await savePatch({ stage: { main: button.dataset.main } });
  });
});

document.querySelectorAll('[data-overlay]').forEach((button) => {
  button.addEventListener('click', async () => {
    document.querySelectorAll('[data-overlay]').forEach((b) => b.classList.toggle('active', b === button));
    $('pip-options').hidden = button.dataset.overlay !== 'pip';
    await savePatch({ stage: { overlay: button.dataset.overlay } });
  });
});

document.querySelectorAll('[data-pip-corner]').forEach((button) => {
  button.addEventListener('click', async () => {
    document.querySelectorAll('[data-pip-corner]').forEach((b) => b.classList.toggle('active', b === button));
    await savePatch({ stage: { pip: { corner: button.dataset.pipCorner } } });
  });
});

document.querySelectorAll('[data-pip-size]').forEach((button) => {
  button.addEventListener('click', async () => {
    document.querySelectorAll('[data-pip-size]').forEach((b) => b.classList.toggle('active', b === button));
    await savePatch({ stage: { pip: { size: button.dataset.pipSize } } });
  });
});

async function loadCameras() {
  const button = $('load-cameras');
  button.disabled = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    stream.getTracks().forEach((track) => track.stop());
  } catch (error) {
    note(ui.videoNote, `摄像头授权失败：${error.name}`, true);
    button.disabled = false;
    return;
  }
  const devices = (await navigator.mediaDevices.enumerateDevices())
    .filter((device) => device.kind === 'videoinput');
  ui.cameraSelect.innerHTML = '<option value="">默认摄像头</option>' +
    devices.map((device, index) =>
      `<option value="${device.deviceId}">${escapeHtml(device.label || `摄像头 ${index + 1}`)}</option>`).join('');
  note(ui.videoNote, `找到 ${devices.length} 个摄像头`);
  button.disabled = false;
}

$('load-cameras').addEventListener('click', loadCameras);

ui.cameraSelect.addEventListener('change', async () => {
  await savePatch({ stage: { camera: ui.cameraSelect.value } });
  note(ui.videoNote, '已切换摄像头，演播窗口会自动重连');
});

$('video-upload').addEventListener('click', () => {
  const input = $('video-file');
  const button = $('video-upload');
  const file = input.files?.[0];
  if (!file) {
    note(ui.videoNote, '先选择一个视频文件', true);
    return;
  }
  const total = file.size || 0;
  button.disabled = true;
  note(ui.videoNote, `正在上传 0%（${(total / 1024 / 1024).toFixed(0)} MB）`);

  // 用 XHR 而不是 fetch，才能拿到上传进度
  const xhr = new XMLHttpRequest();
  xhr.open('POST', `/api/assets/video?name=${encodeURIComponent(file.name)}`);
  xhr.upload.onprogress = (event) => {
    if (!event.lengthComputable) return;
    const percent = Math.round((event.loaded / event.total) * 100);
    note(ui.videoNote, `正在上传 ${percent}%（${(event.total / 1024 / 1024).toFixed(0)} MB）`);
  };
  xhr.onload = () => {
    button.disabled = false;
    let data = {};
    try {
      data = JSON.parse(xhr.responseText || '{}');
    } catch (error) {
      data = {};
    }
    if (xhr.status >= 200 && xhr.status < 300 && data.ok) {
      ui.videoTag.textContent = data.video;
      ui.videoTag.classList.add('ok');
      note(ui.videoNote, `素材已更新（${(data.size / 1024 / 1024).toFixed(1)} MB），演播窗口自动切换`);
      input.value = '';
    } else {
      note(ui.videoNote, data.detail || `上传失败（${xhr.status}）`, true);
    }
  };
  xhr.onerror = () => {
    button.disabled = false;
    note(ui.videoNote, '上传失败：本地服务没响应，请确认程序还开着', true);
  };
  xhr.send(file);
});

$('clear-video').addEventListener('click', async () => {
  await api('/api/assets/video', { method: 'DELETE' });
  $('video-file').value = '';
  ui.videoTag.textContent = '未设置素材';
  ui.videoTag.classList.remove('ok');
  note(ui.videoNote, '已清空素材');
});

$('send-reply').addEventListener('click', async () => {
  const button = $('send-reply');
  button.disabled = true;
  note(ui.replyNote, '正在处理…');
  try {
    const result = await api('/api/stage/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: $('reply-text').value, nickname: $('reply-name').value }),
    });
    ui.replyTag.textContent = result.audio === 'cloud' ? '云端合成中' : result.audio === 'speech' ? '本机语音 + 字幕' : '字幕 + 测试音';
    ui.replyTag.classList.add('ok');
    note(ui.replyNote, result.notice || '已推送到演播窗口');
  } catch (error) {
    note(ui.replyNote, error.message, true);
    ui.replyTag.textContent = '失败';
  } finally {
    button.disabled = false;
  }
});

let productVersion = null;

async function loadProduct() {
  try {
    const product = await api('/api/cloud/product');
    $('prod-logistics').value = product.logistics || '';
    $('prod-after').value = product.after_sales || '';
    $('prod-specs').value = product.specs || '';
    $('prod-audience').value = product.target_audience || '';
    $('prod-promotion').value = product.promotion_info || '';
    productVersion = product.version || null;
    $('product-tag').textContent = product.product_name || '已读取';
    $('product-tag').classList.add('ok');
  } catch (error) {
    note($('product-note'), error.message, true);
  }
}

$('product-load').addEventListener('click', loadProduct);

$('product-save').addEventListener('click', async () => {
  const button = $('product-save');
  button.disabled = true;
  note($('product-note'), '正在保存到云端…');
  try {
    const result = await api('/api/cloud/product', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        version: productVersion,
        logistics: $('prod-logistics').value.trim(),
        after_sales: $('prod-after').value.trim(),
        specs: $('prod-specs').value.trim(),
        target_audience: $('prod-audience').value.trim(),
        promotion_info: $('prod-promotion').value.trim(),
      }),
    });
    productVersion = result.product?.version || productVersion;
    note($('product-note'), '已保存，云端会按新资料回答');
  } catch (error) {
    note($('product-note'), error.message, true);
  } finally {
    button.disabled = false;
  }
});

function renderCloud(status) {
  const logged = Boolean(status?.logged_in);
  ui.cloudTag.textContent = logged ? `已登录 ${status.phone || ''}` : '未登录';
  ui.cloudTag.classList.toggle('ok', logged);
  setChip($('chip-cloud'), logged ? `云端 ${status.phone || '已登录'}` : '云端未登录', logged ? 'ok' : 'bad');
  ui.cloudProjectField.hidden = !logged || !(status.projects || []).length;
  ui.cloudVoice.hidden = !logged;
  $('cloud-logout').hidden = !logged;
  $('cloud-refresh').hidden = !logged;

  if (status?.base_url) ui.cloudBase.value = status.base_url.replace(/\/api\/live\/v1$/, '');
  if (status?.phone) ui.cloudPhone.value = status.phone;

  if (logged) {
    ui.cloudProject.innerHTML = (status.projects || [])
      .map((p) => `<option value="${p.id}">${escapeHtml(p.name)}</option>`).join('');
    if (status.project_id) ui.cloudProject.value = status.project_id;
    const voice = status.voice || {};
    const parts = [];
    if (voice.name) parts.push(`音色：${voice.name}`);
    if (voice.speaker_id) parts.push(`ID：${voice.speaker_id}`);
    if (voice.trained === false) parts.push('尚未完成训练');
    ui.cloudVoice.textContent = parts.join(' · ') || '该账号还没有可用音色';
    if (status.project_id) loadProduct();
  }
}

async function loadCloud() {
  try {
    const status = await api('/api/cloud/status');
    renderCloud(status);
    if (status.logged_in && !(status.projects || []).length) {
      // 程序刚启动时会话还在后台刷新，稍后再取一次
      setTimeout(async () => {
        try {
          renderCloud(await api('/api/cloud/status'));
        } catch (error) {
          /* 忽略重试失败 */
        }
      }, 1500);
    }
  } catch (error) {
    note(ui.cloudNote, error.message, true);
  }
}

$('cloud-login').addEventListener('click', async () => {
  const button = $('cloud-login');
  button.disabled = true;
  note(ui.cloudNote, '正在登录预发测试端…');
  try {
    const status = await api('/api/cloud/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base_url: ui.cloudBase.value,
        phone: ui.cloudPhone.value,
        password: ui.cloudPassword.value,
      }),
    });
    ui.cloudPassword.value = '';
    renderCloud(status);
    note(ui.cloudNote, status.projects?.length ? '登录成功，已载入音色' : '登录成功，但该账号没有商品');
  } catch (error) {
    note(ui.cloudNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('cloud-logout').addEventListener('click', async () => {
  try {
    renderCloud(await api('/api/cloud/logout', { method: 'POST' }));
    note(ui.cloudNote, '已退出，语音回到本机合成');
  } catch (error) {
    note(ui.cloudNote, error.message, true);
  }
});

$('cloud-refresh').addEventListener('click', async () => {
  note(ui.cloudNote, '正在刷新…');
  try {
    const status = await api('/api/cloud/refresh', { method: 'POST' });
    renderCloud(status);
    note(ui.cloudNote, '已刷新');
  } catch (error) {
    note(ui.cloudNote, error.message, true);
  }
});

ui.cloudProject.addEventListener('change', async () => {
  try {
    renderCloud(await api('/api/cloud/project', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: ui.cloudProject.value }),
    }));
    note(ui.cloudNote, '已切换合成所用商品');
  } catch (error) {
    note(ui.cloudNote, error.message, true);
  }
});

function renderScript(info) {
  if (!info) return;
  const segments = info.segments || [];
  const cached = info.cached || [];
  const cachedCount = cached.filter(Boolean).length;
  const playing = Boolean(info.playing);
  ui.scriptTag.textContent = segments.length
    ? `${segments.length} 段${cachedCount ? ` · 本地 ${cachedCount}` : ''}${playing ? ' · 播放中' : ''}`
    : '未导入';
  ui.scriptTag.classList.toggle('ok', segments.length > 0);
  if (!ui.scriptText.value && segments.length) ui.scriptText.value = segments.join('\n\n');
  ui.scriptList.innerHTML = segments.map((text, index) =>
    `<div class="segment${index === info.index ? ' active' : ''}">`
    + `<span class="idx">${String(index + 1).padStart(2, '0')}</span>`
    + `<span class="txt">${escapeHtml(text)}</span>`
    + `${cached[index] ? '<span class="seg-tag">已下载</span>' : ''}`
    + '</div>'
  ).join('');
  if (info.error) note(ui.scriptNote, info.error, true);
}

async function loadScript() {
  try {
    renderScript(await api('/api/script'));
  } catch (error) {
    note(ui.scriptNote, error.message, true);
  }
}

async function importScript(text, name) {
  if (!text || !text.trim()) {
    note(ui.scriptNote, '文案为空', true);
    return;
  }
  note(ui.scriptNote, '正在切分…');
  try {
    const info = await api('/api/script/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, name }),
    });
    renderScript(info);
    note(ui.scriptNote, `已切分为 ${info.segments.length} 段`);
  } catch (error) {
    note(ui.scriptNote, error.message, true);
  }
}

$('script-import').addEventListener('click', () => {
  importScript(ui.scriptText.value, '粘贴文本');
});

$('script-file').addEventListener('change', (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    ui.scriptText.value = String(reader.result || '');
    importScript(ui.scriptText.value, file.name);
  };
  reader.readAsText(file, 'utf-8');
});

$('script-clear').addEventListener('click', async () => {
  try {
    renderScript(await api('/api/script', { method: 'DELETE' }));
    ui.scriptText.value = '';
    note(ui.scriptNote, '已清空文案');
  } catch (error) {
    note(ui.scriptNote, error.message, true);
  }
});

$('script-start').addEventListener('click', async () => {
  const button = $('script-start');
  button.disabled = true;
  note(ui.scriptNote, '正在启动…');
  try {
    const info = await api('/api/script/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loop: $('script-loop').checked }),
    });
    renderScript(info);
    note(ui.scriptNote, '已开始讲解，云端按段合成中…');
  } catch (error) {
    note(ui.scriptNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('script-stop').addEventListener('click', async () => {
  try {
    renderScript(await api('/api/script/stop', { method: 'POST' }));
    note(ui.scriptNote, '已停止（当前这段播完就停）');
  } catch (error) {
    note(ui.scriptNote, error.message, true);
  }
});

$('script-prepare').addEventListener('click', async () => {
  const button = $('script-prepare');
  button.disabled = true;
  note(ui.scriptNote, '正在把语音下载到本地…');
  try {
    renderScript(await api('/api/script/prepare', { method: 'POST' }));
  } catch (error) {
    note(ui.scriptNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

const events = new EventSource('/api/events');
events.addEventListener('comment', (event) => {
  const item = JSON.parse(event.data);
  const row = document.createElement('div');
  row.className = 'segment';
  row.innerHTML = `<span class="txt"><b>${escapeHtml(item.nickname || '观众')}</b>：${escapeHtml(item.text)}</span>`;
  ui.liveList.prepend(row);
  while (ui.liveList.children.length > 12) ui.liveList.lastElementChild.remove();
});
events.addEventListener('script_state', (event) => {
  const data = JSON.parse(event.data);
  if (data.state === 'preparing') {
    const total = data.total || 0;
    ui.scriptTag.textContent = `下载本地 ${Number(data.index) + 1}/${total}`;
    note(ui.scriptNote, data.skipped
      ? `第 ${Number(data.index) + 1} 段已在本地，跳过`
      : `正在下载第 ${Number(data.index) + 1} / ${total} 段`);
    return;
  }
  if (data.state === 'prepared') {
    note(ui.scriptNote, '全部语音已存到本地，播放时不再请求云端');
    loadScript();
    return;
  }
  if (data.state === 'error') {
    ui.scriptTag.textContent = '合成失败';
    note(ui.scriptNote, data.detail || '云端合成失败', true);
    return;
  }
  if (data.state === 'stopped') {
    ui.scriptTag.textContent = '已停止';
    ui.scriptTag.classList.remove('ok');
    loadScript();
    return;
  }
  const total = data.total || 0;
  const index = Number.isFinite(data.index) ? data.index : -1;
  const isLocal = data.state === 'local';
  const label = isLocal ? '本地播放' : '合成中';
  ui.scriptTag.textContent = `${total} 段 · ${label}`;
  note(ui.scriptNote, `${label} 第 ${index + 1} / ${total} 段${isLocal ? '（已下载，不再请求云端）' : ''}`);
  document.querySelectorAll('.segment').forEach((node, i) => {
    node.classList.toggle('active', i === index);
  });
});

events.addEventListener('reply_state', (event) => {
  const data = JSON.parse(event.data);
  if (data.state === 'synthesizing') {
    ui.replyTag.textContent = '云端合成中';
    note(ui.replyNote, '云端正在用测试端音色合成…');
  } else if (data.state === 'playing') {
    ui.replyTag.textContent = '正在播放';
    note(ui.replyNote, '合成完成，已送入虚拟声卡');
  } else if (data.state === 'done') {
    ui.replyTag.textContent = '已完成';
    note(ui.replyNote, '播报完成');
  } else if (data.state === 'skipped') {
    ui.replyTag.textContent = '已跳过';
    note(ui.replyNote, `${data.detail || '本条跳过'}｜${data.nickname || '观众'}：${data.text || ''}`);
  } else if (data.state === 'error') {
    ui.replyTag.textContent = '失败';
    ui.replyTag.classList.remove('ok');
    note(ui.replyNote, data.detail || '合成失败', true);
  }
});

$('record-line').addEventListener('click', async () => {
  const button = $('record-line');
  button.disabled = true;
  note(ui.recordNote, '正在录制虚拟线 10 秒，请同时让讲解正常播放…');
  try {
    const result = await api('/api/audio/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ seconds: 10 }),
    });
    if (!result.ok) {
      note(ui.recordNote, result.error || '录制失败', true);
      return;
    }
    const analysis = result.analysis || {};
    const verdict = analysis.clean === true
      ? '虚拟线正常：没有重复副本、没有断续、音量平稳'
      : ((analysis.issues || []).join('；') || '未检测到问题');
    note(ui.recordNote, `${verdict}｜已存 ${result.seconds} 秒到 runtime\\recordings`, analysis.clean === false);
  } catch (error) {
    note(ui.recordNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

function renderLive(info) {
  if (!info) return;
  const running = Boolean(info.running);
  const counts = info.counts || {};
  ui.liveTag.textContent = running ? `采集中 · ${counts.collected || 0} 条` : '未开始';
  ui.liveTag.classList.toggle('ok', running);
  $('live-auto').checked = Boolean(info.auto_reply);
  if (info.room_url && !ui.liveUrl.value) ui.liveUrl.value = info.room_url;
  if (info.error) {
    note(ui.liveNote, info.error, true);
  } else if (running) {
    note(ui.liveNote, `已采集 ${counts.collected || 0} 条 · 已回复 ${counts.replied || 0} 条 · 跳过 ${counts.skipped || 0} 条`);
  }

  const launchTag = $('launch-tag');
  if (running) {
    launchTag.textContent = '已开播';
    launchTag.classList.add('ok');
    launchTag.classList.remove('bad');
    const tail = info.last_skip ? `｜最近跳过：${info.last_skip}` : '';
    note($('launch-note'), info.error
      || `弹幕 ${counts.collected || 0} 条 · 回复 ${counts.replied || 0} 条 · 跳过 ${counts.skipped || 0} 条${tail}`,
    Boolean(info.error));
  } else if (launchTag.textContent === '已开播') {
    launchTag.textContent = '已停止';
    launchTag.classList.remove('ok');
  }
  ui.liveList.innerHTML = (info.comments || []).slice().reverse().map((item) =>
    `<div class="segment"><span class="txt"><b>${escapeHtml(item.nickname || '观众')}</b>：${escapeHtml(item.text)}</span></div>`
  ).join('');
}

async function loadLive() {
  try {
    renderLive(await api('/api/live/status'));
  } catch (error) {
    note(ui.liveNote, error.message, true);
  }
}

$('live-start').addEventListener('click', async () => {
  const button = $('live-start');
  button.disabled = true;
  note(ui.liveNote, '正在拉起 Edge 并连接直播间…');
  try {
    const status = await api('/api/live/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_url: ui.liveUrl.value.trim(), auto_reply: $('live-auto').checked }),
    });
    renderLive(status);
    note(ui.liveNote, '已开始采集，Edge 窗口里能看到直播间评论即可');
  } catch (error) {
    note(ui.liveNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('live-stop').addEventListener('click', async () => {
  try {
    renderLive(await api('/api/live/stop', { method: 'POST' }));
    note(ui.liveNote, '已停止采集');
  } catch (error) {
    note(ui.liveNote, error.message, true);
  }
});

$('live-probe').addEventListener('click', async () => {
  const button = $('live-probe');
  const box = $('live-probe-out');
  button.disabled = true;
  box.hidden = false;
  box.textContent = '正在读取页面结构…';
  try {
    const info = await api('/api/live/probe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_url: ui.liveUrl.value.trim() }),
    });
    box.textContent = JSON.stringify(info, null, 2);
  } catch (error) {
    box.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$('live-auto').addEventListener('change', async () => {
  try {
    await api('/api/live/auto-reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: $('live-auto').checked }),
    });
    note(ui.liveNote, $('live-auto').checked ? '自动回复已开启' : '自动回复已关闭，只采集不回复');
  } catch (error) {
    note(ui.liveNote, error.message, true);
  }
});

$('live-filler').addEventListener('change', async () => {
  try {
    await savePatch({ live: { filler: $('live-filler').value.trim() } });
  } catch (error) {
    note(ui.liveNote, error.message, true);
  }
});

$('launch-all').addEventListener('click', async () => {
  const button = $('launch-all');
  const tag = $('launch-tag');
  const noteEl = $('launch-note');
  button.disabled = true;
  let scriptStarted = false;
  try {
    const roomUrl = ui.liveUrl.value.trim();
    if (!roomUrl.startsWith('http')) throw new Error('先填直播间地址');
    const cloudStatus = await api('/api/cloud/status');
    if (!cloudStatus.logged_in) throw new Error('还没登录测试端，请到「高级设置 → 测试端语音」登录');
    const scriptInfo = await api('/api/script');
    if (!(scriptInfo.segments || []).length) throw new Error('还没有文案，先在下面导入口播稿');

    tag.textContent = '启动中';
    tag.classList.remove('ok');
    tag.classList.remove('bad');
    note(noteEl, '正在开始讲解…');
    $('script-loop').checked = true;
    renderScript(await api('/api/script/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ loop: true }),
    }));
    scriptStarted = true;

    note(noteEl, '正在打开采集窗口，第一次需要在里面扫码登录抖音…');
    renderLive(await api('/api/live/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ room_url: roomUrl, auto_reply: $('live-auto').checked,
                             filler: $('live-filler').value.trim() }),
    }));
    note(noteEl, $('live-auto').checked
      ? '已开播：讲解在播、弹幕在采并自动回复'
      : '已开播：讲解在播、弹幕在采（自动回复未开）');
  } catch (error) {
    tag.textContent = scriptStarted ? '部分开启' : '未开播';
    tag.classList.remove('ok');
    tag.classList.add('bad');
    note(noteEl, (scriptStarted ? '讲解已开始，但采集没起来：' : '') + error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('launch-stop').addEventListener('click', async () => {
  const button = $('launch-stop');
  const tag = $('launch-tag');
  button.disabled = true;
  note($('launch-note'), '正在停止…');
  try {
    await Promise.allSettled([
      api('/api/script/stop', { method: 'POST' }),
      api('/api/live/stop', { method: 'POST' }),
    ]);
    renderScript(await api('/api/script'));
    renderLive(await api('/api/live/status'));
    tag.textContent = '已下播';
    tag.classList.remove('ok');
    tag.classList.remove('bad');
    note($('launch-note'), '已下播：讲解和采集都停了');
  } catch (error) {
    note($('launch-note'), error.message, true);
  } finally {
    button.disabled = false;
  }
});

async function loadSystemAudio() {
  try {
    const info = await api('/api/audio/system');
    if (!info.ok) {
      note(ui.systemNote, `系统默认输出：读取失败（${info.error}）`, true);
      return;
    }
    const isVirtual = Boolean(info.virtual_default);
    note(ui.systemNote, `系统默认输出：${info.default || '未知'}${isVirtual ? ' · 虚拟声卡占着，建议交还扬声器' : ''}`, isVirtual);
  } catch (error) {
    note(ui.systemNote, error.message, true);
  }
}

$('release-audio').addEventListener('click', async () => {
  const button = $('release-audio');
  button.disabled = true;
  note(ui.systemNote, '正在切回扬声器…');
  try {
    const result = await api('/api/audio/system/release', { method: 'POST' });
    if (!result.ok) note(ui.systemNote, result.error || '切换失败', true);
    else note(ui.systemNote, result.changed ? `已交还：${result.default}` : `当前已是 ${result.default}，无需切换`);
    await loadSystemAudio();
  } catch (error) {
    note(ui.systemNote, error.message, true);
  } finally {
    button.disabled = false;
  }
});

(async () => {
  try {
    await loadConfig();
    await loadRuntime();
    await loadDevices();
    await loadCloud();
    await loadSystemAudio();
    await loadScript();
    await loadLive();
  } catch (error) {
    note(ui.toneNote, error.message, true);
  }
  setInterval(loadRuntime, 5000);
  setInterval(loadLive, 5000);
})();
