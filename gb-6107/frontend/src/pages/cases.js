import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { status } from "../components/common/status.js";
import { table } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { navigate } from "../router/router.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";

function queueForm(config, selected) {
  const form = el("form", {class: "queue-form"},
    field(config.parameter_label, el("input", {
      name: "parameter", type: "number", min: 1, max: config.max_parameter, required: true,
      value: selected.page_size ?? config.default_parameter, disabled: true,
    }), `页容量在质保批登记时固定为 ${selected.page_size ?? config.default_parameter}，版本间保持一致。`),
    el("button", {type: "submit"}, "提交出货抽检"), errorText());
  submit(form, async () => {
    const run = await api.enqueue(selected.id, {parameter: selected.page_size ?? config.default_parameter});
    navigate(`runs/${run.id}`);
  });
  return form;
}

function parseCorrection(text) {
  // 每行形如 板件编号=整数厘瓦（如 D3-0109=42000），空行与 # 注释忽略。
  const readings = [];
  const seen = new Set();
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^([A-Za-z0-9_-]{1,32})\s*=\s*(\d+)$/);
    if (!match) throw new Error(`无法解析的复测行：${line}（应为 编号=厘瓦，如 D3-0109=42000）`);
    const [, serial, power] = match;
    if (seen.has(serial)) throw new Error(`复测板件重复：${serial}`);
    seen.add(serial);
    readings.push({serial, power_cw: Number(power)});
  }
  if (!readings.length) throw new Error("至少填写一条复测读数");
  return readings;
}

function correctionForm(config, selected, onDone) {
  const form = el("form", {class: "correction-form"},
    field("按板件编号补录复测（每行 编号=厘瓦）", el("textarea", {
      name: "readings", rows: 4, spellcheck: "false", placeholder: "D3-0109=42000\nD3-0119=42050",
    }), "只重建这些编号所在页，未变页不重读功率；齐备后一次性切换。"),
    field("更正说明", el("input", {name: "change_note", maxlength: 320, placeholder: "可选"})),
    el("label", {class: "field"}, el("span", {}, "中断演练"),
      el("input", {name: "simulate_abort", type: "checkbox"}),
      el("small", {}, "勾选后新版页段不生效，验证旧版仍可接单")),
    el("button", {type: "submit"}, "提交更正并切换版本"), errorText());
  submit(form, async values => {
    const readings = parseCorrection(values.get("readings"));
    const version = await api.correct(selected.id, {
      readings, change_note: values.get("change_note") || "",
      simulate_abort: values.get("simulate_abort") === "on",
    });
    onDone(version);
  });
  return form;
}

function versionTable(versions) {
  return table(
    ["版本", "状态", "重建页", "复用页", "重读读数", "说明", "操作人", "时间"],
    versions.map(v => [
      `v${v.version_no}`, status(v.status), v.rebuilt_pages, v.reused_pages, v.reread_values,
      v.change_note || "—", v.created_by, time(v.activated_at ?? v.created_at),
    ]),
    "读数版本轨迹",
  );
}

function registration(config, selected) {
  const data = selected ? JSON.stringify(selected.payload, null, 2) : "{}";
  const form = el("form", {id: "case-form"},
    field("名称", el("input", {name: "title", required: true, minlength: 2, maxlength: 120,
      value: selected ? `${selected.title} · 复演` : ""})),
    field("说明", el("input", {name: "description", maxlength: 420, value: selected?.description || ""})),
    field(config.parameter_label, el("input", {
      name: "page_size", type: "number", min: 1, max: config.max_parameter, required: true,
      value: config.default_parameter,
    }), "登记后固定，保证同一板件编号在各版本中落在同一页号。"),
    field(`${config.unit}内容`, el("textarea", {name: "payload", spellcheck: "false", required: true, rows: 12}, data),
      selected ? "可以保留演示数据，也可以编辑 JSON 后保存。" : "输入业务 JSON。"),
    errorText(), el("button", {type: "submit"}, "保存资料"));
  submit(form, async values => {
    let payload;
    try {
      payload = JSON.parse(values.get("payload"));
    } catch {
      throw new Error("资料 JSON 无法解析，请检查括号、引号和逗号");
    }
    const entry = await api.createCase({
      title: values.get("title"), description: values.get("description"), payload,
      page_size: Number(values.get("page_size")),
    });
    navigate(`cases/${entry.id}`);
  });
  return form;
}

export async function casesPage(config, user, route) {
  const cases = await api.cases();
  const selected = cases.find(item => item.id === route.id) ?? cases.at(-1);
  const operator = user.role === "operator";
  const versions = selected ? await api.versions(selected.id) : [];

  const versionSection = section("读数版本轨迹", versions.length ? versionTable(versions) :
    empty("登记后即生成 v1 页段版本。"));
  const correction = operator && selected
    ? section("按板件编号补录复测功率", correctionForm(config, selected, () => navigate(`cases/${selected.id}`)))
    : null;

  return el("div", {class: "page"},
    pageHeading(config.modules[1], `${config.unit} · ${config.fixture}`),
    section("资料目录", cases.length ? table(["编号", "名称", "页容量", "说明", "操作"], cases.map(item =>
      [item.code, item.title, item.page_size, item.description,
        el("a", {href: `#cases/${item.id}`, "aria-current": item.id === selected?.id ? "true" : null}, "选用")]),
      "资料目录") : empty("这里还没有资料。")),
    el("div", {class: "split case-split"},
      section(selected ? selected.title : "等待选择资料", selected ?
        [el("p", {}, selected.description),
          operator ? queueForm(config, selected) : empty("当前账号可查看此资料。"),
          versionSection,
          el("details", {}, el("summary", {}, "查看业务数据"), el("pre", {}, JSON.stringify(selected.payload, null, 2)))] :
        empty("先登记一条资料，再提交执行。")),
      operator ? section("登记新资料", registration(config, selected)) : null),
    correction);
}
