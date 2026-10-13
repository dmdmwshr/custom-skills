"""Convert a local PDF with the installed, offline Docling runtime."""
import argparse
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--first-page', type=int, default=1)
    parser.add_argument('--last-page', type=int, default=2147483647)
    args = parser.parse_args()
    source = args.input.resolve(strict=True)
    if not source.is_file() or source.suffix.lower() != '.pdf':
        parser.error('Expected a local PDF file')
    if not 1 <= args.first_page <= args.last_page:
        parser.error('Invalid page range')
    output = args.output.resolve()
    md = output / (source.stem + '.md')
    structured = output / (source.stem + '.json')
    if md.exists() or structured.exists():
        parser.error('Output already exists; choose another directory')
    runtime = Path.home() / '.local/share/codex-tools/docling/2.138.0'
    os.environ.update(HF_HOME=str(runtime / 'model-cache'), HF_HUB_OFFLINE='1',
                      OMP_NUM_THREADS='4', TOKENIZERS_PARALLELISM='false')
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions, AcceleratorOptions, AcceleratorDevice
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options = PdfPipelineOptions(do_ocr=True, do_table_structure=True,
        ocr_options=RapidOcrOptions(lang=['ch'], backend='onnxruntime'),
        accelerator_options=AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU))
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = converter.convert(source, page_range=(args.first_page, args.last_page))
    if result.status.value != 'success':
        raise RuntimeError(f'Conversion is not complete: {result.status.value}')
    output.mkdir(parents=True, exist_ok=True)
    content = result.document.export_to_markdown()
    if not content.strip():
        raise RuntimeError('No text extracted')
    with md.open('x', encoding='utf-8') as target:
        target.write(content)
    with structured.open('x', encoding='utf-8') as target:
        json.dump(result.document.export_to_dict(), target, ensure_ascii=False, indent=2)
    print(json.dumps({'status': 'success', 'markdown': str(md), 'structured': str(structured),
        'characters': len(content), 'tables': len(result.document.tables),
        'pages': sorted(result.document.pages), 'cloud_model_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
