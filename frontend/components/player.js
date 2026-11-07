/* <cqa-player> — плеер записи звонка: воспроизведение, перемотка, скорость, метки доказательств и участок удержания.
   .setSource(src, durationHint) · .seek(sec, play) · .setMarkers([{sec, tone, label}]) · .setRegions([{from, to, kind}])
   Событие cqa-time {sec}. */
(function (CQA) {
  'use strict';
  var h = CQA.h, icon = CQA.icon, t = CQA.t, fmt = CQA.fmt;

  class CqaPlayer extends HTMLElement {
    constructor() {
      super();
      this._markers = [];
      this._regions = [];
      this._dur = 0;
    }

    connectedCallback() {
      if (this._built) return;
      this._built = true;
      var self = this;
      this.classList.add('player');
      this.audio = h('audio', { preload: 'metadata' });
      this.playBtn = h('button', { class: 'btn btn--primary btn--icon player__play', type: 'button', 'aria-label': t('player.play') }, icon('play'));
      this.backBtn = h('button', { class: 'btn btn--subtle btn--icon', type: 'button', 'aria-label': t('player.back5'), title: t('player.back5') }, icon('back5'));
      this.range = h('input', { class: 'player__range', type: 'range', min: '0', max: '0', step: '0.1', value: '0', 'aria-label': t('player.position') });
      this.track = h('div', { class: 'player__track' }, h('div', { class: 'player__regions' }), h('div', { class: 'player__markers' }), this.range);
      this.time = h('span', { class: 'player__time num', 'aria-live': 'off' }, '0:00 / —:—');
      this.rate = h('select', { class: 'select select--small player__rate', 'aria-label': t('player.rate') },
        ['1', '1.25', '1.5', '2'].map(function (r) { return h('option', { value: r }, r.replace('.', ',') + '×'); }));
      this.status = h('div', { class: 'player__status', role: 'status' });
      this.append(this.audio,
        h('div', { class: 'player__row' },
          this.playBtn, this.backBtn, this.track, this.time, this.rate),
        this.status);

      this.playBtn.addEventListener('click', function () { self.toggle(); });
      this.backBtn.addEventListener('click', function () { self.seek(Math.max(0, self.audio.currentTime - 5)); });
      this.range.addEventListener('input', function () { self.audio.currentTime = Number(self.range.value); self.updateTime(); });
      this.rate.addEventListener('change', function () { self.audio.playbackRate = Number(self.rate.value); });
      this.audio.addEventListener('loadedmetadata', function () {
        if (isFinite(self.audio.duration) && self.audio.duration > 0) self._dur = self.audio.duration;
        self.range.max = String(self._dur || 0);
        self.renderMarks();
        self.updateTime();
      });
      this.audio.addEventListener('timeupdate', function () {
        self.updateTime();
        self.dispatchEvent(new CustomEvent('cqa-time', { bubbles: true, detail: { sec: self.audio.currentTime } }));
      });
      this.audio.addEventListener('play', function () { self.setPlayingUi(true); });
      this.audio.addEventListener('pause', function () { self.setPlayingUi(false); });
      this.audio.addEventListener('ended', function () { self.setPlayingUi(false); });
      this.audio.addEventListener('error', function () {
        self.classList.add('is-error');
        CQA.dom.replace(self.status, icon('warning'), t('player.error'));
      });
    }

    setSource(src, durationHint) {
      this._dur = durationHint || 0;
      this.range.max = String(this._dur);
      this.classList.remove('is-error');
      this.status.textContent = '';
      if (!src) {
        this.classList.add('is-error');
        CQA.dom.replace(this.status, icon('info'), t('player.noAudio'));
        this.playBtn.disabled = true;
        return;
      }
      this.audio.src = src;
      this.renderMarks();
      this.updateTime();
    }

    setPlayingUi(on) {
      CQA.dom.replace(this.playBtn, icon(on ? 'pause' : 'play'));
      this.playBtn.setAttribute('aria-label', on ? t('player.pause') : t('player.play'));
      this.classList.toggle('is-playing', on);
    }

    toggle() {
      if (this.audio.paused) this.play(); else this.audio.pause();
    }

    play() {
      var self = this;
      var p = this.audio.play();
      if (p && p.catch) p.catch(function () {
        // автозапуск запрещён браузером (нет жеста пользователя) — оставляем перемотку, просим нажать ▶
        CQA.dom.replace(self.status, icon('info'), t('player.pressPlay'));
      });
    }

    seek(sec, play) {
      if (sec == null || !isFinite(sec)) return;
      var self = this;
      var apply = function () {
        self.audio.currentTime = sec;
        self.updateTime();
        if (play) self.play();
      };
      if (this.audio.readyState >= 1) apply();
      else this.audio.addEventListener('loadedmetadata', apply, { once: true });
      this.status.textContent = '';
      this.flashAt(sec);
    }

    updateTime() {
      var cur = this.audio.currentTime || 0;
      this.range.value = String(cur);
      this.range.setAttribute('aria-valuetext', fmt.clock(cur));
      var pct = this._dur ? (cur / this._dur) * 100 : 0;
      this.range.style.setProperty('--pct', pct + '%');
      this.time.textContent = fmt.clock(cur) + ' / ' + fmt.clock(this._dur || NaN);
    }

    setMarkers(list) { this._markers = list || []; this.renderMarks(); }
    setRegions(list) { this._regions = list || []; this.renderMarks(); }

    renderMarks() {
      if (!this.track) return;
      var dur = this._dur;
      var markers = this.track.querySelector('.player__markers');
      var regions = this.track.querySelector('.player__regions');
      markers.textContent = '';
      regions.textContent = '';
      if (!dur) return;
      var self = this;
      this._regions.forEach(function (r) {
        if (r.from == null || r.to == null) return;
        regions.appendChild(h('span', {
          class: 'player__region player__region--' + r.kind,
          style: { left: (r.from / dur * 100) + '%', width: Math.max(0.5, (r.to - r.from) / dur * 100) + '%' },
          title: r.label || ''
        }));
      });
      this._markers.forEach(function (m) {
        if (m.sec == null) return;
        markers.appendChild(h('button', {
          class: 'player__marker player__marker--' + (m.tone || 'neutral'), type: 'button', tabindex: '-1',
          style: { left: (m.sec / dur * 100) + '%' }, title: m.label || fmt.clock(m.sec), 'aria-hidden': 'true',
          onClick: function () { self.seek(m.sec, true); }
        }));
      });
    }

    flashAt(sec) {
      if (!this._dur) return;
      var f = h('span', { class: 'player__flash', style: { left: (sec / this._dur * 100) + '%' } });
      this.track.appendChild(f);
      setTimeout(function () { f.remove(); }, 1200);
    }

    disconnectedCallback() { if (this.audio) this.audio.pause(); }
  }

  customElements.define('cqa-player', CqaPlayer);
})(window.CQA = window.CQA || {});
