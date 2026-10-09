// 博客 / 文章模式脚本：暗色切换、阅读进度条、本文目录高亮
(function () {
  // 暗色模式
  var darkBtn = document.getElementById('btn-dark');
  if (darkBtn) {
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      document.body.classList.add('dark');
      darkBtn.classList.add('active');
    }
    darkBtn.addEventListener('click', function () {
      document.body.classList.toggle('dark');
      this.classList.toggle('active');
    });
  }

  // 阅读进度条
  var bar = document.querySelector('.progress-bar');
  if (bar) {
    var updateProgress = function () {
      var doc = document.documentElement;
      var h = doc.scrollHeight - window.innerHeight;
      bar.style.width = (h > 0 ? (window.scrollY / h * 100) : 0) + '%';
    };
    window.addEventListener('scroll', updateProgress, { passive: true });
    window.addEventListener('resize', updateProgress);
    updateProgress();
  }

  // 本文目录：popup 开关 + 滚动高亮
  var toc = document.querySelector('.toc-box');
  if (toc) {
    var toggle = toc.querySelector('.toc-toggle');
    if (toggle) {
      toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        var collapsed = toc.classList.toggle('collapsed');
        toggle.setAttribute('aria-expanded', String(!collapsed));
      });
    }
    document.addEventListener('click', function (e) {
      if (!toc.contains(e.target)) toc.classList.add('collapsed');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') toc.classList.add('collapsed');
    });
    var links = toc.querySelectorAll('a[href^="#"]');
    if (links.length) {
      var sections = [];
      links.forEach(function (a) {
        var el = document.getElementById(a.getAttribute('href').slice(1));
        if (el) sections.push({ el: el, a: a });
      });
      var last = null;
      var setActive = function (a) {
        if (a === last) return;
        links.forEach(function (x) { x.classList.remove('active'); });
        if (a) a.classList.add('active');
        last = a;
      };
      var compute = function () {
        if (!sections.length) return null;
        var line = window.innerHeight * 0.3;
        var chosen = sections[0];
        for (var i = 0; i < sections.length; i++) {
          if (sections[i].el.getBoundingClientRect().top - line <= 0) chosen = sections[i];
          else break;
        }
        return chosen.a;
      };
      window.addEventListener('scroll', function () { setActive(compute()); }, { passive: true });
      window.addEventListener('resize', function () { setActive(compute()); });
      setActive(compute());
    }
  }

  // 悬浮全局目录（Spotlight 式浮层）：开关 + 搜索过滤 + 键盘导航 + 标记当前篇
  var finder = document.getElementById('finder');
  if (finder) {
    var panel = finder.querySelector('.finder-panel');
    var input = finder.querySelector('.finder-search');
    var body = finder.querySelector('.finder-body');
    var empty = document.getElementById('finder-empty');
    var groups = Array.prototype.slice.call(finder.querySelectorAll('.finder-group'));
    var allLinks = Array.prototype.slice.call(finder.querySelectorAll('.finder-group a'));

    var visible = function () {
      return allLinks.filter(function (a) { return a.parentElement.style.display !== 'none'; });
    };
    var setActive = function (a) {
      allLinks.forEach(function (x) { x.classList.remove('active'); });
      if (a) a.classList.add('active');
    };
    var filter = function (q) {
      q = q.trim().toLowerCase();
      var n = 0;
      groups.forEach(function (grp) {
        var hits = 0;
        grp.querySelectorAll('a').forEach(function (a) {
          var hit = !q || a.textContent.toLowerCase().indexOf(q) !== -1;
          a.parentElement.style.display = hit ? '' : 'none';
          if (hit) { hits++; n++; }
        });
        grp.style.display = hits ? '' : 'none';
      });
      if (empty) empty.hidden = n !== 0;
      var vis = visible();
      setActive(vis.length ? vis[0] : null);
    };

    var btn = document.getElementById('btn-find');

    var open = function () {
      finder.classList.add('open'); finder.setAttribute('aria-hidden', 'false');
      if (btn) btn.classList.add('active');
      input.value = ''; filter(''); input.focus();
    };
    var close = function () {
      finder.classList.remove('open'); finder.setAttribute('aria-hidden', 'true');
      if (btn) btn.classList.remove('active');
    };

    if (btn) btn.addEventListener('click', function () {
      if (finder.classList.contains('open')) close();
      else open();
    });

    // 点击浮层外部关闭（无遮罩层，需手动判断）
    finder.addEventListener('mousedown', function (e) {
      if (!panel.contains(e.target)) close();
    });

    input.addEventListener('input', function () { filter(input.value); });

    // 标记当前文章（按文件名匹配，兼容静态相对路径与 serve 的 /p/ 前缀）
    var here = location.pathname.split('/').pop();
    allLinks.forEach(function (a) {
      var href = a.getAttribute('href');
      if (href && href.split('/').pop() === here) a.classList.add('current');
    });

    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault(); open(); return;
      }
      if (!finder.classList.contains('open')) return;
      if (e.key === 'Escape') { close(); return; }
      var vis = visible();
      if (!vis.length) return;
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        var cur = vis.indexOf(document.activeElement);
        var i = cur < 0 ? 0 : cur + (e.key === 'ArrowDown' ? 1 : -1);
        i = Math.max(0, Math.min(vis.length - 1, i));
        setActive(vis[i]); vis[i].focus();
      } else if (e.key === 'Enter') {
        var act = finder.querySelector('.finder-group a.active') || vis[0];
        if (act) window.location.href = act.getAttribute('href');
      }
    });
  }

  // 首页：卷导航点击跳转，按 nav 实际高度动态偏移，避免被吸顶 nav 遮挡
  var volNav = document.querySelector('.vol-nav');
  if (volNav) {
    volNav.addEventListener('click', function (e) {
      var a = e.target.closest && e.target.closest('a[href^="#"]');
      if (!a) return;
      var id = a.getAttribute('href').slice(1);
      var target = id && document.getElementById(id);
      if (!target) return;
      e.preventDefault();
      var sticky = getComputedStyle(volNav).position === 'sticky';
      var offset = 48 + 8; // toolbar(3rem) + 间隙
      if (sticky) offset += volNav.offsetHeight;
      var y = target.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top: y, behavior: 'smooth' });
    });
  }

  // 首页：回到顶部悬浮按钮（滚动后出现）
  var toTop = document.getElementById('to-top');
  if (toTop) {
    var syncTop = function () {
      if (window.scrollY > 400) toTop.classList.add('show');
      else toTop.classList.remove('show');
    };
    window.addEventListener('scroll', syncTop, { passive: true });
    window.addEventListener('resize', syncTop);
    syncTop();
    toTop.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  }
})();
