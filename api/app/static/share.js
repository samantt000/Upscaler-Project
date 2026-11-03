"use strict";

const input = document.getElementById("invite-link");
const status = document.getElementById("copy-status");

document.getElementById("copy-btn").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(input.value);
    status.textContent = "Ссылка скопирована.";
  } catch {
    // Буфер обмена недоступен (страница открыта по HTTP не с localhost): выделяем текст для Ctrl+C
    input.select();
    status.textContent = "Нажмите Ctrl+C, чтобы скопировать выделенную ссылку.";
  }
});
