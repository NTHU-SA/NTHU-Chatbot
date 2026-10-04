/* A bounded renderer for the components emitted by our production Flex builders. */
(() => {
  "use strict";
  const sizes = { xxs: "10px", xs: "12px", sm: "14px", md: "16px", lg: "18px", xl: "20px", xxl: "24px", "3xl": "30px" };
  const spaces = { none: "0px", xs: "4px", sm: "8px", md: "12px", lg: "16px", xl: "20px", xxl: "24px" };
  const bubbleSizes = { nano: 120, micro: 160, deca: 196, hecto: 240, kilo: 260, mega: 300, giga: 360 };
  const gallery = document.getElementById("gallery");
  const picker = document.getElementById("sample-picker");
  const detail = document.getElementById("action-detail");
  const samples = window.FLEX_PREVIEW;

  const length = (value) => spaces[value] || value;
  function inspect(action) {
    const kind = { uri: "開啟網址", postback: "LINE postback", message: "傳送文字" }[action.type];
    const payload = action.uri || action.data || action.text;
    detail.textContent = `${action.label || "動作"} · ${kind}\n${payload}\n${action.displayText || ""}`.trim();
    detail.scrollIntoView({ block: "center" });
  }
  function decorate(element, node) {
    if (node.backgroundColor) element.style.backgroundColor = node.backgroundColor;
    if (node.color) element.style.color = node.color;
    if (node.paddingAll) element.style.padding = length(node.paddingAll);
    for (const [key, property] of Object.entries({
      paddingTop: "paddingTop", paddingBottom: "paddingBottom",
      paddingStart: "paddingLeft", paddingEnd: "paddingRight",
      cornerRadius: "borderRadius", borderWidth: "borderWidth", borderColor: "borderColor",
      margin: "marginTop",
    })) {
      if (node[key]) element.style[property] = length(node[key]);
    }
    if (node.borderWidth) element.style.borderStyle = "solid";
  }
  function render(node) {
    let element;
    if (node.type === "box") {
      element = document.createElement("div");
      element.className = "flex-box";
      element.style.flexDirection = node.layout === "vertical" ? "column" : "row";
      if (node.spacing) element.style.gap = length(node.spacing);
      if (node.alignItems) element.style.alignItems = node.alignItems;
      if (node.justifyContent) element.style.justifyContent = node.justifyContent;
      for (const child of node.contents) {
        const rendered = render(child);
        if (node.layout !== "vertical") rendered.style.flex = child.flex === 0 ? "0 0 auto" : `${child.flex ?? 1} 1 0`;
        element.append(rendered);
      }
    } else if (node.type === "text") {
      element = document.createElement("p");
      element.className = "flex-text";
      element.textContent = node.text;
      element.style.fontSize = sizes[node.size] || node.size || sizes.md;
      element.style.fontWeight = node.weight === "bold" ? "700" : "400";
      element.style.textAlign = node.align === "end" ? "right" : node.align === "center" ? "center" : "left";
      if (node.maxLines) {
        Object.assign(element.style, {
          display: "-webkit-box", WebkitBoxOrient: "vertical",
          WebkitLineClamp: String(node.maxLines), overflow: "hidden",
        });
      }
    } else if (node.type === "button") {
      element = document.createElement("button");
      element.type = "button";
      element.className = "flex-button";
      element.dataset.style = node.style || "link";
      element.style.setProperty("--flex-button-height", node.height === "md" ? "52px" : "44px");
      element.textContent = node.action.label;
      element.addEventListener("click", () => inspect(node.action));
    } else if (node.type === "image") {
      element = document.createElement("img");
      element.className = "flex-image";
      element.src = node.url;
      element.alt = "訊息附圖";
      element.loading = "lazy";
      element.style.aspectRatio = (node.aspectRatio || "1:1").replace(":", "/");
    } else if (node.type === "separator") {
      element = document.createElement("hr");
      element.className = "flex-separator";
    } else {
      throw new Error(`Unsupported Flex component: ${node.type}`);
    }
    decorate(element, node);
    if (node.type === "button" && node.style === "primary") {
      element.style.backgroundColor = node.color;
      element.style.color = "#FFFFFF";
    }
    if (node.action && node.type !== "button") {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "flex-button";
      link.dataset.style = "link";
      link.style.color = "inherit";
      link.textContent = node.text;
      link.addEventListener("click", () => inspect(node.action));
      element.textContent = "";
      element.append(link);
    }
    return element;
  }
  function renderBubble(card) {
    const element = document.createElement("div");
    element.className = "flex-bubble";
    element.dataset.size = card.size || "mega";
    element.style.setProperty("--flex-bubble-width", `${bubbleSizes[card.size || "mega"]}px`);
    for (const part of ["header", "hero", "body", "footer"]) {
      if (!card[part]) continue;
      const section = render(card[part]);
      const style = card.styles?.[part];
      if (style?.backgroundColor) section.style.backgroundColor = style.backgroundColor;
      if (style?.separator) section.style.borderTop = `1px solid ${style.separatorColor || "#DED9E7"}`;
      element.append(section);
    }
    return element;
  }
  function renderMessage(message) {
    const element = document.createElement("div");
    element.className = "preview-message";
    const tools = document.createElement("div");
    tools.className = "preview-message-tools";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "preview-copy";
    copy.textContent = "複製 JSON";
    copy.setAttribute("aria-label", `複製${message.altText}的 Flex JSON`);
    const feedback = document.createElement("span");
    feedback.className = "preview-copy-feedback";
    feedback.setAttribute("role", "status");
    const fallback = document.createElement("textarea");
    fallback.className = "preview-copy-fallback";
    fallback.readOnly = true;
    fallback.hidden = true;
    fallback.rows = 6;
    fallback.setAttribute("aria-label", `${message.altText}的 Flex JSON，供手動複製`);
    const json = JSON.stringify(message.contents, null, 2);
    copy.addEventListener("click", async () => {
      copy.disabled = true;
      copy.textContent = "複製中…";
      feedback.textContent = "";
      try {
        if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
        await navigator.clipboard.writeText(json);
        feedback.textContent = "已複製，可貼進 Flex Simulator。";
        fallback.hidden = true;
      } catch (error) {
        console.warn("Flex JSON clipboard copy failed:", error instanceof Error ? error.name : "UnknownError");
        feedback.textContent = "無法自動複製，請選取下方 JSON 手動複製。";
        fallback.value = json;
        fallback.hidden = false;
        fallback.focus();
        fallback.select();
      } finally {
        copy.disabled = false;
        copy.textContent = "複製 JSON";
      }
    });
    tools.append(copy, feedback);
    element.append(tools, fallback);
    const cards = message.contents.type === "carousel" ? message.contents.contents : [message.contents];
    const row = document.createElement("div");
    if (cards.length > 1) {
      row.className = "flex-carousel";
      row.tabIndex = 0;
      row.setAttribute("role", "region");
      row.setAttribute("aria-label", `${message.altText}，可左右捲動`);
    }
    cards.forEach((card) => row.append(renderBubble(card)));
    element.append(row);
    if (message.quickReply) {
      const replies = document.createElement("div");
      replies.className = "preview-quick-replies";
      for (const item of message.quickReply.items) {
        const control = document.createElement("button");
        control.type = "button";
        control.textContent = item.action.label;
        control.addEventListener("click", () => inspect(item.action));
        replies.append(control);
      }
      element.append(replies);
    }
    return element;
  }
  if (!Array.isArray(samples)) {
    detail.textContent = "預覽資料載入失敗。請執行 uv run python scripts\\build_flex_preview.py 後重新載入。";
    return;
  }
  const downloads = [];
  for (const sample of samples) {
    const option = document.createElement("option");
    option.value = sample.id;
    option.textContent = sample.title;
    picker.append(option);
    const section = document.createElement("section");
    section.className = "preview-sample";
    section.id = sample.id;
    if (sample.messages.some((m) => m.contents.type === "carousel" && m.contents.contents.length > 1)) section.classList.add("wide");
    const heading = document.createElement("div");
    heading.className = "preview-sample-heading";
    const title = document.createElement("h2");
    title.textContent = sample.title;
    const download = document.createElement("a");
    download.className = "preview-download";
    download.textContent = "下載 JSON";
    download.setAttribute("aria-label", `下載${sample.title} JSON`);
    download.download = `flex-${sample.id}.json`;
    const url = URL.createObjectURL(new Blob([JSON.stringify(sample.messages, null, 2)], { type: "application/json" }));
    downloads.push(url);
    download.href = url;
    heading.append(title, download);
    const description = document.createElement("p");
    description.textContent = sample.description;
    const stage = document.createElement("div");
    stage.className = "preview-stage";
    sample.messages.forEach((message) => stage.append(renderMessage(message)));
    section.append(heading, description, stage);
    gallery.append(section);
  }
  picker.addEventListener("change", () => {
    for (const section of gallery.children) section.hidden = picker.value !== "all" && section.id !== picker.value;
  });
  window.addEventListener("pagehide", (event) => {
    if (!event.persisted) downloads.forEach((url) => URL.revokeObjectURL(url));
  });
})();
