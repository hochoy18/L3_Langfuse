// Lesson TOC + scrollspy
// 在每节 lesson 的 main.content 里扫描 h2，自动生成左侧目录，滚动时高亮当前段。
(function () {
  var headings = document.querySelectorAll('main.content h2');
  var toc = document.getElementById('toc-list');
  if (!toc || !headings.length) return;

  var items = [];
  headings.forEach(function (h, i) {
    if (!h.id) h.id = 'sec-' + i;
    var text = h.textContent.trim().replace(/^\d+\.\s*/, '');
    items.push({ id: h.id, el: h });
    var li = document.createElement('li');
    var a = document.createElement('a');
    a.href = '#' + h.id;
    a.textContent = text;
    li.appendChild(a);
    toc.appendChild(li);
  });

  var links = Array.from(toc.querySelectorAll('a'));

  function update() {
    var fromTop = window.scrollY + 96;
    var current = items[0].id;
    for (var i = 0; i < items.length; i++) {
      if (items[i].el.offsetTop <= fromTop) current = items[i].id;
    }
    links.forEach(function (l) {
      l.classList.toggle('active', l.getAttribute('href') === '#' + current);
    });
  }

  window.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', update);
  update();
})();