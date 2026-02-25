/**
 * Ember Particle Canvas — shared across all Last Ember pages.
 * Call initEmbers(count, opacity) to configure per page.
 */
(function() {
  const canvas = document.getElementById('ember-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let embers = [];
  let particleCount = 40;

  function resize() {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
  }
  resize();
  window.addEventListener('resize', resize);

  class Ember {
    constructor() { this.reset(); }
    reset() {
      this.x = Math.random() * canvas.width;
      this.y = canvas.height + 10;
      this.size = Math.random() * 2.5 + 0.5;
      this.speedY = -(Math.random() * 0.4 + 0.1);
      this.speedX = (Math.random() - 0.5) * 0.3;
      this.opacity = Math.random() * 0.5 + 0.2;
      this.decay = Math.random() * 0.001 + 0.0005;
      this.wobble = Math.random() * Math.PI * 2;
      this.wobbleSpeed = Math.random() * 0.02 + 0.005;
      const t = Math.random();
      this.r = Math.floor(200 + t * 55);
      this.g = Math.floor(80 + t * 80);
      this.b = Math.floor(20 + t * 30);
    }
    update() {
      this.wobble += this.wobbleSpeed;
      this.x += this.speedX + Math.sin(this.wobble) * 0.15;
      this.y += this.speedY;
      this.opacity -= this.decay;
      if (this.opacity <= 0 || this.y < -20) this.reset();
    }
    draw() {
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${this.r},${this.g},${this.b},${this.opacity})`;
      ctx.fill();
      // glow
      ctx.beginPath();
      ctx.arc(this.x, this.y, this.size * 3, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${this.r},${this.g},${this.b},${this.opacity * 0.15})`;
      ctx.fill();
    }
  }

  function animate() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    embers.forEach(e => { e.update(); e.draw(); });
    requestAnimationFrame(animate);
  }

  // Public API
  window.initEmbers = function(count, opacity) {
    particleCount = count || 40;
    canvas.style.opacity = opacity !== undefined ? opacity : 0.6;
    embers = [];
    for (let i = 0; i < particleCount; i++) {
      const e = new Ember();
      e.y = Math.random() * canvas.height; // Pre-distribute
      embers.push(e);
    }
    animate();
  };

  // Auto-init with defaults if no explicit call
  window.initEmbers(40, 0.6);
})();
