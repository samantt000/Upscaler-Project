"use strict";

const fmt = (v, digits = 2, suffix = "") => (v === null || v === undefined ? "—" : `${Number(v).toFixed(digits)}${suffix}`);
const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);

function card(title, rows) {
  const section = document.createElement("section");
  section.className = "card";
  const h2 = document.createElement("h2");
  h2.textContent = title;
  const table = document.createElement("table");
  for (const [label, value, cls] of rows) {
    const tr = table.insertRow();
    const th = document.createElement("th");
    th.textContent = label;
    const td = tr.insertCell();
    td.textContent = value;
    if (cls) td.className = cls;
    tr.prepend(th);
  }
  if (!rows.length) {
    const tr = table.insertRow();
    tr.insertCell().textContent = "Нет данных";
  }
  section.append(h2, table);
  return section;
}

function counts(obj) {
  return Object.entries(obj).sort((a, b) => b[1] - a[1]).map(([k, v]) => [k, String(v)]);
}

async function refresh() {
  let m;
  try {
    const res = await fetch("/api/metrics");
    m = await res.json();
  } catch {
    document.getElementById("updated").textContent = "Сервис недоступен";
    return;
  }

  const w = m.worker;
  const drift = m.quality.drift;
  const timeRows = Object.entries(m.processing_time).flatMap(([device, t]) => [
    [`${device}: задач`, String(t.count)],
    [`${device}: p50`, fmt(t.p50, 2, " с")],
    [`${device}: p95`, fmt(t.p95, 2, " с")],
    [`${device}: макс.`, fmt(t.max, 2, " с")],
  ]);

  document.getElementById("metrics").replaceChildren(
    card("Воркер и очередь", [
      ["Состояние", w?.ready ? "готов" : (w ? "загрузка модели" : "нет связи"), w?.ready ? "ok" : "bad"],
      ["Устройство", w?.device ?? "—"],
      ["Модель", w?.model ?? "—"],
      ["В очереди", String(m.queue.length)],
      ["Обрабатывается", String(m.queue.processing)],
      ["Лимит очереди", String(m.queue.limit)],
    ]),
    card("Время обработки", [...timeRows, ["Ожидание в очереди, p95", fmt(m.wait_time_p95, 2, " с")]]),
    card("Задачи", [
      ["Выполнено", String(m.jobs.done)],
      ["Ошибок", String(m.jobs.errors)],
      ["Доля ошибок", pct(m.jobs.error_rate)],
      ["Отклонено при загрузке", String(m.jobs.upload_rejected)],
      ["Скачано результатов", String(m.jobs.downloaded)],
      ["Доля скачиваний (NFR-011)", pct(m.jobs.download_rate)],
    ]),
    card("Контроль качества (BRISQUE)", [
      ["Среднее", fmt(m.quality.brisque_mean)],
      ["Задач с оценкой", String(m.quality.brisque_count)],
      ["Базовая линия", drift ? fmt(drift.baseline) : "не задана"],
      ["Отклонение", drift ? pct(drift.deviation) : "—", drift ? (drift.drift ? "bad" : "ok") : ""],
    ]),
    card("Детектор лиц", [
      ["Задач с лицами", String(m.faces.jobs_with_faces)],
      ["Флаг «зона генерации»", String(m.faces.flagged_zone)],
      ["Среднее снижение достройки", pct(m.faces.mean_diff_reduction)],
    ]),
    card("Форматы", counts(m.inputs.formats)),
    card("Разрешения входа (по большей стороне)", counts(m.inputs.resolutions)),
    card("Коэффициенты", counts(m.inputs.scales)),
  );
  document.getElementById("updated").textContent = `Обновлено: ${new Date().toLocaleTimeString("ru-RU")}`;
}

refresh();
setInterval(refresh, 5000);
