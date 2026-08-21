import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";
import JSZip from "jszip";

const [inputPath, outputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("inputPath와 outputPath가 필요합니다.");
const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();

function columnName(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    value -= 1;
    result = String.fromCharCode(65 + (value % 26)) + result;
    value = Math.floor(value / 26);
  }
  return result;
}

function widthFor(header) {
  if (header.includes("품명")) return 250;
  if (header.includes("수주번호 목록")) return 260;
  if (header === "신규분류요약") return 150;
  if (header.includes("코드")) return 160;
  if (header === "수주번호") return 115;
  if (header === "이니셜") return 82;
  if (header.includes("납기일")) return 105;
  if (["POWER", "CP", "AXIS", "ADD"].includes(header)) return 72;
  if (header.includes("수량") || ["사출", "분리", "하이드레이션", "접착", "누수규격"].includes(header)) return 92;
  return 105;
}

for (const [sheetIndex, data] of payload.sheets.entries()) {
  const sheet = workbook.worksheets.add(data.name);
  sheet.showGridLines = false;
  const columns = data.columns;
  const lastColumn = columnName(columns.length - 1);
  const visibleLastColumn = columnName(columns.length - data.hiddenColumns.length - 1);
  sheet.mergeCells(`A1:${visibleLastColumn}1`);
  sheet.getRange("A1").values = [[payload.title + " · " + data.name]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: "#0A7AFF",
    font: { bold: true, color: "#FFFFFF", size: 15 },
    verticalAlignment: "center",
  };
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeightPx = 34;
  sheet.mergeCells(`A2:${visibleLastColumn}2`);
  sheet.getRange("A2").values = [[payload.note]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: "#EEF5FF",
    font: { color: "#52677E", size: 10 },
    verticalAlignment: "center",
  };
  sheet.getRange(`A2:${lastColumn}2`).format.rowHeightPx = 25;
  sheet.getRangeByIndexes(3, 0, 1, columns.length).values = [columns];
  sheet.getRangeByIndexes(3, 0, 1, columns.length).format = {
    fill: "#E7EEF7",
    font: { bold: true, color: "#173B63" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#D6E0EB" },
  };
  sheet.getRangeByIndexes(3, 0, 1, columns.length).format.rowHeightPx = 28;
  if (data.rows.length) {
    sheet.getRangeByIndexes(4, 0, data.rows.length, columns.length).values = data.rows.map((row) =>
      row.map((value, index) => columns[index].includes("납기일") && /^\d{4}-\d{2}-\d{2}$/.test(value)
        ? new Date(`${value}T00:00:00`)
        : value)
    );
    const body = sheet.getRangeByIndexes(4, 0, data.rows.length, columns.length);
    body.format = {
      font: { color: "#1A2433", size: 10 },
      verticalAlignment: "center",
      borders: {
        insideHorizontal: { style: "thin", color: "#E4E9EF" },
        bottom: { style: "thin", color: "#D6E0EB" },
      },
    };
    body.format.rowHeightPx = 22;
    const tableRange = `A4:${lastColumn}${data.rows.length + 4}`;
    const table = sheet.tables.add(tableRange, true, `ProcessExport${sheetIndex + 1}`);
    table.style = "TableStyleMedium2";
    table.showFilterButton = true;
  }
  columns.forEach((header, index) => {
    const col = columnName(index);
    const lastUsedRow = Math.max(4, data.rows.length + 4);
    sheet.getRange(`${col}1:${col}${lastUsedRow}`).format.columnWidthPx = widthFor(header);
    if (header.includes("납기일") && data.rows.length) {
      sheet.getRange(`${col}5:${col}${data.rows.length + 4}`).format.numberFormat = "yyyy-mm-dd";
    }
    if ((header.includes("수량") || ["사출", "분리", "하이드레이션", "접착", "누수규격", "수주 건수"].includes(header)) && data.rows.length) {
      sheet.getRange(`${col}5:${col}${data.rows.length + 4}`).format.numberFormat = "#,##0";
      sheet.getRange(`${col}5:${col}${data.rows.length + 4}`).format.horizontalAlignment = "right";
    }
    if (data.hiddenColumns.includes(header)) {
      sheet.getRange(`${col}1:${col}${lastUsedRow}`).format.columnWidth = 0;
    }
  });
  sheet.freezePanes.freezeRows(4);
}

if (previewDir) {
  await fs.mkdir(previewDir, { recursive: true });
  for (const data of payload.sheets) {
    const previewLastColumn = columnName(data.columns.length - data.hiddenColumns.length - 1);
    const previewLastRow = Math.min(data.rows.length + 4, 40);
    const preview = await workbook.render({
      sheetName: data.name,
      range: `A1:${previewLastColumn}${previewLastRow}`,
      scale: 1,
      format: "png",
    });
    await fs.writeFile(`${previewDir}/${data.name}.png`, new Uint8Array(await preview.arrayBuffer()));
  }
}
const output = await SpreadsheetFile.exportXlsx(workbook);
// artifact-tool는 0 너비를 기록하지만 hidden 플래그를 내보내지 않으므로,
// 생성된 XLSX의 해당 열만 실제 Excel 숨김 상태로 마무리한다.
const zip = await JSZip.loadAsync(output.data);
for (const path of Object.keys(zip.files).filter((name) => /^xl\/worksheets\/sheet\d+\.xml$/.test(name))) {
  const file = zip.file(path);
  if (!file) continue;
  const xml = await file.async("string");
  zip.file(path, xml.replace(/(<x:col\b[^>]*\bwidth="0"[^>]*\bhidden=")0("[^>]*\/>)/g, "$11$2"));
}
const finalized = await zip.generateAsync({ type: "uint8array", compression: "DEFLATE" });
await fs.writeFile(outputPath, finalized);
console.log(JSON.stringify({ outputPath, sheets: payload.sheets.map((sheet) => sheet.name) }));
