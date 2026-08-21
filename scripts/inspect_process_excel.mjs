import fs from "node:fs/promises";
import { SpreadsheetFile } from "file:///C:/Users/%EC%8B%AC%EB%AF%BC%EC%8B%9D/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";

const [inputPath] = process.argv.slice(2);
const workbook = await SpreadsheetFile.importXlsx(await fs.readFile(inputPath));
const compact = await workbook.inspect({
  kind: "sheet,table",
  range: "간략히보기!A1:K8",
  maxChars: 8000,
  tableMaxRows: 8,
  tableMaxCols: 11,
});
const detail = await workbook.inspect({
  kind: "sheet,table",
  range: "납기별 상세!A1:N8",
  maxChars: 10000,
  tableMaxRows: 8,
  tableMaxCols: 14,
});
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  maxChars: 5000,
});
console.log("COMPACT\n" + compact.ndjson);
console.log("DETAIL\n" + detail.ndjson);
console.log("ERRORS\n" + errors.ndjson);
