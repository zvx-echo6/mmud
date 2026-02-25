/**
 * Last Ember — Client-side logic.
 * Handles AJAX polling for live dashboard updates.
 */
(function() {
  'use strict';

  // ═══ SECRET DOTS ═══
  function renderSecretDots(found, total) {
    const container = document.getElementById('secret-dots');
    if (!container) return;
    container.innerHTML = '';
    for (let i = 0; i < total; i++) {
      const dot = document.createElement('div');
      dot.className = 'secret-dot' + (i < found ? ' found' : '');
      container.appendChild(dot);
    }
  }

  // ═══ TIME AGO ═══
  function timeAgo(dateStr) {
    if (!dateStr) return '';
    const now = new Date();
    const then = new Date(dateStr + (dateStr.includes('Z') ? '' : 'Z'));
    const diff = Math.floor((now - then) / 1000);
    if (diff < 60) return 'now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h';
    return Math.floor(diff / 86400) + 'd';
  }

  // ═══ BROADCAST POLLING ═══
  let lastBroadcastTime = null;

  function pollBroadcasts() {
    let url = '/api/broadcasts?limit=20';
    if (lastBroadcastTime) {
      url += '&since=' + encodeURIComponent(lastBroadcastTime);
    }
    fetch(url)
      .then(r => r.json())
      .then(data => {
        if (!data || !data.length) return;
        const log = document.getElementById('broadcast-log');
        if (!log) return;
        // Track newest timestamp
        if (data[0] && data[0].created_at) {
          lastBroadcastTime = data[0].created_at;
        }
        // Only prepend new entries if we had a previous timestamp
        if (lastBroadcastTime) {
          data.reverse().forEach(b => {
            const entry = document.createElement('div');
            entry.className = 'broadcast-entry';
            entry.innerHTML = `
              <span class="bc-icon">${b.tier === 1 ? '📡' : '📢'}</span>
              <span class="bc-text">${escapeHtml(b.message)}</span>
              <span class="bc-time">${timeAgo(b.created_at)}</span>
            `;
            log.prepend(entry);
          });
        }
      })
      .catch(() => {});
  }

  // ═══ STATUS POLLING ═══
  function pollStatus() {
    fetch('/api/status')
      .then(r => r.json())
      .then(data => {
        if (!data || !data.epoch) return;
        const ep = data.epoch;
        // Update epoch day
        const dayEl = document.getElementById('epoch-day');
        if (dayEl) dayEl.textContent = ep.day_number;
        // Update days remaining
        const remainEl = document.getElementById('days-remain');
        if (remainEl) remainEl.textContent = 30 - ep.day_number;
        // Update breach status
        const breachDot = document.getElementById('breach-dot');
        const breachText = document.getElementById('breach-text');
        if (breachDot && breachText) {
          if (ep.breach_open) {
            breachDot.className = 'dot open';
            breachText.textContent = 'BREACH OPEN';
          } else {
            breachDot.className = 'dot sealed';
            breachText.textContent = 'BREACH SEALED';
          }
        }
        // Update player count
        const pcEl = document.getElementById('players-online');
        if (pcEl && data.player_count !== undefined) {
          pcEl.textContent = data.player_count + ' adventurer' + (data.player_count !== 1 ? 's' : '') + ' this epoch';
        }
        // Update secrets
        if (data.secrets) {
          const label = document.getElementById('secrets-label');
          if (label) label.textContent = `SECRETS FOUND: ${data.secrets.found}/${data.secrets.total}`;
          renderSecretDots(data.secrets.found, data.secrets.total);
        }
      })
      .catch(() => {});
  }

  // ═══ ESCAPE HTML ═══
  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // ═══ INIT ═══
  function init() {
    // Initial secret dots render
    const dotsContainer = document.getElementById('secret-dots');
    if (dotsContainer) {
      const found = parseInt(dotsContainer.dataset.found || '0');
      const total = parseInt(dotsContainer.dataset.total || '20');
      renderSecretDots(found, total);
    }

    // Set up polling intervals (read from data attributes or use defaults)
    const statusInterval = parseInt(document.body.dataset.pollStatus || '30') * 1000;
    const broadcastInterval = parseInt(document.body.dataset.pollBroadcast || '15') * 1000;

    // Only poll on the dashboard page
    if (document.getElementById('epoch-day')) {
      setInterval(pollStatus, statusInterval);
      setInterval(pollBroadcasts, broadcastInterval);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
