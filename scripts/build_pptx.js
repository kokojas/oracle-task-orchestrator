#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

let ShapeType;

function parseArgs(argv) {
  const out = {};
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg.startsWith("--")) {
      out[arg.slice(2)] = argv[i + 1];
      i += 1;
    }
  }
  return out;
}

function safeText(value) {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(safeText).join("\n");
  return String(value);
}

function cleanHex(value, fallback) {
  const raw = safeText(value).replace(/[^0-9a-fA-F]/g, "").slice(0, 6);
  return raw.length === 6 ? raw.toUpperCase() : fallback;
}

function bullets(value) {
  if (!Array.isArray(value)) return [];
  return value.map((item) => safeText(item).trim()).filter(Boolean).slice(0, 6);
}

function addBackground(slide, color) {
  slide.background = { color };
}

function addFooter(slide, index, total, style) {
  slide.addText(`${index}/${total}`, {
    x: 12.35,
    y: 7.12,
    w: 0.6,
    h: 0.18,
    fontFace: "Aptos",
    fontSize: 7,
    color: style.mutedColor,
    align: "right",
    margin: 0,
  });
}

function addTitle(slide, text, style, y = 0.45, size = 28) {
  slide.addText(safeText(text), {
    x: 0.55,
    y,
    w: 11.9,
    h: 0.58,
    fontFace: "Aptos Display",
    fontSize: size,
    bold: true,
    color: style.textColor,
    breakLine: false,
    fit: "shrink",
    margin: 0,
  });
}

function addBullets(slide, items, x, y, w, h, style) {
  const lines = bullets(items);
  if (!lines.length) return;
  slide.addText(
    lines.map((text) => ({ text, options: { bullet: { type: "bullet" }, hanging: 3 } })),
    {
      x,
      y,
      w,
      h,
      fontFace: "Aptos",
      fontSize: 16,
      color: style.textColor,
      valign: "top",
      breakLine: false,
      fit: "shrink",
      margin: 0.08,
      paraSpaceAfterPt: 8,
    },
  );
}

function addClaim(slide, claim, style) {
  const text = safeText(claim).trim();
  if (!text) return;
  slide.addShape(ShapeType.roundRect, {
    x: 0.65,
    y: 1.2,
    w: 11.75,
    h: 0.58,
    rectRadius: 0.08,
    fill: { color: "FFFFFF", transparency: 4 },
    line: { color: style.accentColor, width: 1 },
  });
  slide.addText(text, {
    x: 0.88,
    y: 1.35,
    w: 11.25,
    h: 0.24,
    fontFace: "Aptos",
    fontSize: 12.5,
    color: style.primaryColor,
    bold: true,
    margin: 0,
    fit: "shrink",
  });
}

function findFirstImage(assetsDir) {
  if (!assetsDir || !fs.existsSync(assetsDir)) return null;
  const entries = fs.readdirSync(assetsDir)
    .filter((name) => /\.(png|jpe?g|webp)$/i.test(name))
    .map((name) => path.join(assetsDir, name));
  return entries[0] || null;
}

function addImageOrPrompt(slide, slideSpec, x, y, w, h, style, assetsDir) {
  const explicit = safeText(slideSpec.imagePath).trim();
  const imagePath = explicit && fs.existsSync(explicit) ? explicit : findFirstImage(assetsDir);
  if (imagePath) {
    slide.addImage({ path: imagePath, x, y, w, h });
    return;
  }
  slide.addShape(ShapeType.rect, {
    x,
    y,
    w,
    h,
    fill: { color: "E6EEF5" },
    line: { color: style.accentColor, width: 1 },
  });
  const prompt = safeText(slideSpec.imagePrompt).trim() || "Image placeholder";
  slide.addText(prompt, {
    x: x + 0.18,
    y: y + 0.18,
    w: w - 0.36,
    h: h - 0.36,
    fontFace: "Aptos",
    fontSize: 11,
    italic: true,
    color: style.primaryColor,
    valign: "mid",
    fit: "shrink",
    margin: 0,
  });
}

function addNotes(slide, notes) {
  const text = safeText(notes).trim();
  if (text && typeof slide.addNotes === "function") {
    slide.addNotes(text);
  }
}

async function main() {
  const args = parseArgs(process.argv);
  if (!args.spec || !args.out) {
    console.error("Usage: build_pptx.js --spec slide_spec.json --out deck.pptx [--assets-dir dir]");
    process.exit(2);
  }

  const spec = JSON.parse(fs.readFileSync(args.spec, "utf8"));
  const styleSpec = spec.style || {};
  const style = {
    primaryColor: cleanHex(styleSpec.primaryColor, "1F4E79"),
    accentColor: cleanHex(styleSpec.accentColor, "2CA6A4"),
    backgroundColor: cleanHex(styleSpec.backgroundColor, "F7F9FB"),
    textColor: cleanHex(styleSpec.textColor, "1B1F24"),
    mutedColor: "6D7782",
  };

  const pptx = new pptxgen();
  ShapeType = pptx.ShapeType;
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "Oracle Task Orchestrator";
  pptx.subject = safeText(spec.subtitle);
  pptx.title = safeText(spec.title) || "Oracle Presentation";
  pptx.company = "Oracle Task Orchestrator";
  pptx.theme = {
    headFontFace: "Aptos Display",
    bodyFontFace: "Aptos",
    lang: "uk-UA",
  };
  pptx.defineLayout({ name: "LAYOUT_WIDE", width: 13.333, height: 7.5 });

  const slides = Array.isArray(spec.slides) && spec.slides.length ? spec.slides : [{ layout: "title", title: spec.title }];
  slides.forEach((slideSpec, idx) => {
    const slide = pptx.addSlide();
    addBackground(slide, style.backgroundColor);
    const layout = safeText(slideSpec.layout || "content").toLowerCase();

    if (idx === 0 || layout === "title") {
      slide.addShape(ShapeType.rect, { x: 0, y: 0, w: 13.333, h: 7.5, fill: { color: style.primaryColor }, line: { transparency: 100 } });
      slide.addShape(ShapeType.rect, { x: 0, y: 6.8, w: 13.333, h: 0.7, fill: { color: style.accentColor }, line: { transparency: 100 } });
      slide.addText(safeText(slideSpec.title || spec.title), {
        x: 0.72,
        y: 2.15,
        w: 11.6,
        h: 1.05,
        fontFace: "Aptos Display",
        fontSize: 34,
        bold: true,
        color: "FFFFFF",
        fit: "shrink",
        margin: 0,
      });
      slide.addText(safeText(slideSpec.subtitle || spec.subtitle), {
        x: 0.76,
        y: 3.35,
        w: 10.8,
        h: 0.7,
        fontFace: "Aptos",
        fontSize: 16,
        color: "E8F3F3",
        fit: "shrink",
        margin: 0,
      });
    } else if (layout === "section") {
      slide.addShape(ShapeType.rect, { x: 0, y: 0, w: 0.22, h: 7.5, fill: { color: style.accentColor }, line: { transparency: 100 } });
      slide.addText(safeText(slideSpec.title), {
        x: 0.75,
        y: 2.45,
        w: 11.6,
        h: 0.9,
        fontFace: "Aptos Display",
        fontSize: 32,
        bold: true,
        color: style.textColor,
        fit: "shrink",
        margin: 0,
      });
      slide.addText(safeText(slideSpec.subtitle || slideSpec.claim), {
        x: 0.78,
        y: 3.42,
        w: 10.7,
        h: 0.55,
        fontFace: "Aptos",
        fontSize: 15,
        color: style.mutedColor,
        fit: "shrink",
        margin: 0,
      });
    } else if (layout === "two-column") {
      addTitle(slide, slideSpec.title, style);
      addClaim(slide, slideSpec.claim, style);
      slide.addShape(ShapeType.rect, { x: 0.68, y: 2.05, w: 5.85, h: 4.72, fill: { color: "FFFFFF" }, line: { color: "D9E2EA", width: 1 } });
      slide.addShape(ShapeType.rect, { x: 6.82, y: 2.05, w: 5.85, h: 4.72, fill: { color: "FFFFFF" }, line: { color: "D9E2EA", width: 1 } });
      addBullets(slide, slideSpec.left || slideSpec.bullets, 0.95, 2.45, 5.25, 3.95, style);
      addBullets(slide, slideSpec.right || [], 7.1, 2.45, 5.25, 3.95, style);
    } else if (layout === "image") {
      addTitle(slide, slideSpec.title, style);
      addClaim(slide, slideSpec.claim, style);
      addImageOrPrompt(slide, slideSpec, 0.72, 2.0, 7.15, 4.75, style, args["assets-dir"]);
      addBullets(slide, slideSpec.bullets, 8.25, 2.05, 4.45, 4.6, style);
    } else if (layout === "closing") {
      addTitle(slide, slideSpec.title || "Next steps", style, 1.0, 30);
      addClaim(slide, slideSpec.claim, style);
      addBullets(slide, slideSpec.bullets, 1.0, 2.25, 10.8, 3.7, style);
      slide.addShape(ShapeType.rect, { x: 0, y: 6.95, w: 13.333, h: 0.55, fill: { color: style.primaryColor }, line: { transparency: 100 } });
    } else {
      addTitle(slide, slideSpec.title, style);
      addClaim(slide, slideSpec.claim, style);
      addBullets(slide, slideSpec.bullets, 0.86, 2.05, 11.55, 4.8, style);
    }

    addFooter(slide, idx + 1, slides.length, style);
    addNotes(slide, slideSpec.speakerNotes || slideSpec.notes);
  });

  fs.mkdirSync(path.dirname(args.out), { recursive: true });
  await pptx.writeFile({ fileName: args.out });
  console.log(args.out);
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
