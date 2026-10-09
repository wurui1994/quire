(function(){
  // ---- 缩进切换 ----
  var indentBtn = document.getElementById('btn-indent');
  if (indentBtn) {
    if (new URLSearchParams(location.search).get('indent') !== null)
      document.body.classList.add('indent'), indentBtn.classList.add('active');
    indentBtn.addEventListener('click', function(){
      document.body.classList.toggle('indent');
      this.classList.toggle('active');
    });
  }
  // ---- 暗色模式 ----
  var darkBtn = document.getElementById('btn-dark');
  if (darkBtn) {
    if (window.matchMedia('(prefers-color-scheme:dark)').matches)
      document.body.classList.add('dark'), darkBtn.classList.add('active');
    darkBtn.addEventListener('click', function(){
      document.body.classList.toggle('dark');
      this.classList.toggle('active');
    });
  }
  // ---- TOC 侧边栏 ----
  var tocBtn = document.getElementById('btn-toc');
  var sidebar = document.querySelector('.sidebar');
  if (tocBtn && sidebar) {
    if (window.innerWidth > 960) {
      sidebar.classList.add('open');
      tocBtn.classList.add('active');
    }
    tocBtn.addEventListener('click', function(){
      sidebar.classList.toggle('open');
      this.classList.toggle('active');
    });
  }
  // ---- 目录自动跟随 ----
  // 开启后，侧栏随阅读位置滚动，使当前章节项保持在视野中央。
  var followBtn = document.getElementById('btn-follow');
  var followMode = false;
  // navigating：点击目录跳转进行中。期间内容页正在滚动到目标，目录保持不动、
  // 高亮不刷新；由用户主动滚动内容（wheel/touch/key）解除后，跟随模式才重新生效。
  var navigating = false;
  function syncFollow() {
    if (!followMode || !sidebar) return;
    var a = sidebar.querySelector('a.active');
    if (!a) return;
    // 跟随模式：先展开当前项所在的全部折叠祖先（合订本目录默认整棵折叠，
    // 当前项藏在 display:none 分支里 getBoundingClientRect 为 0，居中会被钳到顶），
    // 再将其居中。该展开只发生在“用户主动滚动内容”触发的跟随中，点击跳转
    // （navigating=true）期间 syncFollow 根本不会被调用，故绝不因点击而炸开树。
    var node = a.closest('li');
    while (node && node !== sidebar) {
      if (node.classList.contains('collapsed')) {
        node.classList.remove('collapsed');
        var b = node.querySelector(':scope > .row > button.tw');
        if (b) b.setAttribute('aria-expanded', 'true');
      }
      var parentUl = node.parentElement;
      node = parentUl ? parentUl.closest('li') : null;
    }
    var sr = sidebar.getBoundingClientRect();
    var tr = a.getBoundingClientRect();
    if (tr.height === 0) return;                           // 不可见则不出手
    var pad = sidebar.clientHeight * 0.2;
    // 当前项已在侧栏舒适可视区内则不动，避免每次切换都居中造成的抖动
    if (tr.top - sr.top >= pad && tr.bottom - sr.top <= sidebar.clientHeight - pad) return;
    var delta = (tr.top - sr.top) - (sidebar.clientHeight / 2) + (tr.height / 2);
    sidebar.scrollTo({ top: Math.max(0, sidebar.scrollTop + delta), behavior: 'auto' });
  }
  if (followBtn) {
    followBtn.addEventListener('click', function(){
      followMode = !followMode;
      this.classList.toggle('active', followMode);
      this.setAttribute('aria-pressed', String(followMode));
      if (followMode) syncFollow();   // 开启时立即对齐当前章节
    });
  }
  // ---- 阅读进度 ----
  var bar = document.querySelector('.progress-bar');
  if (bar) {
    window.addEventListener('scroll', function(){
      var h = document.documentElement.scrollHeight - window.innerHeight;
      bar.style.width = h > 0 ? (window.scrollY / h * 100) + '%' : '0';
    }, {passive: true});
  }
  // ---- TOC 展开 / 收起（多级目录，按职责分离设计） ----
  var sidebarEl = document.querySelector('.sidebar');
  if (sidebarEl) {
    // 仅切换按钮控制展开/收起；标签保留锚点跳转语义
    sidebarEl.addEventListener('click', function(e){
      var btn = e.target.closest('button.tw');
      if (!btn) return;
      var li = btn.closest('li.node');
      if (!li) return;
      var collapsed = li.classList.toggle('collapsed');
      btn.setAttribute('aria-expanded', String(!collapsed));
    });

    // 全局开关：纯切换，只动【最顶层容器】，文章与节一律不碰。
    //   all-one ：顶层是卷(vol-link)，全局只开关卷；文章/节的手动状态全保留。
    //   单 vol  ：顶层是文章(md)，全局只开关文章；节的手动状态保留。
    // “展开到 md 级别”= 打开卷（露出文章标题）、文章/节保持你手动的状态；
    // “收起全部”= 关掉最顶层容器，下面一切原样不动。节/文章永不被动。
    var collapseAllBtn = document.getElementById('btn-collapse-all');
    if (collapseAllBtn) {
      // 默认全收起（两种模式默认顶层容器都是 collapsed 态）→ 初始为 false
      var globalExpanded = false;
      // 仅作用于根 ul 的直接子节点（最顶层容器），绝不进入文章/节内部
      function setGlobal(expand) {
        var rootUl = sidebarEl.querySelector('ul');
        if (!rootUl) return;
        rootUl.querySelectorAll(':scope > li.node').forEach(function(n){
          n.classList.toggle('collapsed', !expand);
          var b = n.querySelector(':scope > .row > button.tw');
          if (b) b.setAttribute('aria-expanded', String(expand));
        });
      }
      collapseAllBtn.addEventListener('click', function(e){
        e.stopPropagation();
        globalExpanded = !globalExpanded;
        setGlobal(globalExpanded);
        collapseAllBtn.classList.toggle('collapsed', !globalExpanded);
        collapseAllBtn.setAttribute('aria-label', globalExpanded ? '收起全部' : '展开全部');
      });
      // 初始标签与默认 DOM 对齐
      collapseAllBtn.classList.toggle('collapsed', !globalExpanded);
      collapseAllBtn.setAttribute('aria-label', globalExpanded ? '收起全部' : '展开全部');
    }
  }

  // ---- TOC 高亮当前章节 ----
  var links = document.querySelectorAll('.sidebar a[href^="#"]');
  if (links.length) {
    var sections = [];
    links.forEach(function(a){
      var el = document.getElementById(a.getAttribute('href').slice(1));
      if (el) sections.push({el: el, a: a});
    });
    var lastActiveA = null;
    // 仅更新高亮；目录（sidebar）的滚动居中由 syncFollow 单独负责，
    // 且只在被动阅读滚动时调用，点击跳转期间绝不调用。
    function setActive(a) {
      if (a === lastActiveA) return;        // 同一章节不重复滚动，避免抖动
      links.forEach(function(x){ x.classList.remove('active'); });
      a.classList.add('active');
      lastActiveA = a;
    }
    // 根据滚动位置唯一确定“当前章节”：最后一个顶部越过参考线（视口 25% 处）
    // 的章节。无论视口内同时有多少章节，都只高亮唯一一项，杜绝闪烁 / 误选相邻节。
    function computeActive() {
      if (!sections.length) return null;
      var line = window.innerHeight * 0.25;
      var chosen = sections[0];
      for (var i = 0; i < sections.length; i++) {
        if (sections[i].el.getBoundingClientRect().top - line <= 0) chosen = sections[i];
        else break;                          // 章节按文档顺序，之后只会更靠下
      }
      return chosen.a;
    }
    // 滚动时更新当前项（rAF 节流）。点击跳转（navigating）期间完全跳过：
    // 既不刷新高亮、也不滚动目录，保证用户点击后目录纹丝不动。
    var rafPending = false;
    window.addEventListener('scroll', function(){
      if (navigating || rafPending) return;
      rafPending = true;
      requestAnimationFrame(function(){
        rafPending = false;
        setActive(computeActive());
        syncFollow();                        // 被动阅读滚动才跟随居中
      });
    }, {passive: true});
    // 用户以滚轮 / 触摸 / 方向键主动滚动内容时，立即接管（解除跳转态）。
    // 仅由用户输入释放：点击跳转的整段平滑滚动（含落地后的拖尾 scroll）都
    // 不会触发 syncFollow，故绝不因点击而展开 / 移动目录；只有用户真正移动
    // 内容页后，跟随模式才重新生效。无任何计时器。
    ['wheel', 'touchstart', 'keydown'].forEach(function(type){
      window.addEventListener(type, function(){ navigating = false; }, {passive: true});
    });
    // 初始对齐到当前阅读位置
    setActive(computeActive());
    if (followMode) syncFollow();
    // 点击目录跳转：立即高亮被点项并进入跳转态。目录（sidebar）在跳转期间保持不动、
    // 不展开；由用户主动滚动内容（wheel/touch/key）解除后，跟随模式才重新生效。
    sidebar.addEventListener('click', function(e){
      var a = e.target.closest('a[href^="#"]');
      if (!a) return;
      var el = document.getElementById(a.getAttribute('href').slice(1));
      setActive(a);
      if (!el) { navigating = false; return; }
      // 目标与当前位置不同 → 将发生平滑滚动，进入跳转态锁定目录；已在该位置则不用锁。
      navigating = Math.abs(el.getBoundingClientRect().top) > 2;
    });
  }
})();
