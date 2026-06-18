window.addEventListener("message", (event) => {
  if (event.source !== window || !event.data || event.data.source !== "super-browser-content") {
    return;
  }
  chrome.runtime.sendMessage(event.data);
});
