"""Extract local text without model services or source mutation."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    output = args.output.resolve()
    if not source.is_file() or source == output or output.exists():
        parser.error('Use an existing source file and a new output path')
    from markitdown import MarkItDown
    result = MarkItDown(enable_plugins=False).convert(str(source))
    if not result.text_content.strip():
        raise RuntimeError('No text extracted; scanned documents require OCR')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as target:
        target.write(result.text_content)
    print(json.dumps({'status': 'success', 'output': str(output),
        'characters': len(result.text_content), 'cloud_model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
