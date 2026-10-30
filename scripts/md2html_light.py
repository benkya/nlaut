#!/usr/bin/env python3
"""MD → 浅色主题 HTML（GitHub 风格），用于测试报告。
用户偏好：浅色系（白底#ffffff + 深色文字 #24292f），不展示版本号。
"""
from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def inline(s: str) -> str:
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    return s


def md_to_html(text: str) -> str:
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    tbl: list[str] = []
    n = len(lines)

    def flush_table() -> None:
        nonlocal tbl
        if not tbl:
            return
        head = [c.strip() for c in tbl[0].strip().strip("|").split("|")]
        thead = "<thead><tr>" + "".join(f"<th>{inline(h)}</th>" for h in head) + "</tr></thead>"
        tbody = "<tbody>"
        for row in tbl[1:]:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            tbody += "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells) + "</tr>"
        tbody += "</tbody>"
        out.append(f'<div class="tw"><table>{thead}{tbody}</table></div>')
        tbl = []

    while i < n:
        line = lines[i]

        if line.strip().startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                out.append("<pre><code>")
                in_code = True
            i += 1
            continue

        if in_code:
            out.append(esc(line))
            i += 1
            continue

        if line.strip().startswith("|"):
            if re.match(r"^\|[\s\-|:.]+\|$", line.strip()):
                i += 1
                continue
            tbl.append(line)
            i += 1
            continue
        flush_table()

        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            lv = len(m.group(1))
            out.append(f"<h{lv}>{inline(m.group(2).strip())}</h{lv}>")
            i += 1
            continue

        if re.match(r"^---+$", line.strip()):
            out.append("<hr>")
            i += 1
            continue

        if line.strip().startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^>\s*", "", lines[i].strip()))
                i += 1
            inner = "<br>".join(inline(x) for x in buf if x.strip())
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        if re.match(r"^[-*]\s+", line):
            items = []
            while i < n and re.match(r"^[-*]\s+", lines[i]):
                items.append(re.sub(r"^[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>" + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            continue

        if not line.strip():
            i += 1
            continue

        out.append(f"<p>{inline(line)}</p>")
        i += 1

    flush_table()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


CSS = """
:root{
  --fg:#24292f; --muted:#57606a; --bd:#d0d7de; --bg:#ffffff;
  --accent:#0969da; --code-bg:#f6f8fa; --th-bg:#f6f8fa;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.75;color:var(--fg);background:var(--bg);}
.wrap{max-width:1080px;margin:0 auto;padding:32px 28px 80px}
h1{font-size:26px;font-weight:650;padding:22px 24px;margin-bottom:26px;
  background:#f6f8fa;border:1px solid var(--bd);border-radius:10px;line-height:1.45}
h2{font-size:20px;font-weight:650;margin:38px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--bd)}
h3{font-size:16px;font-weight:650;margin:24px 0 10px;color:#111}
p{margin:11px 0}
ul{margin:11px 0 11px 24px}
li{margin:5px 0}
strong{font-weight:650;color:#111}
em{color:var(--muted)}
hr{border:none;border-top:1px solid var(--bd);margin:28px 0}
blockquote{background:#fff8c5;border-left:4px solid #d4a72c;padding:12px 18px;margin:14px 0;border-radius:0 6px 6px 0}
blockquote p{margin:4px 0}
code{background:var(--code-bg);padding:2px 6px;border-radius:5px;
  font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;font-size:13.5px}
pre{background:var(--code-bg);border:1px solid var(--bd);border-radius:8px;
  padding:14px 16px;margin:14px 0;overflow-x:auto;line-height:1.6}
pre code{background:none;padding:0;font-size:13px;white-space:pre}
.tw{overflow-x:auto;margin:14px 0}
table{border-collapse:collapse;width:100%;font-size:14px}
th{background:var(--th-bg);text-align:left;padding:9px 12px;border:1px solid var(--bd);font-weight:650;white-space:nowrap}
td{padding:9px 12px;border:1px solid var(--bd);vertical-align:top}
tbody tr:hover{background:var(--th-bg)}
footer{margin-top:44px;padding-top:18px;border-top:1px solid var(--bd);color:var(--muted);font-size:13px;text-align:center}
@media(max-width:700px){.wrap{padding:18px 14px 60px}h1{font-size:21px}}
"""


class V(HTMLParser):
    VOID: ClassVar[set[str]] = {"meta", "br", "hr", "img", "input", "link", "source"}

    def __init__(self):
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.errors.append(f"Unclosed: {self.stack[-1]}")
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"Stray close: {tag}")


def main() -> None:
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    title = sys.argv[3] if len(sys.argv) > 3 else src.stem
    md = src.read_text(encoding="utf-8")
    body = md_to_html(md)
    page = (
        "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"UTF-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f"<title>{esc(title)}</title>\n<style>{CSS}</style>\n</head>\n<body>\n"
        f'<div class="wrap">\n{body}\n'
        f"<footer>由 nlaut 模型上线测试流程生成 · {esc(title)}</footer>\n</div>\n</body>\n</html>\n"
    )
    dst.write_text(page, encoding="utf-8")
    v = V()
    v.feed(page)
    ok = not v.stack and not v.errors
    print(f"生成: {dst}")
    print(f"大小: {len(page)} 字节 | 模板残留 '{{{{': {page.count('{{')}")
    print(f"标签平衡: {'通过' if ok else 'FAIL ' + str(v.errors[:5]) + str(v.stack[:5])}")


if __name__ == "__main__":
    main()