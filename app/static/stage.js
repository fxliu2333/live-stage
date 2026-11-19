const els = {
  stage: document.getElementById('stage'),
  video: document.getElementById('main-video'),
  cam: document.getElementById('main-cam'),
  fallback: document.getElementById('fallback'),
  pip: document.getElementById('pip'),
  pipVideo: document.getElementById('pip-video'),
  pipCam: document.getElementById('pip-cam'),
  subtitle: document.getElementById('subtitle'),
  who: document.getElementById('sub-who'),
  text: document.getElementById('sub-text'),
  clock: document.getElementById('clock'),
  viewers: document.getElementById('viewers'),
  hint: document.getElementById('hint'),
  debug: document.getElementById('debug'),
};

const state = {
  config: null,
  camStream: null,
  cameraId: undefined,
  desiredCamera: undefined,
  videoReady: false,
  subtitleTimer: 0,
  debug: new URLSearchParams(location.search).has('debug'),
};

// 视频解不了码时给出明确原因，别让人以为是程序坏了
for (const el of [els.video, els.pipVideo]) {
  el.addEventListener('error', () => {
    if (!el.getAttribute('src')) return;
    showHint('视频无法解码：多半是 H.265/HEVC 编码。请导出成 H.264（AVC）的 mp4 再上传。');
  });
}

function showHint(text) {
  els.hint.textContent = text;
  els.hint.hidden = !text;
}

function updateLayers() {
  const main = state.config?.stage?.main || 'camera';
  const overlay = state.config?.stage?.overlay || 'none';
  const hasCamera = Boolean(state.camStream);
  const hasVideo = state.videoReady;

  const cameraIsMain = main === 'camera' && hasCamera;
  els.cam.hidden = !cameraIsMain;
  els.video.hidden = cameraIsMain || !hasVideo;
  els.fallback.hidden = cameraIsMain || hasVideo;

  // 摄像头为主画面：小窗放视频；视频为主画面 + 画中画：小窗放实时摄像头
  const videoInPip = cameraIsMain && hasVideo;
  const cameraInPip = !cameraIsMain && overlay === 'pip' && hasVideo && hasCamera;
  els.pip.hidden = !(videoInPip || cameraInPip);
  els.pipVideo.hidden = !videoInPip;
  els.pipCam.hidden = !cameraInPip;
  if (videoInPip) playVideo(els.pipVideo);
  if (cameraInPip) playVideo(els.pipCam);
}

function playVideo(el) {
  if (!el.src) return;
  const attempt = el.play();
  if (attempt && typeof attempt.catch === 'function') attempt.catch(() => {});
}

function applyVideo(config) {
  const name = config?.stage?.video || '';
  const wanted = name ? `/assets/${encodeURIComponent(name)}?v=${Date.now()}` : '';
  const current = els.video.getAttribute('src') || '';
  if (wanted === current) {
    updateLayers();
    return;
  }
  for (const el of [els.video, els.pipVideo]) {
    if (wanted) {
      el.src = wanted;
      playVideo(el);
    } else {
      el.removeAttribute('src');
      el.load();
    }
  }
  state.videoReady = Boolean(wanted);
  updateLayers();
}

async function openCamera(deviceId) {
  const attempts = [];
  if (deviceId) {
    // 保存的设备可能已经拔掉或换了 id，逐级放宽条件，最后退回系统默认摄像头
    attempts.push({ deviceId: { exact: deviceId }, width: { ideal: 1280 }, height: { ideal: 720 } });
    attempts.push({ deviceId: { ideal: deviceId } });
  }
  attempts.push({ width: { ideal: 1280 }, height: { ideal: 720 } });
  attempts.push({});

  let lastError = null;
  for (const video of attempts) {
    try {
      return await navigator.mediaDevices.getUserMedia({ video, audio: false });
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("camera unavailable");
}

function cameraErrorText(error) {
  const name = error?.name || '';
  const messages = {
    NotAllowedError: '没有摄像头权限：Windows 设置 → 隐私和安全性 → 相机，允许桌面应用访问后重启程序',
    PermissionDeniedError: '没有摄像头权限：Windows 设置 → 隐私和安全性 → 相机，允许桌面应用访问',
    NotReadableError: '摄像头被别的程序占用：先关掉 DJI Mimo、直播伴侣预览等再试',
    TrackStartError: '摄像头被别的程序占用：先关掉占用它的软件',
    NotFoundError: '没有找到摄像头设备',
    OverconstrainedError: '摄像头不支持请求的分辨率',
    SecurityError: '摄像头被系统策略禁止',
  };
  return messages[name] || `摄像头未连接（${name || error?.message || '未知原因'}）`;
}

async function startCamera(deviceId) {
  if (!navigator.mediaDevices?.getUserMedia) {
    showHint('当前环境不支持摄像头访问');
    return;
  }
  if (state.camStream) {
    state.camStream.getTracks().forEach((track) => track.stop());
    state.camStream = null;
  }
  try {
    const stream = await openCamera(deviceId);
    state.camStream = stream;
    const track = stream.getVideoTracks()[0];
    state.cameraId = track?.getSettings?.().deviceId || deviceId;
    els.cam.srcObject = stream;
    els.pipCam.srcObject = stream;
    playVideo(els.cam);
    playVideo(els.pipCam);
    showHint('');
  } catch (error) {
    state.camStream = null;
    showHint(`${cameraErrorText(error)}。可在控制台「演播画面 → 读取设备」重新选择。`);
  }
  updateLayers();
}

function applyConfig(config) {
  state.config = config;
  const size = Number(config?.subtitle?.font_size) || 30;
  document.documentElement.style.setProperty('--sub-size', String(size));
  // 纯摄像头模式：隐藏 LIVE、人数、时钟、字幕和画中画
  const overlay = config?.stage?.overlay || 'none';
  document.body.classList.toggle('overlay-off', overlay === 'none');
  document.body.classList.toggle('overlay-pip', overlay === 'pip');
  const pip = config?.stage?.pip || {};
  const corner = pip.corner === 'bl' ? 'bl' : 'br';
  const pipSize = ['small', 'medium', 'large'].includes(pip.size) ? pip.size : 'medium';
  els.pip.classList.toggle('corner-br', corner === 'br');
  els.pip.classList.toggle('corner-bl', corner === 'bl');
  els.pip.classList.remove('size-small', 'size-medium', 'size-large');
  els.pip.classList.add(`size-${pipSize}`);
  applyVideo(config);

  const camera = config?.stage?.camera || '';
  if (camera !== state.desiredCamera) {
    state.desiredCamera = camera;
    startCamera(camera);
  } else {
    updateLayers();
  }
}

function showSubtitle(payload) {
  const who = payload.nickname || '';
  els.who.textContent = who;
  els.who.hidden = !who;
  els.text.textContent = payload.text || '';
  els.subtitle.classList.add('show');
  clearTimeout(state.subtitleTimer);
  const duration = Number(payload.duration_ms) || 7000;
  state.subtitleTimer = setTimeout(() => els.subtitle.classList.remove('show'), duration);
}

function tickClock() {
  const now = new Date();
  els.clock.textContent = now.toLocaleTimeString('zh-CN', { hour12: false });
}
setInterval(tickClock, 1000);
tickClock();

let viewers = 126;
setInterval(() => {
  viewers = Math.max(20, viewers + Math.round((Math.random() - 0.42) * 7));
  els.viewers.textContent = String(viewers);
}, 3000);
els.viewers.textContent = String(viewers);

if (state.debug) {
  els.debug.hidden = false;
  let frames = 0;
  let last = performance.now();
  const loop = () => {
    frames += 1;
    const now = performance.now();
    if (now - last >= 1000) {
      els.debug.textContent = [
        `${innerWidth}×${innerHeight} @${devicePixelRatio}x`,
        `render ${Math.round((frames * 1000) / (now - last))} fps`,
      ].join('\n');
      frames = 0;
      last = now;
    }
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

function reportMetrics() {
  fetch('/api/stage/metrics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      width: Math.round(innerWidth * devicePixelRatio),
      height: Math.round(innerHeight * devicePixelRatio),
      dpr: devicePixelRatio,
      css_width: innerWidth,
      css_height: innerHeight,
    }),
  }).catch(() => {});
}
reportMetrics();
let metricsTimer = 0;
addEventListener('resize', () => {
  clearTimeout(metricsTimer);
  metricsTimer = setTimeout(reportMetrics, 400);
});

fetch('/api/config')
  .then((res) => res.json())
  .then(applyConfig)
  .catch(() => showHint('本地服务未连接'));

const events = new EventSource('/api/events');
events.addEventListener('subtitle', (event) => showSubtitle(JSON.parse(event.data)));
events.addEventListener('config', (event) => applyConfig(JSON.parse(event.data)));
