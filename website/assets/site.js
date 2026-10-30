(function () {
  "use strict";

  var navToggle = document.querySelector(".nav-toggle");
  var primaryNav = document.getElementById("primary-nav");

  if (navToggle && primaryNav) {
    navToggle.addEventListener("click", function () {
      var isOpen = navToggle.getAttribute("aria-expanded") === "true";
      navToggle.setAttribute("aria-expanded", String(!isOpen));
      navToggle.setAttribute("aria-label", isOpen ? "Open navigation" : "Close navigation");
      primaryNav.classList.toggle("open", !isOpen);
    });

    primaryNav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.setAttribute("aria-label", "Open navigation");
        primaryNav.classList.remove("open");
      });
    });
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function (resolve, reject) {
      var helper = document.createElement("textarea");
      helper.value = text;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      try {
        var ok = document.execCommand("copy");
        document.body.removeChild(helper);
        if (ok) resolve();
        else reject(new Error("Copy command was not available."));
      } catch (error) {
        document.body.removeChild(helper);
        reject(error);
      }
    });
  }

  document.querySelectorAll("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var target = document.querySelector(button.getAttribute("data-copy"));
      if (!target) return;
      var original = button.textContent;
      copyText(target.textContent).then(function () {
        button.textContent = "Copied";
        window.setTimeout(function () { button.textContent = original; }, 1500);
      }).catch(function () {
        button.textContent = "Select text";
        window.setTimeout(function () { button.textContent = original; }, 1800);
      });
    });
  });

  var feedbackForm = document.getElementById("feedback-form");
  if (feedbackForm) {
    var status = document.getElementById("feedback-status");
    var copyButton = document.getElementById("copy-feedback");
    var openButton = document.getElementById("open-feedback");

    function fieldValue(name) {
      var field = feedbackForm.elements.namedItem(name);
      return field ? String(field.value || "").trim() : "";
    }

    function buildFeedback() {
      var lines = [
        "# Synapse product feedback",
        "",
        "### What were you trying to do?",
        fieldValue("area") || "(Not specified)",
        "",
        "### How did it go?",
        fieldValue("outcome") || "(Not specified)",
        "",
        "### What felt confusing, slow, broken, or harder than expected?",
        fieldValue("stuck") || "(No details provided)",
        "",
        "### What worked well or felt useful?",
        fieldValue("good") || "(No details provided)",
        "",
        "### What would you improve or want to see next?",
        fieldValue("next") || "(No details provided)",
        "",
        "### Environment",
        fieldValue("environment") || "(Not specified)",
        "",
        "---",
        "Please remove secrets, personal data, prompts, raw tool arguments, and customer information before submitting."
      ];
      return lines.join("\n");
    }

    if (copyButton) {
      copyButton.addEventListener("click", function () {
        copyText(buildFeedback()).then(function () {
          status.textContent = "Copied. You can paste this wherever you want to share it.";
        }).catch(function () {
          status.textContent = "Clipboard access was unavailable. You can still select and copy your answers manually.";
        });
      });
    }

    if (openButton) {
      openButton.addEventListener("click", function () {
        var titleSeed = fieldValue("area") || "developer experience";
        var title = "[Feedback] " + titleSeed;
        var query = new URLSearchParams();
        query.set("template", "product_feedback.md");
        query.set("title", title.slice(0, 110));
        query.set("body", buildFeedback());
        status.textContent = "Opening a GitHub draft. Review the content there and submit only if you are comfortable.";
        window.open("https://github.com/721189/Synapse-Memory/issues/new?" + query.toString(), "_blank", "noopener,noreferrer");
      });
    }
  }

  if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("reveal");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.08 });
    document.querySelectorAll(".problem-card, .capability-card, .security-points article, .docs-section").forEach(function (node) {
      observer.observe(node);
    });
  }

  var currentHash = window.location.hash;
  if (currentHash && document.querySelector('.docs-sidebar a[href="' + currentHash + '"]')) {
    document.querySelector('.docs-sidebar a[href="' + currentHash + '"]').classList.add("active");
  }
})();