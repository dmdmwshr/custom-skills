#!/usr/bin/env python3
"""Validate with Python's standard XML parser; preserve source bytes except a leading declaration when embedding."""
import json
import re
import sys
import xml.etree.ElementTree as ET
import xml.parsers.expat as expat

class NoDoctype(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("DOCTYPE is not allowed")


def diagram_spans(text):
    """Expat identifies actual direct diagram elements; offsets preserve untouched source text."""
    raw = text.encode("utf-8")
    parser = expat.ParserCreate()
    stack, pages = [], []
    def tag_end(start):
        quote = None
        for i in range(start, len(raw)):
            value = raw[i]
            if quote is not None:
                if value == quote: quote = None
            elif value in (34, 39): quote = value
            elif value == 62: return i + 1
        raise ValueError("Incomplete XML token")
    def js_offset(byte_index):
        return len(raw[:byte_index].decode("utf-8").encode("utf-16-le")) // 2
    def start(name, attrs):
        if name == "diagram" and stack == ["mxfile"]:
            begin = parser.CurrentByteIndex
            opening_end = tag_end(begin)
            pages.append({"index": len(pages), "attrs": attrs, "byteStart": begin,
                          "openingEnd": opening_end, "selfClosing": raw[begin:opening_end].rstrip().endswith(b"/>")})
        stack.append(name)
    def end(name):
        if name == "diagram" and stack == ["mxfile", "diagram"]:
            page = pages[-1]
            closing_start = parser.CurrentByteIndex
            end_index = page["openingEnd"] if page["selfClosing"] else tag_end(closing_start)
            body_end = page["openingEnd"] if page["selfClosing"] else closing_start
            page.update(start=js_offset(page.pop("byteStart")), end=js_offset(end_index),
                        bodyStart=js_offset(page["openingEnd"]), bodyEnd=js_offset(body_end),
                        body=raw[page["openingEnd"]:body_end].decode("utf-8"))
            del page["openingEnd"], page["selfClosing"]
        stack.pop()
    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.Parse(raw, True)
    return pages

def main():
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    text = request["xml"]
    root = ET.fromstring(text, parser=ET.XMLParser(target=NoDoctype()))
    result = {"root": root.tag, "elements": sum(1 for _ in root.iter()),
              "hasDiagram": any(el.tag == "diagram" for el in root.iter())}
    if request.get("diagrams"):
        result["diagrams"] = diagram_spans(text)
    if request.get("stripDeclaration"):
        body = text.removeprefix("\ufeff")
        if re.match(r"^<\?xml\s", body):
            # The standard parser already validated the declaration and its position.
            body = body[body.index("?>") + 2:].lstrip()
        result["xml"] = body
    return result

if __name__ == "__main__":
    try:
        output = main()
    except (ET.ParseError, expat.ExpatError, ValueError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(output, ensure_ascii=False))
