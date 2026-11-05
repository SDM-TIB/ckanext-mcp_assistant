/* ckanext-mcp_assistant: conversation UI for the AI Assistant.
 *
 * The conversation lives in the browser: earlier questions and answers are
 * sent with each new question, so follow-up questions work. "New
 * conversation" (or reloading the page) starts over.
 *
 * Options (data-module-* attributes):
 *   askUrl           URL of POST /assistant/ask
 *   initialQuestion  asked right away when not empty (from ?q=...)
 *   i18n             translated strings rendered by the template
 */
ckan.module("mcp-assistant", function ($) {
  "use strict";

  var ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"};
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }

  /* Small, safe Markdown subset: everything is escaped first, then links,
   * bold, inline code, headings, bullet / numbered lists and paragraphs. */
  function inline(text) {
    return text
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
               '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
      .replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g,
               '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function render(md) {
    var lines = esc(md).split(/\r?\n/), html = [], list = null, para = [];
    function flushPara() { if (para.length) { html.push("<p>" + inline(para.join("<br>")) + "</p>"); para = []; } }
    function flushList() { if (list) { html.push("</" + list + ">"); list = null; } }
    lines.forEach(function (line) {
      var m;
      if ((m = line.match(/^\s*[-*•]\s+(.*)$/)) || (m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
        var type = /^\s*\d/.test(line) ? "ol" : "ul";
        flushPara();
        if (list !== type) { flushList(); html.push("<" + type + ">"); list = type; }
        html.push("<li>" + inline(m[1]) + "</li>");
      } else if ((m = line.match(/^#{1,6}\s+(.*)$/))) {
        flushPara(); flushList();
        html.push("<p><strong>" + inline(m[1]) + "</strong></p>");
      } else if (/^\s*$/.test(line)) {
        flushPara(); flushList();
      } else {
        flushList(); para.push(line);
      }
    });
    flushPara(); flushList();
    return html.join("");
  }

  /* CKAN >= 2.10 protects POST requests with a CSRF token; 2.9 does not. */
  function csrfHeaders() {
    var field = $('meta[name="csrf_field_name"]').attr("content");
    var token = field && $('meta[name="' + field + '"]').attr("content");
    return token ? {"X-CSRFToken": token} : {};
  }

  return {
    options: {askUrl: null, initialQuestion: "", i18n: {}},

    initialize: function () {
      $.proxyAll(this, /_on/);
      this.history = [];
      this.busy = false;
      this.t = $.extend({
        searching: "Searching the portal…", noAnswer: "(no answer)",
        unreachable: "Could not reach the server.", stepsOne: "How this answer was found: 1 tool call",
        stepsMany: "How this answer was found: {n} tool calls", error: "error",
        you: "You", assistant: "Assistant", empty: "Please type a question."
      }, typeof this.options.i18n === "object" ? this.options.i18n : {});

      this.$log = this.$(".mcp-assistant__log");
      this.$form = this.$(".mcp-assistant__form");
      this.$input = this.$(".mcp-assistant__input");
      this.$ask = this.$(".mcp-assistant__ask");
      this.$status = this.$(".mcp-assistant__status");
      this.$new = this.$(".mcp-assistant__new");
      this.$examples = this.$(".mcp-assistant__examples");

      this.$form.on("submit", this._onSubmit);
      this.$new.on("click", this._onNew);
      this.$(".mcp-assistant__example").on("click", this._onExample);

      // CKAN turns an empty data-module-* attribute into `true`: accept strings only
      var q = this.options.initialQuestion;
      var initial = typeof q === "string" ? q.trim() : (typeof q === "number" ? String(q) : "");
      if (initial) { this.ask(initial); }
    },

    _onSubmit: function (e) {
      e.preventDefault();
      this.ask(String(this.$input.val() || "").trim());
    },

    _onExample: function (e) {
      this.ask($(e.currentTarget).text().trim());
    },

    _onNew: function () {
      this.history = [];
      this.$log.empty();
      this.$new.prop("hidden", true);
      this.$examples.prop("hidden", false);
      this.$input.val("").trigger("focus");
    },

    addTurn: function (question) {
      /* "sr-only" is Bootstrap 3 (CKAN 2.9), "visually-hidden" Bootstrap 5 (CKAN >= 2.10).
       * The icons exist in Font Awesome 4.7 (CKAN 2.9) and 6 (CKAN >= 2.10). */
      var $turn = $('<div class="mcp-assistant__turn">' +
        '<div class="mcp-assistant__q"><i class="fa fa-user mcp-assistant__q-icon" aria-hidden="true"></i>' +
        '<span class="sr-only visually-hidden"></span></div>' +
        '<div class="mcp-assistant__a mcp-assistant__a--pending"><span class="sr-only visually-hidden"></span>' +
        '<i class="fa fa-spinner fa-spin" aria-hidden="true"></i></div></div>');
      $turn.find(".mcp-assistant__q .sr-only").text(this.t.you + ": ");
      $turn.find(".mcp-assistant__q").append(document.createTextNode(question));
      $turn.find(".mcp-assistant__a .sr-only").text(this.t.assistant + ": ");
      this.$log.append($turn);
      return $turn;
    },

    showSteps: function ($turn, steps) {
      if (!steps || !steps.length) { return; }
      var t = this.t;
      var summary = steps.length === 1 ? t.stepsOne : t.stepsMany.replace("{n}", steps.length);
      var items = steps.map(function (s) {
        return "<li><code>" + esc(s.tool) + "(" + esc(s.args || "") + ")</code><br>" +
               (s.error ? '<span class="text-danger">' + esc(t.error) + ":</span> " : "") +
               esc(s.summary || "") + "</li>";
      }).join("");
      $turn.append('<details class="mcp-assistant__steps"><summary>' + esc(summary) +
                   "</summary><ol>" + items + "</ol></details>");
    },

    ask: function (question) {
      if (this.busy) { return; }
      if (!question) { this.$status.text(this.t.empty); this.$input.trigger("focus"); return; }
      var self = this, $turn = this.addTurn(question), $a = $turn.find(".mcp-assistant__a");
      this.busy = true;
      this.$input.val("");
      this.$ask.prop("disabled", true);
      this.$examples.prop("hidden", true);
      this.$status.text(this.t.searching);

      $.ajax({
        url: this.options.askUrl,
        method: "POST",
        contentType: "application/json",
        dataType: "json",
        headers: csrfHeaders(),
        data: JSON.stringify({question: question, history: this.history})
      }).done(function (data) {
        var answer = (data && data.answer) || "";
        $a.removeClass("mcp-assistant__a--pending").html(render(answer || self.t.noAnswer));
        self.history.push({role: "user", content: question}, {role: "assistant", content: answer});
        self.$new.prop("hidden", false);
        self.showSteps($turn, data && data.steps);
      }).fail(function (xhr) {
        var msg = (xhr.responseJSON && xhr.responseJSON.error) || self.t.unreachable;
        $a.removeClass("mcp-assistant__a--pending").addClass("mcp-assistant__a--error").text(msg);
      }).always(function () {
        self.busy = false;
        self.$status.text("");
        self.$ask.prop("disabled", false);
        self.$input.trigger("focus");
        if (self.$form[0].scrollIntoView) { self.$form[0].scrollIntoView({block: "nearest"}); }
      });
    }
  };
});
