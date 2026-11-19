(function () {
  "use strict";

  var hdr = document.getElementById("hdr");
  var bar = document.getElementById("progressBar");
  var burger = document.getElementById("burger");
  var menu = document.getElementById("mobileMenu");

  /* ---------- 顶部状态 + 滚动进度（rAF 节流） ---------- */
  var ticking = false;
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(function () {
      var y = window.pageYOffset || document.documentElement.scrollTop || 0;
      if (hdr) hdr.classList.toggle("scrolled", y > 8);
      var max = document.documentElement.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(1, y / max) : 0;
      if (bar) bar.style.transform = "scaleX(" + p + ")";
      ticking = false;
    });
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* ---------- 滚动揭示 ---------- */
  var reveals = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach(function (el) { el.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          e.target.classList.add("in");
          io.unobserve(e.target);
        }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    reveals.forEach(function (el) { io.observe(el); });

    // 首屏元素直接显示，避免进场延迟
    Array.prototype.forEach.call(document.querySelectorAll(".hero .reveal"), function (el) {
      el.classList.add("in");
      io.unobserve(el);
    });
  }

  /* ---------- 导航当前段落高亮 ---------- */
  var links = Array.prototype.slice.call(document.querySelectorAll(".nav-link"));
  var secs = links
    .map(function (a) { return document.querySelector(a.getAttribute("href")); })
    .filter(Boolean);

  if ("IntersectionObserver" in window && secs.length) {
    var io2 = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        var id = "#" + e.target.id;
        links.forEach(function (l) {
          l.classList.toggle("active", l.getAttribute("href") === id);
        });
      });
    }, { rootMargin: "-45% 0px -50% 0px" });
    secs.forEach(function (s) { io2.observe(s); });
  }

  /* ---------- 移动端菜单 ---------- */
  function setMenu(open) {
    if (!burger || !menu) return;
    burger.setAttribute("aria-expanded", String(open));
    burger.setAttribute("aria-label", open ? "关闭菜单" : "打开菜单");
    menu.hidden = !open;
  }

  if (burger && menu) {
    burger.addEventListener("click", function () {
      setMenu(burger.getAttribute("aria-expanded") !== "true");
    });
    menu.addEventListener("click", function (e) {
      if (e.target.closest("a")) setMenu(false);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") setMenu(false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 900) setMenu(false);
    }, { passive: true });
  }
})();
